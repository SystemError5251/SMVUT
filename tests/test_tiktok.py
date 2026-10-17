from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from terminal import config
from terminal.auth import TokenStore
from terminal.errors import QuotaExceeded, UploadError
from terminal.history import DRAFT_SENT, Entry
from terminal.meta import Platforms, PostMeta, TikTokOptions
from terminal.platforms.tiktok import TikTokUploader, chunk_plan, chunk_ranges
from tests.conftest import fake_ctx

API = config.TIKTOK_API
UPLOAD_URL = "https://open-upload.tiktokapis.com/video/?upload_id=1"


@pytest.mark.parametrize(("size", "chunk", "count"), [
    (3 * config.MB, 3 * config.MB, 1),          # unter 5 MB: ein Stück
    (7 * config.MB, 7 * config.MB, 1),          # unter 10 MB: ein Stück
    (50_000_123, 10 * config.MB, 4),            # Rest landet im letzten Chunk
])
def test_chunk_plan(size: int, chunk: int, count: int) -> None:
    assert chunk_plan(size) == (chunk, count)
    ranges = chunk_ranges(size)
    assert len(ranges) == count
    assert ranges[0][0] == 0 and ranges[-1][1] == size - 1
    assert all(b[0] == a[1] + 1 for a, b in zip(ranges, ranges[1:], strict=False))
    assert ranges[-1][1] - ranges[-1][0] + 1 <= 128 * config.MB


def test_last_chunk_of_50_000_123() -> None:
    start, end = chunk_ranges(50_000_123)[-1]
    assert (start, end) == (3 * 10 * config.MB, 50_000_122)


def ok(data: dict) -> httpx.Response:  # type: ignore[type-arg]
    return httpx.Response(200, json={"data": data, "error": {"code": "ok", "message": ""}})


def err(code: str, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"data": {}, "error": {"code": code, "message": code}})


@pytest.fixture
def logged_in(store: TokenStore) -> TokenStore:
    store.save("tiktok", {"access_token": "AT", "expires_at": time.time() + 80000,
                          "refresh_token": "RT", "refresh_expires_at": time.time() + 9e6,
                          "open_id": "o", "scope": "user.info.basic,video.upload"})
    return store


def meta() -> PostMeta:
    return PostMeta(title="T", description="Hallo", hashtags=["x"], ai_generated=True,
                    platforms=Platforms(tiktok=TikTokOptions()))


@respx.mock
def test_upload_as_draft(clip: Path, logged_in: TokenStore) -> None:
    init = respx.post(f"{API}/post/publish/inbox/video/init/").mock(
        return_value=ok({"publish_id": "p1", "upload_url": UPLOAD_URL}))
    put = respx.put(UPLOAD_URL).mock(return_value=httpx.Response(201))
    respx.post(f"{API}/post/publish/status/fetch/").mock(side_effect=[
        ok({"status": "PROCESSING_UPLOAD"}), ok({"status": "SEND_TO_USER_INBOX"})])

    ctx = fake_ctx()
    result = TikTokUploader(logged_in, sleep=lambda s: None).upload(clip, meta(), ctx)

    size = clip.stat().st_size
    body = json.loads(init.calls[0].request.content)
    assert body == {"source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                    "chunk_size": size, "total_chunk_count": 1}}
    assert init.calls[0].request.headers["Authorization"] == "Bearer AT"
    assert put.calls[0].request.headers["Content-Range"] == f"bytes 0-{size - 1}/{size}"
    assert result.status == DRAFT_SENT and result.remote_id == "p1"
    assert "Hallo\n\n#x" in (result.note or "")
    assert "KI-generiert" in (result.note or "")
    assert [s for s, _ in ctx.saved] == ["uploading", "processing"]


@respx.mock
def test_401_refreshes_once(clip: Path, logged_in: TokenStore,
                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "ck")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "cs")
    token = respx.post(f"{API}/oauth/token/").mock(return_value=httpx.Response(200, json={
        "access_token": "AT2", "expires_in": 86400, "refresh_token": "RT2",
        "refresh_expires_in": 31536000, "open_id": "o", "scope": "video.upload"}))
    respx.post(f"{API}/post/publish/inbox/video/init/").mock(side_effect=[
        err("access_token_invalid", 401), ok({"publish_id": "p1", "upload_url": UPLOAD_URL})])
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(201))
    respx.post(f"{API}/post/publish/status/fetch/").mock(
        return_value=ok({"status": "SEND_TO_USER_INBOX"}))

    TikTokUploader(logged_in, sleep=lambda s: None).upload(clip, meta(), fake_ctx())
    assert token.call_count == 1
    form = token.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in form and "refresh_token=RT" in form
    saved = logged_in.load("tiktok")
    assert saved and saved["access_token"] == "AT2" and saved["refresh_token"] == "RT2"


@respx.mock
def test_spam_limit_is_quota(clip: Path, logged_in: TokenStore) -> None:
    respx.post(f"{API}/post/publish/inbox/video/init/").mock(
        return_value=err("spam_risk_too_many_posts", 403))
    with pytest.raises(QuotaExceeded, match="morgen"):
        TikTokUploader(logged_in, sleep=lambda s: None).upload(clip, meta(), fake_ctx())


@respx.mock
def test_failed_status(clip: Path, logged_in: TokenStore) -> None:
    respx.post(f"{API}/post/publish/inbox/video/init/").mock(
        return_value=ok({"publish_id": "p1", "upload_url": UPLOAD_URL}))
    respx.put(UPLOAD_URL).mock(return_value=httpx.Response(201))
    respx.post(f"{API}/post/publish/status/fetch/").mock(
        return_value=ok({"status": "FAILED", "fail_reason": "file_format_check_failed"}))
    with pytest.raises(UploadError, match="file_format_check_failed"):
        TikTokUploader(logged_in, sleep=lambda s: None).upload(clip, meta(), fake_ctx())


@respx.mock
def test_resumes_processing_entry(clip: Path, logged_in: TokenStore) -> None:
    init = respx.post(f"{API}/post/publish/inbox/video/init/")
    respx.post(f"{API}/post/publish/status/fetch/").mock(
        return_value=ok({"status": "SEND_TO_USER_INBOX"}))
    prev = Entry(sha256="ab" * 32, platform="tiktok", video=str(clip), status="processing",
                 remote_id="p0")
    result = TikTokUploader(logged_in, sleep=lambda s: None).upload(
        clip, meta(), fake_ctx(previous=prev))
    assert result.remote_id == "p0" and not init.called
