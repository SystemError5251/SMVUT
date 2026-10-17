"""YouTube Short über videos.insert (resumable Upload).

Ein hochkantes oder quadratisches Video bis 3 Minuten wird automatisch als Short
eingestuft – einen eigenen Shorts-Parameter gibt es nicht.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from terminal import config
from terminal.auth import TokenStore, youtube_credentials
from terminal.errors import AuthRequired, QuotaExceeded, UploadError
from terminal.history import PUBLISHED, UPLOADING
from terminal.meta import PostMeta, YouTubeOptions
from terminal.platforms.base import UploadContext, UploadResult
from terminal.validate import Issue, VideoInfo

QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}


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


def _reason(exc: Any) -> tuple[str, str]:
    try:
        error = json.loads(exc.content.decode("utf-8")).get("error", {})
        first = (error.get("errors") or [{}])[0]
        return str(first.get("reason", "")), str(error.get("message", ""))
    except (ValueError, AttributeError):
        return "", str(getattr(exc, "reason", "") or exc)


def _default_service(creds: Any) -> Any:
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


class YouTubeUploader:
    name = "youtube"

    def __init__(self, store: TokenStore,
                 service_factory: Callable[[Any], Any] = _default_service) -> None:
        self.store = store
        self.service_factory = service_factory

    def check(self, video: Path, meta: PostMeta, info: VideoInfo) -> list[Issue]:
        if not self.store.load("youtube"):
            return [Issue("error", self.name, "Nicht angemeldet – `terminal auth youtube`")]
        return []

    def upload(self, video: Path, meta: PostMeta, ctx: UploadContext) -> UploadResult:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        creds = youtube_credentials(self.store)
        yt = self.service_factory(creds)
        media = MediaFileUpload(str(video), mimetype="video/mp4",
                                chunksize=config.YOUTUBE_CHUNK_SIZE, resumable=True)
        request = yt.videos().insert(part="snippet,status", body=build_body(meta),
                                     media_body=media)
        ctx.save(UPLOADING)
        size = video.stat().st_size
        response = None
        try:
            while response is None:
                # next_chunk wiederholt 5xx und Netzwerkfehler selbst
                status, response = request.next_chunk(num_retries=5)
                if status is not None:
                    ctx.progress(int(status.resumable_progress), size)
        except HttpError as exc:
            reason, message = _reason(exc)
            if exc.resp.status == 401:
                raise AuthRequired(self.name, "YouTube-Anmeldung ungültig") from exc
            if reason in QUOTA_REASONS:
                raise QuotaExceeded(f"YouTube {reason}") from exc
            raise UploadError(f"YouTube {reason or exc.resp.status}: {message}") from exc
        ctx.progress(size, size)

        video_id = str(response["id"])
        opts = meta.platforms.youtube or YouTubeOptions()
        note = f"Sichtbarkeit: {opts.privacy}"
        if opts.publish_at is not None:
            note += f", geplant für {opts.publish_at:%d.%m.%Y %H:%M %Z}"
        return UploadResult(self.name, PUBLISHED, video_id,
                            f"https://youtube.com/shorts/{video_id}", note)
