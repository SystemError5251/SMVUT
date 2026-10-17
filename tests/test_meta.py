from __future__ import annotations

from pathlib import Path

import pytest

from terminal.errors import MetaError
from terminal.meta import load_meta, meta_path_for


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "clip.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_full_example() -> None:
    meta = load_meta(Path(__file__).parent.parent / "examples" / "clip.yaml")
    assert meta.title == "Drei Fakten über Oktopusse"
    assert meta.targets == ["youtube", "instagram", "tiktok"]
    assert meta.platforms.youtube is not None
    assert meta.platforms.youtube.category_id == "27"


def test_shared_text_and_overrides(tmp_path: Path) -> None:
    meta = load_meta(write(tmp_path, """\
title: T
description: Hallo
hashtags: ["#eins", zwei]
platforms:
  instagram: {caption: "Nur Insta"}
  tiktok: {}
"""))
    assert meta.hashtags == ["eins", "zwei"]
    assert meta.caption_for("tiktok") == "Hallo\n\n#eins #zwei"
    assert meta.caption_for("instagram") == "Nur Insta"
    assert meta.targets == ["instagram", "tiktok"]


def test_typo_reports_line_number(tmp_path: Path) -> None:
    path = write(tmp_path, """\
title: T
platforms:
  youtube:
    privcy: public
""")
    with pytest.raises(MetaError) as exc:
        load_meta(path)
    assert "clip.yaml:4" in str(exc.value)
    assert "platforms.youtube.privcy" in str(exc.value)
    assert "Tippfehler" in str(exc.value)


def test_publish_at_forces_private(tmp_path: Path) -> None:
    meta = load_meta(write(tmp_path, """\
title: T
platforms:
  youtube:
    privacy: public
    publish_at: 2026-10-20T18:00:00+02:00
"""))
    assert meta.platforms.youtube is not None
    assert meta.platforms.youtube.privacy == "private"


def test_publish_at_needs_timezone(tmp_path: Path) -> None:
    with pytest.raises(MetaError, match="Zeitzone"):
        load_meta(write(tmp_path, "title: T\nplatforms:\n  youtube:\n"
                                  "    publish_at: 2026-10-20T18:00:00\n"))


def test_missing_yaml(tmp_path: Path) -> None:
    with pytest.raises(MetaError, match="clip.yaml fehlt"):
        meta_path_for(tmp_path / "clip.mp4")
