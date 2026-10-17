"""Uploader je Plattform. Eine neue Plattform kommt als weitere Datei dazu."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terminal.auth import TokenStore
    from terminal.platforms.base import Uploader


def create(name: str, store: TokenStore) -> Uploader:
    if name == "youtube":
        from terminal.platforms.youtube import YouTubeUploader

        return YouTubeUploader(store)
    if name == "instagram":
        from terminal.platforms.instagram import InstagramUploader

        return InstagramUploader(store)
    if name == "tiktok":
        from terminal.platforms.tiktok import TikTokUploader

        return TikTokUploader(store)
    raise ValueError(f"Unbekannte Plattform: {name}")
