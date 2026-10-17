from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from terminal import config
from terminal.auth import TokenStore
from terminal.errors import AuthRequired, QuotaExceeded, UploadError, UploadTimeout
from terminal.history import CONTAINER_CREATED, PUBLISHED, Entry
from terminal.meta import InstagramOptions, PostMeta
from terminal.platforms.instagram import InstagramUploader
from tests.conftest import fake_ctx

G = config.INSTAGRAM_GRAPH
SIGNED = "https://bucket.example.com/terminal/x.mp4?X-Amz-Signature=abc"


class FakeHosting:
    def __init__(self) -> None:
        self.put_calls: list[Path] = []
        self.deleted: list[str] = []

    def put(self, video: Path, progress: Any = None) -> str:
        self.put_calls.append(video)
        if progress:
            progress(video.stat().st_size)
        return "terminal/x.mp4"

    def url(self, key: str) -> str:
        return SIGNED

    def delete(self, key: str) -> None:
        self.deleted.append(key)

    @staticmethod
    def assert_reachable(url: str) -> None:
        return None


@pytest.fixture
def logged_in(store: TokenStore) -> TokenStore:
    now = time.time()
    store.save("instagram", {"access_token": "IGT", "user_id": "17841", "username": "me",
                             "refreshed_at": now, "expires_at": now + 50 * 86400})
    return store


def meta() -> PostMeta:
    return PostMeta(title="T", description="Hallo", hashtags=["x"],
                    platforms={"instagram": InstagramOptions()})  # type: ignore[arg-type]


def uploader(store: TokenStore, hosting: FakeHosting) -> InstagramUploader:
    return InstagramUploader(store, client=httpx.Client(), hosting_factory=lambda: hosting,
                             sleep=lambda s: None)  # type: ignore[arg-type,return-value]


@respx.mock
def test_full_reel_flow(clip: Path, logged_in: TokenStore) -> None:
    hosting = FakeHosting()
    create = respx.post(f"{G}/17841/media").mock(
        return_value=httpx.Response(200, json={"id": "C1"}))
    respx.get(f"{G}/C1").mock(side_effect=[
        httpx.Response(200, json={"status_code": "IN_PROGRESS"}),
        httpx.Response(200, json={"status_code": "FINISHED"})])
    publish = respx.post(f"{G}/17841/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "M1"}))
    respx.get(f"{G}/M1").mock(
        return_value=httpx.Response(200, json={"permalink": "https://instagram.com/reel/abc"}))

    ctx = fake_ctx()
    result = uploader(logged_in, hosting).upload(clip, meta(), ctx)

    form = create.calls[0].request.content.decode()
    assert "media_type=REELS" in form and "share_to_feed=true" in form
    assert "caption=Hallo" in form
    assert publish.calls[0].request.content.decode().startswith("creation_id=C1")
    assert hosting.put_calls[0].name.endswith("-faststart.mp4")  # Remux-Kopie, nicht Original
    assert hosting.deleted == ["terminal/x.mp4"]
    assert result.status == PUBLISHED and result.remote_id == "M1"
    assert result.link == "https://instagram.com/reel/abc"
    # Container-ID wird vor media_publish gesichert
    assert ctx.saved[1] == (CONTAINER_CREATED, {"remote_id": "C1",
                                                "extra": {"bucket_key": "terminal/x.mp4"}})


@respx.mock
def test_timeout_keeps_file(clip: Path, logged_in: TokenStore) -> None:
    hosting = FakeHosting()
    respx.post(f"{G}/17841/media").mock(return_value=httpx.Response(200, json={"id": "C1"}))
    respx.get(f"{G}/C1").mock(return_value=httpx.Response(200, json={
        "status_code": "IN_PROGRESS"}))
    with pytest.raises(UploadTimeout):
        uploader(logged_in, hosting).upload(clip, meta(), fake_ctx())
    assert hosting.deleted == []


@respx.mock
def test_resume_does_not_post_twice(clip: Path, logged_in: TokenStore) -> None:
    hosting = FakeHosting()
    create = respx.post(f"{G}/17841/media")
    publish = respx.post(f"{G}/17841/media_publish")
    respx.get(f"{G}/C0").mock(return_value=httpx.Response(200, json={
        "status_code": "PUBLISHED"}))
    prev = Entry(sha256="ab" * 32, platform="instagram", video=str(clip),
                 status=CONTAINER_CREATED, remote_id="C0")
    result = uploader(logged_in, hosting).upload(clip, meta(), fake_ctx(previous=prev))
    assert result.status == PUBLISHED
    assert not create.called and not publish.called and not hosting.put_calls


@respx.mock
def test_resume_finished_container_publishes(clip: Path, logged_in: TokenStore) -> None:
    hosting = FakeHosting()
    respx.get(f"{G}/C0").mock(return_value=httpx.Response(200, json={
        "status_code": "FINISHED"}))
    respx.post(f"{G}/17841/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "M9"}))
    respx.get(f"{G}/M9").mock(return_value=httpx.Response(200, json={}))
    prev = Entry(sha256="ab" * 32, platform="instagram", video=str(clip),
                 status=CONTAINER_CREATED, remote_id="C0", extra={"bucket_key": "k0"})
    result = uploader(logged_in, hosting).upload(clip, meta(), fake_ctx(previous=prev))
    assert result.remote_id == "M9" and hosting.deleted == ["k0"] and not hosting.put_calls


@respx.mock
def test_container_error(clip: Path, logged_in: TokenStore) -> None:
    respx.post(f"{G}/17841/media").mock(return_value=httpx.Response(200, json={"id": "C1"}))
    respx.get(f"{G}/C1").mock(return_value=httpx.Response(200, json={
        "status_code": "ERROR", "status": "Error: 2207026"}))
    with pytest.raises(UploadError, match="ERROR"):
        uploader(logged_in, FakeHosting()).upload(clip, meta(), fake_ctx())


@pytest.mark.parametrize(("error", "exc"), [
    ({"code": 190, "message": "Invalid OAuth access token"}, AuthRequired),
    ({"code": 9, "error_subcode": 2207042, "message": "limit"}, QuotaExceeded),
    ({"code": 100, "message": "Invalid parameter"}, UploadError),
])
@respx.mock
def test_graph_errors(clip: Path, logged_in: TokenStore, error: dict, exc: type) -> None:  # type: ignore[type-arg]
    respx.post(f"{G}/17841/media").mock(return_value=httpx.Response(400, json={"error": error}))
    with pytest.raises(exc):
        uploader(logged_in, FakeHosting()).upload(clip, meta(), fake_ctx())


@respx.mock
def test_token_refreshed_after_24h(clip: Path, store: TokenStore) -> None:
    old = time.time() - 2 * 86400
    store.save("instagram", {"access_token": "OLD", "user_id": "17841", "username": "me",
                             "refreshed_at": old, "expires_at": old + 60 * 86400})
    refresh = respx.get(f"{config.INSTAGRAM_TOKEN_BASE}/refresh_access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "NEW", "expires_in": 5184000}))
    respx.post(f"{G}/17841/media").mock(return_value=httpx.Response(400, json={
        "error": {"code": 100, "message": "stop"}}))
    with pytest.raises(UploadError):
        uploader(store, FakeHosting()).upload(clip, meta(), fake_ctx())
    assert refresh.called
    saved = store.load("instagram")
    assert saved and saved["access_token"] == "NEW"
