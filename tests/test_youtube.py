from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from googleapiclient.errors import HttpError

from terminal.auth import TokenStore
from terminal.errors import QuotaExceeded
from terminal.meta import PostMeta, YouTubeOptions
from terminal.platforms.youtube import YouTubeUploader, build_body
from tests.conftest import fake_ctx


def meta(**yt: Any) -> PostMeta:
    return PostMeta(title="Titel", description="Text", hashtags=["a", "b"], ai_generated=True,
                    platforms={"youtube": YouTubeOptions(**yt)})  # type: ignore[arg-type]


def test_body() -> None:
    body = build_body(meta(privacy="unlisted", category_id="27"))
    assert body["snippet"] == {"title": "Titel", "description": "Text\n\n#a #b",
                               "tags": ["a", "b"], "categoryId": "27"}
    assert body["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False,
                              "containsSyntheticMedia": True}


def test_body_scheduled() -> None:
    when = datetime(2026, 10, 20, 18, tzinfo=UTC)
    body = build_body(meta(publish_at=when))
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2026-10-20T18:00:00+00:00"


class FakeRequest:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks

    def next_chunk(self, num_retries: int = 0) -> Any:
        item = self.chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeService:
    def __init__(self, request: FakeRequest) -> None:
        self.request = request
        self.inserted: dict[str, Any] = {}

    def videos(self) -> FakeService:
        return self

    def insert(self, **kwargs: Any) -> FakeRequest:
        self.inserted = kwargs
        return self.request


@pytest.fixture
def logged_in(store: TokenStore) -> TokenStore:
    expiry = (datetime.now(UTC) + timedelta(hours=5)).replace(tzinfo=None)
    store.save("youtube", {"token": "t", "refresh_token": "r", "client_id": "c",
                           "client_secret": "s", "expiry": expiry.isoformat() + "Z",
                           "token_uri": "https://oauth2.googleapis.com/token"})
    return store


def test_upload(clip: Path, logged_in: TokenStore) -> None:
    progress: list[tuple[int, int]] = []
    request = FakeRequest([(SimpleNamespace(resumable_progress=100), None),
                           (None, {"id": "VID"})])
    service = FakeService(request)
    ctx = fake_ctx()
    ctx.progress = lambda d, t: progress.append((d, t))
    result = YouTubeUploader(logged_in, lambda creds: service).upload(clip, meta(), ctx)
    assert result.remote_id == "VID"
    assert result.link == "https://youtube.com/shorts/VID"
    assert service.inserted["part"] == "snippet,status"
    assert progress[0][0] == 100 and progress[-1][0] == progress[-1][1]


def test_quota(clip: Path, logged_in: TokenStore) -> None:
    content = json.dumps({"error": {"message": "quota", "errors": [
        {"reason": "quotaExceeded"}]}}).encode()
    error = HttpError(SimpleNamespace(status=403, reason="Forbidden"), content)  # type: ignore[arg-type]
    service = FakeService(FakeRequest([error]))
    with pytest.raises(QuotaExceeded):
        YouTubeUploader(logged_in, lambda creds: service).upload(clip, meta(), fake_ctx())
