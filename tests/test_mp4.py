from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from terminal import mp4
from terminal.validate import faststart_copy, probe, video_info

DATA = Path(__file__).parent / "data"
TINY = DATA / "tiny.mp4"  # 144×256, 30 fps, 1 s, H.264 + AAC, moov am Ende


def test_probe_without_ffmpeg() -> None:
    info = mp4.probe(TINY)
    assert info.video is not None and info.audio is not None
    assert (info.video.codec, info.audio.codec) == ("h264", "aac")
    assert (info.video.width, info.video.height, info.video.rotation) == (144, 256, 0)
    assert info.video.samples == 30
    assert 0.99 < info.duration < 1.1


def test_rotation_is_applied() -> None:
    vi = video_info(DATA / "tiny_rotated.mp4")
    assert (vi.width, vi.height) == (256, 144)


def test_not_an_mp4(tmp_path: Path) -> None:
    junk = tmp_path / "x.mp4"
    junk.write_bytes(b"\x00\x00\x00\x08free" + b"x" * 100)
    with pytest.raises(mp4.Mp4Error):
        mp4.probe(junk)


def test_faststart_moves_moov_and_keeps_original(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    shutil.copy(TINY, src)
    before = src.read_bytes()
    assert mp4.needs_faststart(src)
    out = faststart_copy(src, "a" * 64)
    assert out != src and src.read_bytes() == before
    data = out.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")
    assert not mp4.needs_faststart(out)
    assert faststart_copy(out, "b" * 64) == out  # schon faststart: keine Kopie


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg nicht installiert")
@pytest.mark.parametrize("name", ["tiny.mp4", "tiny_rotated.mp4"])
def test_matches_ffprobe_and_decodes_identically(tmp_path: Path, name: str) -> None:
    src = DATA / name
    ours = video_info(src)
    ref = video_info(src, probe(src))
    assert (ours.codec, ours.width, ours.height, ours.fps, ours.audio_codec) == \
        (ref.codec, ref.width, ref.height, ref.fps, ref.audio_codec)
    assert abs(ours.duration - ref.duration) < 0.01

    out = tmp_path / "out.mp4"
    mp4.faststart(src, out)

    def frames(p: Path) -> list[str]:
        result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-map", "0",
                                 "-f", "framemd5", "-"], capture_output=True, text=True,
                                check=True)
        assert result.stderr == ""
        return [line for line in result.stdout.splitlines() if not line.startswith("#")]

    assert frames(out) == frames(src)
