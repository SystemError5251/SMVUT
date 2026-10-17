"""YouTube Short über videos.insert (resumable Upload).

Ein hochkantes oder quadratisches Video bis 3 Minuten wird automatisch als Short
eingestuft – einen eigenen Shorts-Parameter gibt es nicht.

Direkt per httpx nach dem dokumentierten Resumable-Upload-Protokoll statt über
google-api-python-client (das allein ~110 MB Beschreibungen aller Google-APIs mitbringt):
Sitzung starten → Datei in Stücken senden → bei Abbruch Stand abfragen und fortsetzen.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from terminal import config
from terminal.auth import TokenStore, youtube_credentials
from terminal.errors import AuthRequired, QuotaExceeded, UploadError
from terminal.history import PUBLISHED, UPLOADING
from terminal.meta import PostMeta, YouTubeOptions
from terminal.platforms.base import UploadContext, UploadResult
from terminal.retry import TRANSIENT_STATUS, transient
from terminal.validate import Issue, VideoInfo

QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded",
                 "uploadLimitExceeded"}
MAX_FAILURES = 5


def build_body(meta: PostMeta) -> dict[str, Any]:
    opts = meta.platforms.youtube or YouTubeOptions()
    status: dict[str, Any] = {
        "privacyStatus": opts.privacy,
        "selfDeclaredMadeForKids": opts.made_for_kids,
        "containsSyntheticMedia": meta.ai_generated,
    }
    if opts.publish_at is not None:
        status["publishAt"] = opts.publish_at.isoformat()
    return {
        "snippet": {
            "title": meta.title,
            "description": meta.caption_for("youtube"),
            "tags": meta.hashtags,
            "categoryId": opts.category_id,
        },
        "status": status,
    }


def raise_for_youtube(r: httpx.Response) -> None:
    """Ordnet API-Fehler den Fehlerklassen zu; 5xx/429 werden zum Wiederholen weitergereicht."""
    if r.status_code < 400:
        return
    if r.status_code in TRANSIENT_STATUS:
        r.raise_for_status()
    try:
        error = r.json().get("error", {})
    except ValueError:
        error = {}
    first = (error.get("errors") or [{}])[0] if isinstance(error, dict) else {}
    reason = str(first.get("reason", ""))
    message = str(error.get("message", "")) if isinstance(error, dict) else ""
    if r.status_code == 401:
        raise AuthRequired("youtube", "YouTube-Anmeldung ungültig")
    if reason in QUOTA_REASONS:
        raise QuotaExceeded(f"YouTube {reason}")
    raise UploadError(f"YouTube {reason or r.status_code}: {message or r.text[:200]}")


def _next_offset(r: httpx.Response) -> int:
    """308-Antwort: „Range: bytes=0-N“ heißt, N+1 Bytes sind angekommen."""
    match = re.match(r"bytes=0-(\d+)", r.headers.get("range", ""))
    return int(match.group(1)) + 1 if match else 0


class YouTubeUploader:
    name = "youtube"

    def __init__(self, store: TokenStore, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.store = store
        self.client = client or httpx.Client(timeout=httpx.Timeout(60, write=300))
        self.sleep = sleep

    def check(self, video: Path, meta: PostMeta, info: VideoInfo) -> list[Issue]:
        if not self.store.load("youtube"):
            return [Issue("error", self.name, "Nicht angemeldet – `smvut auth youtube`")]
        return []

    @transient
    def _start(self, token: str, meta: PostMeta, size: int) -> str:
        r = self.client.post(
            config.YOUTUBE_UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            json=build_body(meta),
            headers={"Authorization": f"Bearer {token}",
                     "X-Upload-Content-Type": "video/*",
                     "X-Upload-Content-Length": str(size)})
        raise_for_youtube(r)
        session = r.headers.get("location")
        if not session:
            raise UploadError("YouTube hat keine Upload-Adresse geliefert")
        return str(session)

    def _send(self, session: str, video: Path, size: int, token: str,
              progress: Callable[[int, int], None]) -> dict[str, Any]:
        auth = {"Authorization": f"Bearer {token}"}
        offset, failures = 0, 0
        with video.open("rb") as f:
            while True:
                try:
                    if failures:
                        # Nach einem Abbruch: fragen, wie viel angekommen ist
                        r = self.client.put(session, headers={
                            **auth, "Content-Range": f"bytes */{size}"})
                    else:
                        f.seek(offset)
                        chunk = f.read(config.YOUTUBE_CHUNK_SIZE)
                        r = self.client.put(session, content=chunk, headers={
                            **auth,
                            "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{size}"})
                except httpx.TransportError:
                    r = None
                if r is not None and r.status_code in (200, 201):
                    progress(size, size)
                    result: dict[str, Any] = r.json()
                    return result
                if r is not None and r.status_code == 308:
                    offset, failures = _next_offset(r), 0
                    progress(offset, size)
                    continue
                if r is not None and r.status_code not in TRANSIENT_STATUS:
                    raise_for_youtube(r)
                failures += 1
                if failures > MAX_FAILURES:
                    raise UploadError(f"YouTube-Upload nach {MAX_FAILURES} Versuchen "
                                      "abgebrochen – Verbindung prüfen")
                self.sleep(min(2 ** failures, 60))

    def upload(self, video: Path, meta: PostMeta, ctx: UploadContext) -> UploadResult:
        token = str(youtube_credentials(self.store).token)
        size = video.stat().st_size
        ctx.save(UPLOADING)
        session = self._start(token, meta, size)
        response = self._send(session, video, size, token, ctx.progress)

        video_id = str(response["id"])
        opts = meta.platforms.youtube or YouTubeOptions()
        note = f"Sichtbarkeit: {opts.privacy}"
        if opts.publish_at is not None:
            note += f", geplant für {opts.publish_at:%d.%m.%Y %H:%M %Z}"
        return UploadResult(self.name, PUBLISHED, video_id,
                            f"https://youtube.com/shorts/{video_id}", note)
