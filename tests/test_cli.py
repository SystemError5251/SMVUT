from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from terminal import cli, platforms
from terminal.auth import TokenStore
from terminal.errors import UploadError
from terminal.history import DRAFT_SENT, PUBLISHED
from terminal.meta import PostMeta
from terminal.platforms.base import UploadContext, UploadResult
from terminal.validate import Issue, VideoInfo

runner = CliRunner()


class FakeUploader:
    calls: list[str] = []
    fail: set[str] = set()

    def __init__(self, name: str) -> None:
        self.name = name

    def check(self, video: Path, meta: PostMeta, info: VideoInfo) -> list[Issue]:
        return []

    def upload(self, video: Path, meta: PostMeta, ctx: UploadContext) -> UploadResult:
        FakeUploader.calls.append(self.name)
        ctx.progress(1, 1)
        if self.name in FakeUploader.fail:
            raise UploadError(f"{self.name} kaputt")
        status = DRAFT_SENT if self.name == "tiktok" else PUBLISHED
        note = "Caption zum Kopieren" if self.name == "tiktok" else None
        return UploadResult(self.name, status, f"{self.name}-id", f"https://{self.name}", note)


@pytest.fixture(autouse=True)
def fake_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeUploader.calls = []
    FakeUploader.fail = set()

    def create(name: str, store: TokenStore) -> Any:
        return FakeUploader(name)

    monkeypatch.setattr(platforms, "create", create)
    monkeypatch.setattr(cli.platforms, "create", create)
    monkeypatch.setattr(cli, "console", Console(width=200))


def test_check_ok(clip: Path) -> None:
    result = runner.invoke(cli.app, ["check", str(clip)])
    assert result.exit_code == 0, result.output
    assert "1080×1920" in result.output and "Alles in Ordnung" in result.output


def test_unknown_platform(clip: Path) -> None:
    result = runner.invoke(cli.app, ["check", str(clip), "--only", "myspace"])
    assert result.exit_code == 1 and "Unbekannte Plattform" in result.output


def test_dry_run_uploads_nothing(clip: Path) -> None:
    result = runner.invoke(cli.app, ["post", str(clip), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert FakeUploader.calls == []
    assert "würde hochladen" in result.output


def test_post_then_second_post_skips(clip: Path) -> None:
    first = runner.invoke(cli.app, ["post", str(clip)])
    assert first.exit_code == 0, first.output
    assert FakeUploader.calls == ["youtube", "instagram", "tiktok"]
    assert "Caption zum Kopieren" in first.output

    FakeUploader.calls = []
    second = runner.invoke(cli.app, ["post", str(clip)])
    assert second.exit_code == 0, second.output
    assert FakeUploader.calls == []
    assert "übersprungen" in second.output

    forced = runner.invoke(cli.app, ["post", str(clip), "--only", "youtube", "--force"])
    assert forced.exit_code == 0 and FakeUploader.calls == ["youtube"]


def test_one_failure_does_not_stop_others(clip: Path) -> None:
    FakeUploader.fail = {"instagram"}
    result = runner.invoke(cli.app, ["post", str(clip)])
    assert result.exit_code == 1
    assert FakeUploader.calls == ["youtube", "instagram", "tiktok"]
    assert "instagram kaputt" in result.output

    # erneuter Lauf: nur die gescheiterte Plattform wird nachgeholt
    FakeUploader.fail, FakeUploader.calls = set(), []
    retry = runner.invoke(cli.app, ["post", str(clip)])
    assert retry.exit_code == 0 and FakeUploader.calls == ["instagram"]

    history = runner.invoke(cli.app, ["history"])
    assert "clip.mp4" in history.output and "https://instagram" in history.output


def test_auth_check() -> None:
    result = runner.invoke(cli.app, ["auth", "--check"])
    assert result.exit_code == 0
    assert "nicht angemeldet" in result.output


def test_issue_shows_platform_label(clip: Path) -> None:
    (clip.parent / "clip.yaml").write_text("title: x<y\nplatforms:\n  youtube: {}\n")
    result = runner.invoke(cli.app, ["check", str(clip)])
    assert result.exit_code == 1
    assert "[youtube] Titel darf kein <" in result.output
