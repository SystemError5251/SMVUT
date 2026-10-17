from __future__ import annotations

from pathlib import Path

from terminal import config
from terminal.meta import PostMeta
from terminal.validate import (
    VideoInfo,
    check_all,
    check_texts,
    check_video,
    faststart_copy,
    utf16_len,
    video_info,
)

ALL = ["youtube", "instagram", "tiktok"]


def info(**overrides: object) -> VideoInfo:
    base: dict[str, object] = dict(codec="h264", width=1080, height=1920, fps=30.0,
                                   duration=20.0, size=10 * config.MB, bitrate=8_000_000,
                                   audio_codec="aac")
    base.update(overrides)
    return VideoInfo(**base)  # type: ignore[arg-type]


def messages(issues: list) -> str:  # type: ignore[type-arg]
    return "\n".join(f"{i.level}:{i.platform}:{i.message}" for i in issues)


def test_real_vertical_video_passes(vertical_video: Path) -> None:
    meta = PostMeta(title="T")
    vi, issues = check_all(vertical_video, meta, ALL)
    assert (vi.width, vi.height, vi.codec, vi.fps) == (1080, 1920, "h264", 30.0)
    assert 4.5 < vi.duration < 5.5
    assert not issues


def test_landscape_warns_for_youtube(landscape_video: Path) -> None:
    issues = check_video(landscape_video, video_info(landscape_video), ["youtube"])
    text = messages(issues)
    assert "warning:youtube:Querformat" in text
    assert "9:16" in text
    assert not any(i.blocking for i in issues)


def test_rotation_swaps_dimensions() -> None:
    data = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920,
                         "height": 1080, "avg_frame_rate": "30000/1001",
                         "side_data_list": [{"rotation": -90}]}],
            "format": {"duration": "10", "size": "1000"}}
    vi = video_info(Path("x.mp4"), data)
    assert (vi.width, vi.height) == (1080, 1920)
    assert vi.fps == 29.97


def test_blocking_video_problems() -> None:
    issues = check_video(Path("clip.mp4"),
                         info(codec="vp9", fps=120.0, duration=2.0, bitrate=30_000_000,
                              width=2160, height=3840), ALL)
    text = messages(issues)
    assert "error:alle:Video-Codec vp9" in text
    assert "error:alle:Bildrate 120" in text
    assert "error:instagram:Video ist 2.0 s lang, mindestens 3 s" in text
    assert "error:instagram:Bitrate 30.0 Mbit/s" in text
    assert "ffmpeg -i clip.mp4" in text
    assert "error:instagram:Breite 2160" in text


def test_platform_limits() -> None:
    issues = check_video(Path("c.mp4"), info(duration=11 * 60, size=400 * config.MB), ALL)
    text = messages(issues)
    assert "error:tiktok:Video ist 660 s lang" in text
    assert "error:instagram:Datei ist 400.0 MB" in text
    assert "warning:youtube:Länger als 3 Minuten" in text
    assert "error:youtube" not in text


def test_text_limits() -> None:
    meta = PostMeta(title="x" * 101 + "<", description="ä" * 2600,
                    hashtags=[f"tag{i}" for i in range(31)])
    text = messages(check_texts(meta, ALL))
    assert "error:youtube:Titel hat 102 Zeichen" in text
    assert "error:youtube:Titel darf kein <" in text
    assert "error:youtube:Beschreibung hat" in text
    assert "error:instagram:Caption hat" in text
    assert "error:instagram:Mehr als 30 Hashtags" in text
    assert "error:tiktok:Caption hat" in text


def test_utf16_counts_emoji_twice() -> None:
    assert utf16_len("a😀") == 3


def test_faststart_copy_leaves_original(vertical_video: Path) -> None:
    before = vertical_video.read_bytes()
    copy = faststart_copy(vertical_video, "f" * 64)
    assert copy.exists() and copy != vertical_video
    assert vertical_video.read_bytes() == before
    # moov-Atom steht nach dem Remux vor mdat
    head = copy.read_bytes()
    assert head.index(b"moov") < head.index(b"mdat")
