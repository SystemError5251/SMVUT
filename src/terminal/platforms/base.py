"""Gemeinsame Schnittstelle aller Uploader. cli.py kennt nur dieses Protokoll."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from terminal.history import Entry
from terminal.meta import PostMeta
from terminal.validate import Issue, VideoInfo

Progress = Callable[[int, int], None]  # (erledigt, gesamt) in Bytes


@dataclass
class UploadResult:
    platform: str
    status: str  # Status-Werte aus history.py
    remote_id: str | None = None
    link: str | None = None
    note: str | None = None  # z. B. Caption zum Kopieren bei TikTok


@dataclass
class UploadContext:
    """Was ein Uploader über den Lauf wissen muss, ohne die Historie selbst zu kennen."""

    sha256: str
    progress: Progress
    previous: Entry | None = None
    save: Callable[..., Any] = field(default=lambda *a, **k: None)
    log: Callable[[str], None] = field(default=lambda msg: None)


class Uploader(Protocol):
    name: str  # "instagram" | "youtube" | "tiktok"

    def check(self, video: Path, meta: PostMeta, info: VideoInfo) -> list[Issue]:
        """Plattformspezifische Prüfungen zusätzlich zu validate.py (z. B. Login vorhanden)."""
        ...

    def upload(self, video: Path, meta: PostMeta, ctx: UploadContext) -> UploadResult: ...
