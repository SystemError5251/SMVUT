from __future__ import annotations

import hashlib
import stat
import time
from pathlib import Path

import httpx
import pytest
import respx

from terminal import config
from terminal.auth import TokenStore, instagram_login, pkce_pair, tiktok_token, token_overview
from terminal.errors import AuthRequired, redact
from terminal.history import CONTAINER_CREATED, PUBLISHED, History


def test_history_merges_fields(tmp_path: Path) -> None:
    h = History(tmp_path / "h.db")
    h.save("s", "instagram", "v.mp4", CONTAINER_CREATED, remote_id="C1", extra={"bucket_key": "k"})
    entry = h.save("s", "instagram", "v.mp4", PUBLISHED, link="https://x")
    assert entry.remote_id == "C1" and entry.extra == {"bucket_key": "k"} and entry.done
    assert h.get("s", "youtube") is None
    assert [e.platform for e in h.recent()] == ["instagram"]
    h.reset("s", "instagram")
    assert h.get("s", "instagram") is None


def test_pkce_is_hex_sha256() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert challenge == hashlib.sha256(verifier.encode()).hexdigest()


@respx.mock
def test_instagram_login_exchanges_and_reads_id(store: TokenStore,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", "sec")
    respx.get(f"{config.INSTAGRAM_TOKEN_BASE}/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "LONG", "expires_in": 5184000}))
    me = respx.get(f"{config.INSTAGRAM_GRAPH}/me").mock(
        return_value=httpx.Response(200, json={"user_id": "17841", "username": "me"}))
    with httpx.Client() as client:
        message = instagram_login(store, " SHORT ", client)
    assert "@me" in message
    assert "access_token=LONG" in str(me.calls[0].request.url)
    saved = store.load("instagram")
    assert saved and saved["access_token"] == "LONG" and saved["user_id"] == "17841"


@respx.mock
def test_instagram_login_invalid(store: TokenStore) -> None:
    respx.get(f"{config.INSTAGRAM_GRAPH}/me").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad token"}}))
    with httpx.Client() as client, pytest.raises(AuthRequired, match="bad token"):
        instagram_login(store, "x", client)


def test_tiktok_token_expired_refresh(store: TokenStore) -> None:
    store.save("tiktok", {"access_token": "a", "expires_at": 0, "refresh_token": "r",
                          "refresh_expires_at": time.time() - 1})
    with httpx.Client() as client, pytest.raises(AuthRequired, match="terminal auth tiktok"):
        tiktok_token(store, client)


def test_token_overview(store: TokenStore) -> None:
    now = time.time()
    store.save("instagram", {"access_token": "a", "user_id": "1", "username": "me",
                             "refreshed_at": now, "expires_at": now + 3 * 86400})
    infos = {i.platform: i for i in token_overview(store)}
    assert not infos["youtube"].logged_in
    assert infos["instagram"].logged_in and infos["instagram"].expiring_soon


def test_redact() -> None:
    text = ("GET https://graph.instagram.com/me?access_token=SECRET123&fields=x "
            "Authorization: Bearer abc.def X-Amz-Signature=deadbeef")
    out = redact(text)
    assert "SECRET123" not in out and "abc.def" not in out and "deadbeef" not in out
    assert "fields=x" in out


def test_file_store_is_private(store: TokenStore) -> None:
    assert store.backend == "file"
    store.save("tiktok", {"access_token": "a"})
    store.save("youtube", {"token": "b"})
    path = config.token_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert TokenStore().load("tiktok") == {"access_token": "a"}
    store.delete("tiktok")
    assert store.load("tiktok") is None and store.load("youtube") == {"token": "b"}


def test_keyring_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    keyring = pytest.importorskip("keyring")
    from keyring.backend import KeyringBackend

    class Memory(KeyringBackend):
        priority = 1  # type: ignore[assignment]
        data: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self.data.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self.data[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            self.data.pop((service, username), None)

    keyring.set_keyring(Memory())
    store = TokenStore("keyring")
    store.save("instagram", {"access_token": "x"})
    assert store.load("instagram") == {"access_token": "x"}
    assert not config.token_file().exists()


@pytest.mark.parametrize(("env", "backend"), [
    ({}, "keyring"),
    ({"TERMUX_VERSION": "0.119"}, "file"),
    ({"PREFIX": "/data/data/com.termux/files/usr"}, "file"),
    ({"PREFIX": "/data/data/com.termux/files/usr", "TERMINAL_TOKEN_STORE": "keyring"},
     "keyring"),
])
def test_token_backend_detection(monkeypatch: pytest.MonkeyPatch, env: dict[str, str],
                                 backend: str) -> None:
    for name in ("TERMUX_VERSION", "PREFIX", "TERMINAL_TOKEN_STORE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config.sys, "platform", "linux")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert config.token_backend() == backend


def test_termux_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setenv("TERMUX_VERSION", "0.119")
    monkeypatch.setattr(config.shutil, "which", lambda name: f"/usr/bin/{name}")
    config.setup_browser()
    assert config.os.environ["BROWSER"] == "termux-open-url"
    monkeypatch.delenv("BROWSER")


def test_home_is_private() -> None:
    assert stat.S_IMODE(config.home().stat().st_mode) == 0o700


def test_android_app_uses_file_store(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TERMUX_VERSION", "PREFIX", "TERMINAL_TOKEN_STORE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config.sys, "platform", "android")  # APK: kein Termux
    assert config.is_android() and not config.is_termux()
    assert config.token_backend() == "file"


def test_env_file_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_BUCKET", raising=False)
    config.write_env_file({"S3_BUCKET": "videos", "TIKTOK_CLIENT_KEY": "a'b\"c"})
    assert stat.S_IMODE(config.env_file().stat().st_mode) == 0o600
    assert config.read_env_file() == {"S3_BUCKET": "videos", "TIKTOK_CLIENT_KEY": "a'b\"c"}
    assert config.env("S3_BUCKET") == "videos"
    config.write_env_file({"S3_BUCKET": ""})
    assert "S3_BUCKET" not in config.read_env_file()
    monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
