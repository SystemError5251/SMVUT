"""PostMeta: die YAML-Datei neben jedem Video.

Bewusst ohne pydantic: pydantic-core hat keine Android-Wheels und müsste in Termux
mit Rust kompiliert werden. Die Prüfung hier ist reines Python.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import MISSING, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from terminal.errors import MetaError

PRIVACY = ("public", "unlisted", "private")
Loc = tuple[int | str, ...]


@dataclass
class InstagramOptions:
    share_to_feed: bool = True
    caption: str | None = None  # None = gemeinsamer Text aus description + hashtags


@dataclass
class YouTubeOptions:
    privacy: str = "public"  # public | unlisted | private
    category_id: str = "22"  # 22 = People & Blogs, 27 = Bildung
    made_for_kids: bool = False
    publish_at: datetime | None = None
    description: str | None = None  # None = gemeinsamer Text

    def __post_init__(self) -> None:
        # YouTube verlangt privacyStatus=private, solange publishAt gesetzt ist
        if self.publish_at is not None:
            self.privacy = "private"


@dataclass
class TikTokOptions:
    caption: str | None = None  # wird nur zum Kopieren ausgegeben (Entwurf)


@dataclass
class Platforms:
    instagram: InstagramOptions | None = None
    youtube: YouTubeOptions | None = None
    tiktok: TikTokOptions | None = None


@dataclass
class PostMeta:
    title: str
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    ai_generated: bool = False
    platforms: Platforms = field(default_factory=Platforms)

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


# --- Prüfung ---------------------------------------------------------------
class _Errors:
    def __init__(self) -> None:
        self.items: list[tuple[Loc, str]] = []

    def add(self, loc: Loc, msg: str) -> None:
        self.items.append((loc, msg))


Check = Callable[[Any, Loc, _Errors], Any]


def _str(value: Any, loc: Loc, errors: _Errors) -> Any:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        errors.add(loc, "Text erwartet")
        return None
    return str(value)


def _opt_str(value: Any, loc: Loc, errors: _Errors) -> Any:
    return None if value is None else _str(value, loc, errors)


def _bool(value: Any, loc: Loc, errors: _Errors) -> Any:
    if not isinstance(value, bool):
        errors.add(loc, "true oder false erwartet")
    return value


def _str_list(value: Any, loc: Loc, errors: _Errors) -> Any:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.add(loc, "Liste erwartet, z. B. [eins, zwei]")
        return []
    items = [_str(v, (*loc, i), errors) for i, v in enumerate(value)]
    cleaned = [str(t).strip().lstrip("#").strip() for t in items if t is not None]
    return [t for t in cleaned if t]


def _privacy(value: Any, loc: Loc, errors: _Errors) -> Any:
    if value not in PRIVACY:
        errors.add(loc, f"erlaubt: {', '.join(PRIVACY)}")
    return value


def _opt_datetime(value: Any, loc: Loc, errors: _Errors) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            errors.add(loc, "Datum/Zeit erwartet, z. B. 2026-10-20T18:00:00+02:00")
            return None
    if not isinstance(value, datetime):  # ein reines Datum reicht nicht
        errors.add(loc, "Datum mit Uhrzeit erwartet, z. B. 2026-10-20T18:00:00+02:00")
        return None
    if value.tzinfo is None:
        errors.add(loc, "publish_at braucht eine Zeitzone, z. B. 2026-10-20T18:00:00+02:00")
        return None
    return value


def _section(cls: type, spec: dict[str, Check]) -> Check:
    def check(value: Any, loc: Loc, errors: _Errors) -> Any:
        if value is None:  # `tiktok:` ohne Inhalt = Plattform mit Standardwerten
            return cls()
        if not isinstance(value, dict):
            errors.add(loc, "Abschnitt mit key: value erwartet")
            return None
        return _build(cls, spec, value, loc, errors)
    return check


def _build(cls: type, spec: dict[str, Check], data: dict[str, Any], loc: Loc,
           errors: _Errors) -> Any:
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in spec:
            errors.add((*loc, key), "unbekanntes Feld (Tippfehler?)")
            continue
        checked = spec[key](value, (*loc, key), errors)
        if checked is not None:
            kwargs[key] = checked
    for f in fields(cls):
        required = f.default is MISSING and f.default_factory is MISSING
        if required and f.name not in data:
            errors.add((*loc, f.name), "Pflichtfeld fehlt")
    if errors.items:
        return None
    return cls(**kwargs)


INSTAGRAM_SPEC: dict[str, Check] = {"share_to_feed": _bool, "caption": _opt_str}
YOUTUBE_SPEC: dict[str, Check] = {"privacy": _privacy, "category_id": _str,
                                  "made_for_kids": _bool, "publish_at": _opt_datetime,
                                  "description": _opt_str}
TIKTOK_SPEC: dict[str, Check] = {"caption": _opt_str}
PLATFORMS_SPEC: dict[str, Check] = {
    "instagram": _section(InstagramOptions, INSTAGRAM_SPEC),
    "youtube": _section(YouTubeOptions, YOUTUBE_SPEC),
    "tiktok": _section(TikTokOptions, TIKTOK_SPEC),
}


def _title(value: Any, loc: Loc, errors: _Errors) -> Any:
    text = _str(value, loc, errors)
    if text is not None and not text.strip():
        errors.add(loc, "title darf nicht leer sein")
        return None
    return text.strip() if text else text


def _platforms(value: Any, loc: Loc, errors: _Errors) -> Any:
    if value is None:
        return Platforms()
    return _section(Platforms, PLATFORMS_SPEC)(value, loc, errors)


META_SPEC: dict[str, Check] = {"title": _title, "description": _str, "hashtags": _str_list,
                               "ai_generated": _bool, "platforms": _platforms}


# --- Laden -----------------------------------------------------------------
def meta_path_for(video: Path) -> Path:
    for suffix in (".yaml", ".yml"):
        candidate = video.with_suffix(suffix)
        if candidate.exists():
            return candidate
    raise MetaError(f"Keine Metadaten gefunden: {video.with_suffix('.yaml').name} fehlt "
                    f"neben {video.name}")


def _line_of(node: yaml.Node | None, loc: Loc) -> int | None:
    """Sucht die Zeile eines Feldes im YAML-Baum, damit Fehler eine Zeilenangabe bekommen."""
    line = node.start_mark.line + 1 if node else None
    for key in loc:
        if isinstance(node, yaml.MappingNode):
            pair = next(((k, v) for k, v in node.value if k.value == key), None)
            if pair is None:
                break
            line = pair[0].start_mark.line + 1
            node = pair[1]
        elif isinstance(node, yaml.SequenceNode) and isinstance(key, int) and key < len(
                node.value):
            node = node.value[key]
            line = node.start_mark.line + 1
        else:
            break
    return line


def parse_meta(data: dict[str, Any]) -> tuple[PostMeta | None, list[tuple[Loc, str]]]:
    errors = _Errors()
    meta = _build(PostMeta, META_SPEC, data, (), errors)
    return meta, errors.items


def load_meta(path: Path) -> PostMeta:
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
        tree = yaml.compose(text)
    except yaml.YAMLError as exc:
        raise MetaError(f"{path.name}: kein gültiges YAML – {exc}") from exc
    if not isinstance(data, dict):
        raise MetaError(f"{path.name}: erwartet wird eine Zuordnung (key: value)")
    meta, errors = parse_meta(data)
    if meta is None or errors:
        lines = []
        for loc, msg in errors:
            where = ".".join(str(p) for p in loc) or "(Datei)"
            lines.append(f"  {path.name}:{_line_of(tree, loc) or '?'}  {where}: {msg}")
        raise MetaError("Metadaten ungültig:\n" + "\n".join(lines))
    return meta
