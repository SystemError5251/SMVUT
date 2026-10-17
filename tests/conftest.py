from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from terminal.auth import TokenStore

TEST_YAML = """\
title: "Testvideo"
description: |
  Ein kurzer Test.
hashtags: [test, terminal]
platforms:
  instagram:
    share_to_feed: true
  youtube:
    privacy: private
  tiktok: {}
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMINAL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TERMINAL_TOKEN_STORE", "file")  # läuft auch auf Termux ohne keyring
    for name in ("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL",
                 "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "INSTAGRAM_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def store() -> TokenStore:
    return TokenStore()


def _make_video(path: Path, size: str, seconds: int = 5) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg nicht installiert")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds), "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True)
    return path


@pytest.fixture(scope="session")
def video_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("videos")


@pytest.fixture(scope="session")
def vertical_video(video_dir: Path) -> Path:
    return _make_video(video_dir / "vertical.mp4", "1080x1920")


@pytest.fixture(scope="session")
def landscape_video(video_dir: Path) -> Path:
    return _make_video(video_dir / "landscape.mp4", "640x360")


@pytest.fixture
def clip(tmp_path: Path, vertical_video: Path) -> Path:
    """Testvideo mit gleichnamiger YAML in einem eigenen Ordner."""
    target = tmp_path / "clip.mp4"
    shutil.copy(vertical_video, target)
    (tmp_path / "clip.yaml").write_text(TEST_YAML, encoding="utf-8")
    return target


def fake_ctx(**kwargs: Any) -> Any:
    from terminal.platforms.base import UploadContext

    saved: list[tuple[str, dict[str, Any]]] = []
    ctx = UploadContext(sha256="ab" * 32, progress=lambda done, total: None,
                        save=lambda status, **kw: saved.append((status, kw)), **kwargs)
    ctx.saved = saved  # type: ignore[attr-defined]
    return ctx
