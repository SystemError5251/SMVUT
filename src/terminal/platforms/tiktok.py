"""TikTok nur als Entwurf: Video landet im Posteingang, veröffentlicht wird in der App.

Die Entwurfs-API (inbox/video/init) überträgt nur das Video, keinen Text. Caption und
Hashtags gibt Terminal deshalb am Ende zum Kopieren aus.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from terminal import config
from terminal.auth import TokenStore, tiktok_token
from terminal.errors import (
    AuthRequired,
    QuotaExceeded,
    TransientApiError,
    UploadError,
    UploadTimeout,
)
from terminal.history import DRAFT_SENT, PROCESSING, UPLOADING
from terminal.meta import PostMeta
from terminal.platforms.base import UploadContext, UploadResult
from terminal.retry import transient
from terminal.validate import Issue, VideoInfo

QUOTA_CODES = {"spam_risk_too_many_posts", "spam_risk_too_many_pending_share",
               "rate_limit_exceeded", "reached_active_user_cap"}
AUTH_CODES = {"scope_not_authorized", "scope_permission_missed", "auth_removed"}
TRANSIENT_CODES = {"internal_error"}
INBOX_STATES = {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}
PENDING_STATES = {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"}


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) – Restbytes wandern in den letzten Chunk.

    Unter 5 MB wird die Datei als ein Stück gesendet; sonst 10-MB-Chunks, der letzte
    darf bis 128 MB groß werden.
    """
    if size <= 0:
        raise UploadError("Videodatei ist leer")
    chunk = min(size, config.TIKTOK_CHUNK_SIZE)
    return chunk, size // chunk


def chunk_ranges(size: int) -> list[tuple[int, int]]:
    chunk, count = chunk_plan(size)
    return [(i * chunk, size - 1 if i == count - 1 else (i + 1) * chunk - 1)
            for i in range(count)]


class _AccessTokenInvalid(Exception):
    pass


class TikTokUploader:
    name = "tiktok"

    def __init__(self, store: TokenStore, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = store
        self.client = client or httpx.Client(timeout=60)
        self.sleep = sleep
        self._token: str | None = None

    def check(self, video: Path, meta: PostMeta, info: VideoInfo) -> list[Issue]:
        if not self.store.load("tiktok"):
            return [Issue("error", self.name, "Nicht angemeldet – `terminal auth tiktok`")]
        return []

    # --- API ---------------------------------------------------------------
    def _post_once(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert self._token is not None
        r = self.client.post(f"{config.TIKTOK_API}{path}", json=payload, headers={
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=UTF-8"})
        try:
            body = r.json()
        except ValueError:
            body = {}
        error = body.get("error", {}) if isinstance(body, dict) else {}
        code = error.get("code", "ok") if isinstance(error, dict) else "ok"
        if r.status_code == 401 or code == "access_token_invalid":
            raise _AccessTokenInvalid
        if r.status_code >= 500 or code in TRANSIENT_CODES:
            raise TransientApiError(f"TikTok {code}: {error.get('message', r.status_code)}")
        if code == "ok" and r.status_code == 200:
            data = body.get("data", {})
            return data if isinstance(data, dict) else {}
        message = f"TikTok {code}: {error.get('message') or f'HTTP {r.status_code}'}"
        if code in QUOTA_CODES or r.status_code == 429:
            raise QuotaExceeded(message)
        if code in AUTH_CODES:
            raise AuthRequired(self.name, message)
        raise UploadError(message)

    def api_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Bei HTTP 401 einmal erneuern, danach mit Hinweis auf `terminal auth` abbrechen."""
        if self._token is None:
            self._token = tiktok_token(self.store, self.client)
        try:
            return self._post_once(path, payload)
        except _AccessTokenInvalid:
            self._token = tiktok_token(self.store, self.client, force_refresh=True)
            try:
                return self._post_once(path, payload)
            except _AccessTokenInvalid:
                raise AuthRequired(self.name, "TikTok-Token ungültig") from None

    @transient
    def _status(self, publish_id: str) -> dict[str, Any]:
        return self.api_post("/post/publish/status/fetch/", {"publish_id": publish_id})

    @transient
    def _put_chunk(self, url: str, data: bytes, start: int, end: int, size: int) -> None:
        r = self.client.put(url, content=data, timeout=None, headers={
            "Content-Type": "video/mp4", "Content-Length": str(len(data)),
            "Content-Range": f"bytes {start}-{end}/{size}"})
        if r.status_code not in (200, 201, 206):
            r.raise_for_status()
            raise UploadError(f"TikTok-Chunk {start}-{end}: HTTP {r.status_code}")

    def _poll(self, publish_id: str) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for _ in range(config.TIKTOK_POLL_ATTEMPTS):
            state = self._status(publish_id)
            if state.get("status") not in PENDING_STATES:
                return state
            self.sleep(config.TIKTOK_POLL_INTERVAL)
        return state

    # --- Ablauf ------------------------------------------------------------
    def upload(self, video: Path, meta: PostMeta, ctx: UploadContext) -> UploadResult:
        publish_id: str | None = None
        prev = ctx.previous
        if prev and prev.status == PROCESSING and prev.remote_id:
            state = self._status(prev.remote_id).get("status")
            if state in INBOX_STATES or state in PENDING_STATES:
                ctx.log(f"Setze Entwurf {prev.remote_id} aus dem letzten Lauf fort")
                publish_id = prev.remote_id

        if publish_id is None:
            publish_id = self._send(video, ctx)

        state = self._poll(publish_id)
        status = state.get("status")
        if status in PENDING_STATES:
            raise UploadTimeout("TikTok verarbeitet noch – `terminal post` später erneut "
                                "ausführen, dann wird fortgesetzt")
        if status not in INBOX_STATES:
            raise UploadError(f"TikTok {status}: {state.get('fail_reason', 'unbekannt')}")

        lines = ["Öffne die TikTok-App → Benachrichtigung „Entwurf“ → Caption einfügen:",
                 "", meta.caption_for(self.name)]
        if meta.ai_generated:
            lines += ["", "Hinweis: In der App das Label „KI-generiert“ setzen."]
        return UploadResult(self.name, DRAFT_SENT, publish_id, None, "\n".join(lines))

    def _send(self, video: Path, ctx: UploadContext) -> str:
        size = video.stat().st_size
        chunk, count = chunk_plan(size)
        ctx.save(UPLOADING)
        # inbox/video/init nicht automatisch wiederholen: jeder Aufruf zählt zum Limit
        # (6 pro Minute); ein zweiter Aufruf erzeugt höchstens einen zweiten Entwurf
        init = self.api_post("/post/publish/inbox/video/init/", {"source_info": {
            "source": "FILE_UPLOAD", "video_size": size,
            "chunk_size": chunk, "total_chunk_count": count}})
        publish_id, upload_url = str(init["publish_id"]), str(init["upload_url"])

        with video.open("rb") as f:  # Chunks strikt nacheinander senden
            for start, end in chunk_ranges(size):
                f.seek(start)
                self._put_chunk(upload_url, f.read(end - start + 1), start, end, size)
                ctx.progress(end + 1, size)
        ctx.save(PROCESSING, remote_id=publish_id)
        return publish_id
