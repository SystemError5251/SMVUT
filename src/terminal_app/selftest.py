"""Selbsttest auf dem Gerät: Python-Umgebung und Android-Anbindung, ohne Konten.

Auf dem Handy oder im Emulator starten mit:
    adb shell am start -n io.github.systemerror5251.terminal_app/org.beeware.android.MainActivity \
        --ez selftest true
Ergebnis steht im Logcat (Zeilen „SELFTEST …“) und im Ergebnisfeld der App.
"""

from __future__ import annotations

import importlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from terminal import config, mp4
from terminal.auth import TokenStore
from terminal.history import PUBLISHED, History
from terminal.hosting import SigV4
from terminal.validate import faststart_copy, video_info
from terminal_app import device

SAMPLE = Path(__file__).parent / "resources" / "selftest.mp4"
MODULES = ("google_auth_oauthlib.flow", "google.oauth2.credentials",
           "google.auth.transport.requests", "cryptography.hazmat.primitives.hashes", "httpx",
           "yaml", "tenacity", "dotenv")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _run(checks: list[Check], name: str, fn: Callable[[], Any]) -> None:
    try:
        detail = fn()
        checks.append(Check(name, True, str(detail) if detail is not None else "ok"))
    except Exception as exc:
        checks.append(Check(name, False, f"{type(exc).__name__}: {exc}"))


def background_checks() -> list[Check]:
    """Alles, was im Hintergrund-Thread laufen darf."""
    checks: list[Check] = []
    _run(checks, "Datenordner", lambda: f"{config.home()} (Token-Speicher: "
                                        f"{config.token_backend()})")
    _run(checks, "Token-Speicher", _token_store)
    _run(checks, "Verlauf (SQLite)", _history)
    _run(checks, "MP4 lesen", _probe)
    _run(checks, "faststart", _faststart)
    _run(checks, "Bibliotheken", _imports)
    _run(checks, "Google-Login (offline)", _google_oauth)
    _run(checks, "S3/R2-Signatur (offline)", _s3_signature)
    _run(checks, "HTTPS", _https)
    return checks


def ui_checks(app: Any) -> list[Check]:
    """Java-Aufrufe, die im UI-Thread laufen müssen (nur auf Android)."""
    checks: list[Check] = []
    if not device.IS_ANDROID:
        return checks
    _run(checks, "Java-Klassen", _java_classes)
    _run(checks, "Zwischenablage", lambda: _clipboard(app))
    _run(checks, "Bildschirm anlassen", lambda: _screen(app))
    return checks


def _clipboard(app: Any) -> str:
    if not device.copy_text(app, "Selbsttest", "ok"):
        _fail("copy_text lieferte False")
    return "ok"


def _screen(app: Any) -> str:
    device.keep_screen_on(app, True)
    device.keep_screen_on(app, False)
    return "ok"


def _fail(message: str) -> None:
    raise RuntimeError(message)


def _token_store() -> str:
    store = TokenStore()
    store.save("selftest", {"value": 1})
    loaded = store.load("selftest")
    store.delete("selftest")
    if loaded != {"value": 1} or store.load("selftest") is not None:
        _fail(f"gelesen: {loaded!r}")
    return store.backend


def _history() -> str:
    path = config.home() / "selftest.db"
    history = History(path)
    try:
        history.save("0" * 64, "youtube", "x.mp4", PUBLISHED, link="https://example.com")
        entry = history.get("0" * 64, "youtube")
        if entry is None or not entry.done:
            _fail("Eintrag fehlt")
    finally:
        history.close()
        path.unlink(missing_ok=True)
    return "ok"


def _probe() -> str:
    info = video_info(SAMPLE)
    if (info.width, info.height, info.codec) != (144, 256, "h264"):
        _fail(f"unerwartet: {info}")
    return f"{info.width}×{info.height} {info.codec} {info.fps:g} fps {info.duration:.1f} s"


def _faststart() -> str:
    work = config.cache_dir() / "selftest-input.mp4"
    shutil.copyfile(SAMPLE, work)
    try:
        out = faststart_copy(work, "f" * 64)
        data = out.read_bytes()
        if out == work or data.index(b"moov") > data.index(b"mdat") or mp4.needs_faststart(out):
            _fail("moov nicht vorne")
        out.unlink()
    finally:
        work.unlink(missing_ok=True)
    return "ok"


def _imports() -> str:
    for name in MODULES:
        importlib.import_module(name)
    return f"{len(MODULES)} Module"


def _google_oauth() -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": "selftest", "client_secret": "selftest",
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token"}},
        config.YOUTUBE_SCOPES)
    flow.redirect_uri = "http://localhost:1/"
    url, _ = flow.authorization_url(access_type="offline")
    if not url.startswith("https://accounts.google.com/"):
        _fail(url)
    return "ok"


def _s3_signature() -> str:
    """Offizieller AWS-Testvektor (presigned GET) – prüft hashlib/hmac auf dem Gerät."""
    signer = SigV4("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                   "us-east-1")
    url = signer.presign("GET", "https://examplebucket.s3.amazonaws.com/test.txt", 86400,
                         datetime(2013, 5, 24, tzinfo=UTC))
    if not url.endswith("aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"):
        _fail("Signatur weicht ab")
    return "ok"


def _https() -> str:
    response = httpx.get("https://www.googleapis.com/discovery/v1/apis",
                         params={"name": "youtube", "preferred": "true"}, timeout=30)
    response.raise_for_status()
    return f"HTTP {response.status_code}"


def _java_classes() -> str:
    from android.app import Activity  # noqa: F401
    from android.content import ClipboardManager, Intent  # noqa: F401
    from android.net import Uri  # noqa: F401
    from android.provider import OpenableColumns  # noqa: F401
    from android.view import WindowManager  # noqa: F401
    from java import cast  # noqa: F401

    return "ok"


def report(checks: list[Check]) -> str:
    lines = [f"{'OK  ' if c.ok else 'FEHL'} {c.name}: {c.detail}" for c in checks]
    passed = all(c.ok for c in checks)
    lines.append(f"SELFTEST RESULT: {'PASS' if passed else 'FAIL'} "
                 f"({sum(c.ok for c in checks)}/{len(checks)})")
    return "\n".join(lines)
