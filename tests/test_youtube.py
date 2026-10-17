from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from terminal import config
from terminal.auth import TokenStore
from terminal.errors import AuthRequired, QuotaExceeded, UploadError
from terminal.meta import Platforms, PostMeta, YouTubeOptions
from terminal.platforms.youtube import YouTubeUploader, build_body
from tests.conftest import fake_ctx

SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=abc"
CHUNK = 40_000


def meta(**yt: Any) -> PostMeta:
    return PostMeta(title="Titel", description="Text", hashtags=["a", "b"], ai_generated=True,
                    platforms=Platforms(youtube=YouTubeOptions(**yt)))


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


@pytest.fixture
def logged_in(store: TokenStore, monkeypatch: pytest.MonkeyPatch) -> TokenStore:
    expiry = (datetime.now(UTC) + timedelta(hours=5)).replace(tzinfo=None)
    store.save("youtube", {"token": "t", "refresh_token": "r", "client_id": "c",
                           "client_secret": "s", "expiry": expiry.isoformat() + "Z",
                           "token_uri": "https://oauth2.googleapis.com/token"})
    monkeypatch.setattr(config, "YOUTUBE_CHUNK_SIZE", CHUNK)
    return store


def uploader(store: TokenStore) -> YouTubeUploader:
    return YouTubeUploader(store, client=httpx.Client(), sleep=lambda s: None)


def start_route() -> respx.Route:
    return respx.post(config.YOUTUBE_UPLOAD_URL).mock(
        return_value=httpx.Response(200, headers={"Location": SESSION}))


def ranges(route: respx.Route) -> list[str]:
    return [c.request.headers["content-range"] for c in route.calls]


@respx.mock
def test_resumable_upload_in_chunks(clip: Path, logged_in: TokenStore) -> None:
    size = clip.stat().st_size
    assert size > 2 * CHUNK
    start = start_route()

    def answer(request: httpx.Request) -> httpx.Response:
        first, last = request.headers["content-range"][6:].split("/")[0].split("-")
        if int(last) + 1 < size:
            return httpx.Response(308, headers={"Range": f"bytes=0-{last}"})
        return httpx.Response(200, json={"id": "VID"})

    put = respx.put(SESSION).mock(side_effect=answer)
    progress: list[tuple[int, int]] = []
    ctx = fake_ctx()
    ctx.progress = lambda d, t: progress.append((d, t))
    result = uploader(logged_in).upload(clip, meta(), ctx)

    init = start.calls[0].request
    assert init.url.params["uploadType"] == "resumable"
    assert init.url.params["part"] == "snippet,status"
    assert init.headers["authorization"] == "Bearer t"
    assert init.headers["x-upload-content-length"] == str(size)
    assert json.loads(init.content) == build_body(meta())
    assert ranges(put)[0] == f"bytes 0-{CHUNK - 1}/{size}"
    assert b"".join(c.request.content for c in put.calls) == clip.read_bytes()
    assert result.remote_id == "VID" and result.link == "https://youtube.com/shorts/VID"
    assert progress[-1] == (size, size)


@respx.mock
def test_resumes_after_interruption(clip: Path, logged_in: TokenStore) -> None:
    size = clip.stat().st_size
    start_route()
    put = respx.put(SESSION).mock(side_effect=[
        httpx.Response(308, headers={"Range": f"bytes=0-{CHUNK - 1}"}),
        httpx.ConnectError("Netz weg"),                      # zweiter Chunk bricht ab
        httpx.Response(308, headers={"Range": f"bytes=0-{CHUNK + 999}"}),  # Stand: 1000 mehr
        httpx.Response(200, json={"id": "VID"}),
    ])
    result = uploader(logged_in).upload(clip, meta(), fake_ctx())
    assert result.remote_id == "VID"
    assert ranges(put) == [f"bytes 0-{CHUNK - 1}/{size}",
                           f"bytes {CHUNK}-{2 * CHUNK - 1}/{size}",
                           f"bytes */{size}",                       # Statusabfrage
                           f"bytes {CHUNK + 1000}-{min(2 * CHUNK + 999, size - 1)}/{size}"]


@respx.mock
def test_quota(clip: Path, logged_in: TokenStore) -> None:
    respx.post(config.YOUTUBE_UPLOAD_URL).mock(return_value=httpx.Response(403, json={
        "error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}}))
    with pytest.raises(QuotaExceeded):
        uploader(logged_in).upload(clip, meta(), fake_ctx())


@respx.mock
def test_unauthorized(clip: Path, logged_in: TokenStore) -> None:
    respx.post(config.YOUTUBE_UPLOAD_URL).mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(AuthRequired):
        uploader(logged_in).upload(clip, meta(), fake_ctx())


@respx.mock
def test_gives_up_after_repeated_failures(clip: Path, logged_in: TokenStore) -> None:
    start_route()
    respx.put(SESSION).mock(return_value=httpx.Response(503))
    with pytest.raises(UploadError, match="nach 5 Versuchen"):
        uploader(logged_in).upload(clip, meta(), fake_ctx())
