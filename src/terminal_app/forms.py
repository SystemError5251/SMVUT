"""Formular-Logik der App, unabhängig von der Oberfläche (testbar ohne Android)."""

from __future__ import annotations

import re
from typing import Any

from terminal.errors import MetaError
from terminal.history import FAILED, STATUS_LABELS
from terminal.meta import PostMeta, parse_meta
from terminal.platforms.base import UploadResult
from terminal.service import FROM_HISTORY
from terminal.validate import Issue, VideoInfo

PLATFORM_LABELS = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}
PRIVACY_LABELS = {"öffentlich": "public", "nicht gelistet": "unlisted", "privat": "private"}
FIELD_LABELS = {"title": "Titel", "description": "Beschreibung", "hashtags": "Hashtags"}

# Einstellungen, die in ~/.terminal/.env landen: (Schlüssel, Beschriftung, geheim?)
SETTINGS: list[tuple[str, str, bool]] = [
    ("S3_ENDPOINT_URL", "Speicher-Endpoint (R2: https://<ID>.r2.cloudflarestorage.com)", False),
    ("S3_BUCKET", "Bucket-Name", False),
    ("S3_ACCESS_KEY_ID", "Access Key ID", False),
    ("S3_SECRET_ACCESS_KEY", "Secret Access Key", True),
    ("S3_REGION", "Region (R2: auto)", False),
    ("INSTAGRAM_APP_SECRET", "Instagram App-Secret (optional)", True),
    ("TIKTOK_CLIENT_KEY", "TikTok Client Key", False),
    ("TIKTOK_CLIENT_SECRET", "TikTok Client Secret", True),
]


def parse_hashtags(text: str) -> list[str]:
    """„#a #b, c“ → ["a", "b", "c"]."""
    tags = [t.strip().lstrip("#") for t in re.split(r"[\s,;]+", text)]
    return [t for t in tags if t]


def build_meta(*, title: str, description: str, hashtags: str, platforms: list[str],
               privacy: str = "öffentlich", ai_generated: bool = False) -> PostMeta:
    if not platforms:
        raise MetaError("Bitte mindestens eine Plattform auswählen")
    sections: dict[str, Any] = {name: {} for name in platforms}
    if "youtube" in sections:
        sections["youtube"]["privacy"] = PRIVACY_LABELS.get(privacy, privacy)
    data = {"title": title, "description": description,
            "hashtags": parse_hashtags(hashtags), "ai_generated": ai_generated,
            "platforms": sections}
    meta, errors = parse_meta(data)
    if meta is None or errors:
        lines = [f"{FIELD_LABELS.get(str(loc[0]), '.'.join(map(str, loc)))}: {msg}"
                 for loc, msg in errors]
        raise MetaError("\n".join(lines) or "Eingaben ungültig")
    return meta


def describe_video(name: str, info: VideoInfo) -> str:
    return (f"{name}\n{info.width}×{info.height} · {info.codec} · {info.fps:g} fps · "
            f"{info.duration:.1f} s · {info.size / 1024 / 1024:.1f} MB")


def format_issues(issues: list[Issue]) -> str:
    lines = []
    for issue in issues:
        prefix = "Fehler" if issue.blocking else "Hinweis"
        where = PLATFORM_LABELS.get(issue.platform, "Video")
        lines.append(f"{prefix} ({where}): {issue.message}")
    return "\n".join(lines)


def format_results(results: list[UploadResult]) -> str:
    lines = []
    for r in results:
        status = STATUS_LABELS.get(r.status, r.status)
        mark = "✗" if r.status == FAILED else "✓"
        line = f"{mark} {PLATFORM_LABELS.get(r.platform, r.platform)}: {status}"
        if r.link or r.remote_id:
            line += f"\n   {r.link or r.remote_id}"
        if r.status == FAILED and r.note:
            line += f"\n   {r.note}"
        elif r.note == FROM_HISTORY:
            line += " (schon früher erledigt)"
        lines.append(line)
    return "\n".join(lines)


def tiktok_caption(results: list[UploadResult], meta: PostMeta) -> str | None:
    """Caption zum Kopieren, wenn ein TikTok-Entwurf gerade gesendet wurde."""
    for r in results:
        if r.platform == "tiktok" and r.status != FAILED and r.note != FROM_HISTORY:
            return meta.caption_for("tiktok")
    return None
