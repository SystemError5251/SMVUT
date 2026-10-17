"""Was auf Android anders läuft als am Desktop: Dateiauswahl, Browser, Zwischenablage.

Auf Android spricht dieses Modul über Chaquopy direkt mit der Java-API.
Am Desktop (zum Entwickeln) gibt es einfache Ersatzwege.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import webbrowser
from pathlib import Path
from typing import Any

import toga

IS_ANDROID = sys.platform == "android"


def setup(app: toga.App) -> None:
    """Vor allem anderen aufrufen: Datenordner und Browser für die Logins festlegen."""
    if not IS_ANDROID:
        return
    # Tokens, Historie und Einstellungen im privaten App-Speicher
    os.environ.setdefault("TERMINAL_HOME", str(app.paths.data))
    webbrowser.register("android-intent", None, _AndroidBrowser(app), preferred=True)


def selftest_requested(app: toga.App) -> bool:
    """Start mit `--ez selftest true` (Android) bzw. TERMINAL_SELFTEST=1 (Desktop)."""
    if not IS_ANDROID:
        return os.environ.get("TERMINAL_SELFTEST") == "1"
    intent = _activity(app).getIntent()
    return bool(intent is not None and intent.getBooleanExtra("selftest", False))


def _activity(app: toga.App) -> Any:
    return app._impl.native


class _AndroidBrowser(webbrowser.BaseBrowser):
    """Öffnet Login-Seiten im Standard-Browser des Handys (auch aus Hintergrund-Threads)."""

    def __init__(self, app: toga.App) -> None:
        super().__init__("android-intent")
        self.app = app

    def open(self, url: str, new: int = 0, autoraise: bool = True) -> bool:
        self.app.loop.call_soon_threadsafe(open_url, self.app, url)
        return True


def open_url(app: toga.App, url: str) -> None:
    if not IS_ANDROID:
        webbrowser.open(url)
        return
    from android.content import Intent
    from android.net import Uri

    intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    _activity(app).startActivity(intent)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .") or "datei"
    return name[:120]


async def pick_file(app: toga.App, mime: str, target_dir: Path) -> Path | None:
    """Lässt eine Datei auswählen und kopiert sie in `target_dir` (Android: Inhalts-URI)."""
    if not IS_ANDROID:
        window = app.main_window
        assert isinstance(window, toga.Window)
        path = await window.dialog(toga.OpenFileDialog("Datei auswählen"))
        return Path(path) if path else None

    from android.app import Activity
    from android.content import Intent

    intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    intent.setType(mime)
    future: asyncio.Future[tuple[int, Any]] = app.loop.create_future()
    app._impl.start_activity(
        intent, on_complete=lambda code, data: future.set_result((code, data)))
    code, data = await future
    if code != Activity.RESULT_OK or data is None or data.getData() is None:
        return None
    uri = data.getData()
    resolver = _activity(app).getContentResolver()
    target = target_dir / _safe_name(_display_name(resolver, uri) or "video.mp4")
    target_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_copy_uri, resolver, uri, target)
    return target


def _display_name(resolver: Any, uri: Any) -> str | None:
    from android.provider import OpenableColumns

    cursor = resolver.query(uri, None, None, None, None)
    if cursor is None:
        return None
    try:
        if cursor.moveToFirst():
            index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if index >= 0:
                return str(cursor.getString(index))
        return None
    finally:
        cursor.close()


def _copy_uri(resolver: Any, uri: Any, target: Path) -> None:
    # Den Dateideskriptor an Python übergeben: schnell, ohne Java-Byte-Arrays
    fd = resolver.openFileDescriptor(uri, "r").detachFd()
    tmp = target.with_name(target.name + ".part")
    with os.fdopen(fd, "rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
    tmp.replace(target)


def copy_text(app: toga.App, label: str, text: str) -> bool:
    """In die Zwischenablage kopieren; False, wenn das hier nicht geht."""
    if not IS_ANDROID:
        return False
    from android.content import ClipboardManager, ClipData, Context
    from java import cast

    manager = cast(ClipboardManager,
                   _activity(app).getSystemService(Context.CLIPBOARD_SERVICE))
    manager.setPrimaryClip(ClipData.newPlainText(label, text))
    return True


def keep_screen_on(app: toga.App, on: bool) -> None:
    """Während eines Uploads den Bildschirm anlassen, damit Android nichts anhält."""
    if not IS_ANDROID:
        return
    from android.view import WindowManager

    flag = WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
    window = _activity(app).getWindow()
    if on:
        window.addFlags(flag)
    else:
        window.clearFlags(flag)
