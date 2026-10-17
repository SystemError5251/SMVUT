from __future__ import annotations

import pytest

from terminal.errors import MetaError
from terminal.history import DRAFT_SENT, FAILED, PUBLISHED
from terminal.platforms.base import UploadResult
from terminal.service import FROM_HISTORY

forms = pytest.importorskip("terminal_app.forms")


def test_parse_hashtags() -> None:
    assert forms.parse_hashtags("#tiere, natur;  #fakten\nwissen") == [
        "tiere", "natur", "fakten", "wissen"]


def test_build_meta() -> None:
    meta = forms.build_meta(title=" Oktopus ", description="Drei Herzen",
                            hashtags="#tiere fakten", platforms=["youtube", "tiktok"],
                            privacy="nicht gelistet", ai_generated=True)
    assert meta.title == "Oktopus"
    assert meta.targets == ["youtube", "tiktok"]
    assert meta.platforms.youtube is not None
    assert meta.platforms.youtube.privacy == "unlisted"
    assert meta.ai_generated
    assert meta.caption_for("tiktok") == "Drei Herzen\n\n#tiere #fakten"


def test_build_meta_errors() -> None:
    with pytest.raises(MetaError, match="mindestens eine Plattform"):
        forms.build_meta(title="T", description="", hashtags="", platforms=[])
    with pytest.raises(MetaError, match="Titel: title darf nicht leer sein"):
        forms.build_meta(title="  ", description="", hashtags="", platforms=["youtube"])


def test_results_and_caption() -> None:
    meta = forms.build_meta(title="T", description="Text", hashtags="a",
                            platforms=["youtube", "instagram", "tiktok"])
    results = [UploadResult("youtube", PUBLISHED, "v1", "https://youtube.com/shorts/v1"),
               UploadResult("instagram", FAILED, note="Tageslimit erreicht"),
               UploadResult("tiktok", DRAFT_SENT, "p1", None, "…")]
    text = forms.format_results(results)
    assert "✓ YouTube: veröffentlicht\n   https://youtube.com/shorts/v1" in text
    assert "✗ Instagram: fehlgeschlagen\n   Tageslimit erreicht" in text
    assert "✓ TikTok: Entwurf gesendet" in text
    assert forms.tiktok_caption(results, meta) == "Text\n\n#a"
    old = [UploadResult("tiktok", DRAFT_SENT, "p1", None, FROM_HISTORY)]
    assert forms.tiktok_caption(old, meta) is None
