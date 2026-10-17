"""Baut die echte App-Oberfläche mit Togas kopflosem Test-Backend (ohne Android)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("toga_dummy")
os.environ["TOGA_BACKEND"] = "toga_dummy"


def test_app_builds_and_saves_settings(tmp_path: Path) -> None:
    from terminal import config
    from terminal_app.app import main

    app = main()
    assert [tab.text for tab in app.tabs.content] == ["Hochladen", "Konten", "Verlauf"]
    assert app.switches["youtube"].value and app.post_button.enabled
    assert "✗ YouTube: nicht angemeldet" in app.accounts_status.text

    app.settings["S3_BUCKET"].value = "videos"
    config.write_env_file({k: str(f.value) for k, f in app.settings.items()})
    assert config.read_env_file()["S3_BUCKET"] == "videos"

    app.tabs.current_tab = "Verlauf"
    app._refresh_history()
    assert len(app.history_table.data) == 0

    from terminal.history import PUBLISHED, History

    history = History()
    history.save("s" * 64, "youtube", "/x/clip.mp4", PUBLISHED, remote_id="v1",
                 link="https://youtube.com/shorts/v1")
    history.close()
    app._refresh_history()
    row = app.history_table.data[0]
    assert (row.video, row.plattform, row.status) == ("clip.mp4", "YouTube", "veröffentlicht")
    assert row.link == "https://youtube.com/shorts/v1"


def test_selftest_passes_on_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Prüfungen, die auch ohne Android laufen (HTTPS braucht Netz)."""
    import asyncio

    from terminal_app import selftest
    from terminal_app.app import main

    monkeypatch.setattr(selftest, "_https", lambda: "übersprungen (Test ohne Netz)")
    app = main()
    text = asyncio.run(app.run_selftest())
    assert "SELFTEST RESULT: PASS" in text, text
    assert "OK   MP4 lesen: 144×256 h264 30 fps 1.0 s" in text
