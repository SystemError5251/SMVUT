"""Instagram Reel über Instagram Login (graph.instagram.com) mit video_url.

Ablauf: Video in den Bucket → signierte URL → Container anlegen → status_code abfragen
→ Datei im Bucket löschen → media_publish. Die Container-ID wird vor dem Veröffentlichen
in der Historie gesichert, damit ein erneuter Lauf kein zweites Reel erzeugt.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from terminal import config
from terminal.auth import TokenStore, instagram_token
from terminal.errors import (
    AuthRequired,
    QuotaExceeded,
    TransientApiError,
    UploadError,
    UploadTimeout,
)
from terminal.history import CONTAINER_CREATED, PUBLISHED, UPLOADING
from terminal.hosting import Hosting
from terminal.meta import InstagramOptions, PostMeta
from terminal.platforms.base import UploadContext, UploadResult
from terminal.retry import transient
from terminal.validate import Issue, VideoInfo, faststart_copy

RATE_LIMIT_CODES = {4, 17, 32, 613}
PUBLISH_LIMIT_SUBCODE = 2207042
DONE_STATES = {"FINISHED", "ERROR", "EXPIRED", "PUBLISHED"}


def raise_for_graph(r: httpx.Response) -> dict[str, Any]:
    """Ordnet Graph-API-Fehler den Fehlerklassen aus dem Konzept zu."""
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and "error" not in body:
        return body if isinstance(body, dict) else {}
    error = body.get("error", {}) if isinstance(body, dict) else {}
    code, subcode = error.get("code"), error.get("error_subcode")
    message = error.get("error_user_msg") or error.get("message") or f"HTTP {r.status_code}"
    if code == 190 or r.status_code == 401:
        raise AuthRequired("instagram", f"Instagram-Token ungültig: {message}")
    if subcode == PUBLISH_LIMIT_SUBCODE or code in RATE_LIMIT_CODES:
        raise QuotaExceeded(f"Instagram: {message}")
    if r.status_code >= 500 or error.get("is_transient"):
        raise TransientApiError(f"Instagram: {message}")
    raise UploadError(f"Instagram: {message}")


class InstagramUploader:
    name = "instagram"

    def __init__(self, store: TokenStore, client: httpx.Client | None = None,
                 hosting_factory: Callable[[], Hosting] = Hosting,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = store
        self.client = client or httpx.Client(timeout=60)
        self.hosting_factory = hosting_factory
        self.sleep = sleep

    def check(self, video: Path, meta: PostMeta, info: VideoInfo) -> list[Issue]:
        issues = []
        if not self.store.load("instagram"):
            issues.append(Issue("error", self.name,
                                "Nicht angemeldet – `terminal auth instagram --token …`"))
        missing = [n for n in ("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
                   if not config.env(n)]
        if missing:
            issues.append(Issue("error", self.name, "Bucket für die Zwischenablage fehlt "
                                                    f"in .env: {', '.join(missing)}"))
        return issues

    # --- API-Aufrufe -------------------------------------------------------
    @transient
    def _create_container(self, ig_id: str, token: str, url: str, meta: PostMeta) -> str:
        opts = meta.platforms.instagram or InstagramOptions()
        r = self.client.post(f"{config.INSTAGRAM_GRAPH}/{ig_id}/media", data={
            "media_type": "REELS", "video_url": url, "caption": meta.caption_for(self.name),
            "share_to_feed": "true" if opts.share_to_feed else "false", "access_token": token})
        return str(raise_for_graph(r)["id"])

    @transient
    def _container(self, container_id: str, token: str) -> dict[str, Any]:
        r = self.client.get(f"{config.INSTAGRAM_GRAPH}/{container_id}",
                            params={"fields": "status_code,status", "access_token": token})
        return raise_for_graph(r)

    def _publish(self, ig_id: str, token: str, container_id: str) -> str:
        # bewusst ohne automatische Wiederholung: ein zweites media_publish könnte doppelt posten
        r = self.client.post(f"{config.INSTAGRAM_GRAPH}/{ig_id}/media_publish",
                             data={"creation_id": container_id, "access_token": token})
        return str(raise_for_graph(r)["id"])

    @transient
    def _permalink(self, media_id: str, token: str) -> str | None:
        r = self.client.get(f"{config.INSTAGRAM_GRAPH}/{media_id}",
                            params={"fields": "permalink", "access_token": token})
        link = raise_for_graph(r).get("permalink")
        return str(link) if link else None

    def _poll(self, container_id: str, token: str) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for _ in range(config.INSTAGRAM_POLL_ATTEMPTS):
            self.sleep(config.INSTAGRAM_POLL_INTERVAL)
            state = self._container(container_id, token)
            if state.get("status_code") in DONE_STATES:
                break
        return state

    # --- Ablauf ------------------------------------------------------------
    def upload(self, video: Path, meta: PostMeta, ctx: UploadContext) -> UploadResult:
        token, ig_id = instagram_token(self.store, self.client)
        container_id: str | None = None
        bucket_key: str | None = None
        hosting: Hosting | None = None

        prev = ctx.previous
        if prev and prev.status == CONTAINER_CREATED and prev.remote_id:
            state = self._container(prev.remote_id, token).get("status_code")
            if state == "PUBLISHED":
                return UploadResult(self.name, PUBLISHED, prev.remote_id,
                                    note="war im letzten Lauf schon veröffentlicht")
            if state in ("FINISHED", "IN_PROGRESS"):
                ctx.log(f"Setze Container {prev.remote_id} aus dem letzten Lauf fort")
                container_id = prev.remote_id
                bucket_key = prev.extra.get("bucket_key")
            # ERROR/EXPIRED: neu anfangen

        if container_id is None:
            hosting = self.hosting_factory()
            source = faststart_copy(video, ctx.sha256)
            size, sent = source.stat().st_size, [0]

            def on_bytes(n: int) -> None:
                sent[0] += n
                ctx.progress(sent[0], size)

            ctx.save(UPLOADING)
            bucket_key = hosting.put(source, on_bytes)
            url = hosting.url(bucket_key)
            hosting.assert_reachable(url)
            container_id = self._create_container(ig_id, token, url, meta)
            ctx.save(CONTAINER_CREATED, remote_id=container_id,
                     extra={"bucket_key": bucket_key})
            ctx.log("Container angelegt, Meta lädt das Video …")

        state = self._poll(container_id, token)
        status_code = state.get("status_code")
        if status_code not in DONE_STATES:
            # Meta lädt noch: Datei liegen lassen, der Container bleibt 24 h gültig
            raise UploadTimeout("Instagram verarbeitet noch – `terminal post` später erneut "
                                "ausführen, dann wird fortgesetzt")

        if bucket_key:
            try:
                (hosting or self.hosting_factory()).delete(bucket_key)
            except Exception as exc:  # Lifecycle-Regel räumt notfalls auf
                ctx.log(f"Bucket-Datei nicht gelöscht ({type(exc).__name__}), "
                        "die Lifecycle-Regel räumt auf")

        if status_code == "PUBLISHED":
            return UploadResult(self.name, PUBLISHED, container_id)
        if status_code != "FINISHED":
            raise UploadError(f"Instagram-Container {status_code}: {state.get('status', '')}")

        try:
            media_id = self._publish(ig_id, token, container_id)
        except (httpx.TransportError, TransientApiError):
            # Unklar, ob veröffentlicht wurde: Status prüfen statt blind zu wiederholen
            if self._container(container_id, token).get("status_code") == "PUBLISHED":
                return UploadResult(self.name, PUBLISHED, container_id)
            raise
        return UploadResult(self.name, PUBLISHED, media_id, self._permalink(media_id, token))
