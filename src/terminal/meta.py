"""PostMeta: die YAML-Datei neben jedem Video."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from terminal.errors import MetaError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstagramOptions(StrictModel):
    share_to_feed: bool = True
    caption: str | None = None  # None = gemeinsamer Text aus description + hashtags


class YouTubeOptions(StrictModel):
    privacy: Literal["public", "unlisted", "private"] = "public"
    category_id: str = "22"  # 22 = People & Blogs, 27 = Bildung
    made_for_kids: bool = False
    publish_at: datetime | None = None
    description: str | None = None  # None = gemeinsamer Text

    @field_validator("category_id", mode="before")
    @classmethod
    def _category_as_str(cls, value: Any) -> Any:
        return str(value) if isinstance(value, int) else value

    @field_validator("publish_at")
    @classmethod
    def _needs_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("publish_at braucht eine Zeitzone, z. B. 2026-10-20T18:00:00+02:00")
        return value

    @model_validator(mode="after")
    def _scheduled_is_private(self) -> YouTubeOptions:
        # YouTube verlangt privacyStatus=private, solange publishAt gesetzt ist
        if self.publish_at is not None:
            self.privacy = "private"
        return self


class TikTokOptions(StrictModel):
    caption: str | None = None  # wird nur zum Kopieren ausgegeben (Entwurf)


class Platforms(StrictModel):
    instagram: InstagramOptions | None = None
    youtube: YouTubeOptions | None = None
    tiktok: TikTokOptions | None = None


class PostMeta(StrictModel):
    title: str
    description: str = ""
    hashtags: list[str] = []
    ai_generated: bool = False
    platforms: Platforms = Platforms()

    @field_validator("hashtags")
    @classmethod
    def _strip_hash(cls, tags: list[str]) -> list[str]:
        cleaned = [t.strip().lstrip("#").strip() for t in tags]
        return [t for t in cleaned if t]

    @field_validator("title")
    @classmethod
    def _title_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title darf nicht leer sein")
        return value.strip()

    @property
    def targets(self) -> list[str]:
        """Plattformen mit eigenem Abschnitt in der YAML (fehlende werden übersprungen)."""
        return [name for name in ("youtube", "instagram", "tiktok")
                if getattr(self.platforms, name) is not None]

    def shared_text(self) -> str:
        tags = " ".join(f"#{t}" for t in self.hashtags)
        return "\n\n".join(part for part in (self.description.strip(), tags) if part)

    def caption_for(self, platform: str) -> str:
        options = getattr(self.platforms, platform)
        if platform == "youtube":
            override = options.description if options else None
        else:
            override = options.caption if options else None
        return override if override is not None else self.shared_text()


def meta_path_for(video: Path) -> Path:
    for suffix in (".yaml", ".yml"):
        candidate = video.with_suffix(suffix)
        if candidate.exists():
            return candidate
    raise MetaError(f"Keine Metadaten gefunden: {video.with_suffix('.yaml').name} fehlt "
                    f"neben {video.name}")


def _line_of(node: yaml.Node | None, loc: tuple[int | str, ...]) -> int | None:
    """Sucht die Zeile eines Feldes im YAML-Baum, damit Fehler eine Zeilenangabe bekommen."""
    line = node.start_mark.line + 1 if node else None
    for key in loc:
        if isinstance(node, yaml.MappingNode):
            match = next((v for k, v in node.value if k.value == key), None)
            key_node = next((k for k, _ in node.value if k.value == key), None)
            if match is None:
                break
            line = (key_node or match).start_mark.line + 1
            node = match
        elif isinstance(node, yaml.SequenceNode) and isinstance(key, int) and key < len(
                node.value):
            node = node.value[key]
            line = node.start_mark.line + 1
        else:
            break
    return line


def load_meta(path: Path) -> PostMeta:
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
        tree = yaml.compose(text)
    except yaml.YAMLError as exc:
        raise MetaError(f"{path.name}: kein gültiges YAML – {exc}") from exc
    if not isinstance(data, dict):
        raise MetaError(f"{path.name}: erwartet wird eine Zuordnung (key: value)")
    try:
        return PostMeta.model_validate(data)
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            loc = tuple(err["loc"])
            line = _line_of(tree, loc)
            where = ".".join(str(p) for p in loc) or "(Datei)"
            msg = "unbekanntes Feld (Tippfehler?)" if err["type"] == "extra_forbidden" \
                else err["msg"]
            lines.append(f"  {path.name}:{line or '?'}  {where}: {msg}")
        raise MetaError("Metadaten ungültig:\n" + "\n".join(lines)) from exc
