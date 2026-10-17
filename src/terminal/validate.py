"""Video und Texte prüfen, bevor ein einziges Byte hochgeladen wird."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from terminal import config, mp4
from terminal.errors import ConfigError, UploadError
from terminal.meta import PostMeta


@dataclass(frozen=True)
class Issue:
    level: Literal["error", "warning"]
    platform: str  # "alle" für plattformübergreifende Probleme
    message: str

    @property
    def blocking(self) -> bool:
        return self.level == "error"


@dataclass(frozen=True)
class VideoInfo:
    codec: str
    width: int  # bereits um eine Rotation korrigiert (Anzeige-Ausrichtung)
    height: int
    fps: float
    duration: float
    size: int
    bitrate: int | None
    audio_codec: str | None

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0

    @property
    def is_vertical_or_square(self) -> bool:
        return self.height >= self.width


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise ConfigError(f"{tool} nicht gefunden – bitte ffmpeg installieren")
    return path


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [_require("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise UploadError(f"ffprobe konnte {path.name} nicht lesen: {out.stderr.strip()}")
    result: dict[str, Any] = json.loads(out.stdout)
    return result


def _rotation(stream: dict[str, Any]) -> int:
    rotate = stream.get("tags", {}).get("rotate")
    if rotate is not None:
        return int(float(rotate))
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            return int(float(side["rotation"]))
    return 0


def video_info(path: Path, data: dict[str, Any] | None = None) -> VideoInfo:
    """MP4/MOV liest mp4.py ohne ffmpeg; andere Formate nur, wenn ffprobe installiert ist."""
    if data is None:
        try:
            return _mp4_info(path)
        except mp4.Mp4Error:
            if not shutil.which("ffprobe"):
                raise UploadError(f"{path.name}: kein lesbares MP4/MOV") from None
        data = probe(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise UploadError(f"{path.name} enthält keine Videospur")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if abs(_rotation(video)) % 180 == 90:
        width, height = height, width
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        fps = float(Fraction(rate)) if rate != "0/0" else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    bitrate = video.get("bit_rate") or fmt.get("bit_rate")
    return VideoInfo(
        codec=str(video.get("codec_name", "?")),
        width=width,
        height=height,
        fps=round(fps, 3),
        duration=float(fmt.get("duration") or video.get("duration") or 0),
        size=int(fmt.get("size") or path.stat().st_size),
        bitrate=int(bitrate) if bitrate else None,
        audio_codec=audio.get("codec_name") if audio else None,
    )


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def reencode_hint(video: Path) -> str:
    out = video.with_name(f"{video.stem}-ig.mp4")
    return (f"ffmpeg -i {video.name} -c:v libx264 -preset slow -crf 20 -maxrate 20M "
            f"-bufsize 40M -vf \"scale='min(1080,iw)':-2\" -pix_fmt yuv420p "
            f"-c:a aac -ar 48000 -b:a 128k -movflags +faststart {out.name}")


def _fmt_size(size: int) -> str:
    return f"{size / config.MB:.1f} MB"


def check_video(video: Path, info: VideoInfo, platforms: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    if info.codec not in config.ALLOWED_CODECS:
        issues.append(Issue("error", "alle",
                            f"Video-Codec {info.codec} – erlaubt sind H.264 oder HEVC"))
    if not config.MIN_FPS <= info.fps <= config.MAX_FPS:
        issues.append(Issue("error", "alle",
                            f"Bildrate {info.fps:g} fps – erlaubt sind 23–60 fps"))
    if abs(info.aspect - 9 / 16) > 0.01:
        issues.append(Issue("warning", "alle",
                            f"Seitenverhältnis {info.width}×{info.height} ist nicht 9:16 – "
                            "Instagram schneidet zu"))

    for name in platforms:
        limits = config.LIMITS[name]
        if info.duration < limits.min_duration:
            issues.append(Issue("error", name, f"Video ist {info.duration:.1f} s lang, "
                                               f"mindestens {limits.min_duration:g} s nötig"))
        if info.duration > limits.max_duration:
            issues.append(Issue("error", name, f"Video ist {info.duration:.0f} s lang, "
                                               f"höchstens {limits.max_duration:g} s erlaubt"))
        if info.size > limits.max_size:
            issues.append(Issue("error", name, f"Datei ist {_fmt_size(info.size)} groß, "
                                               f"höchstens {_fmt_size(limits.max_size)}"))

    if "instagram" in platforms:
        if info.bitrate and info.bitrate > config.INSTAGRAM_MAX_BITRATE:
            issues.append(Issue("error", "instagram",
                                f"Bitrate {info.bitrate / 1e6:.1f} Mbit/s über 25 Mbit/s. "
                                f"Neu encodieren:\n    {reencode_hint(video)}"))
        if info.width > config.INSTAGRAM_MAX_WIDTH:
            issues.append(Issue("error", "instagram",
                                f"Breite {info.width} px über 1.920 px. "
                                f"Neu encodieren:\n    {reencode_hint(video)}"))
        if info.audio_codec and info.audio_codec != "aac":
            issues.append(Issue("warning", "instagram",
                                f"Audio-Codec {info.audio_codec} – Instagram erwartet AAC"))

    if "youtube" in platforms:
        if not info.is_vertical_or_square:
            issues.append(Issue("warning", "youtube",
                                "Querformat – wird ein normales Video statt Short"))
        if info.duration > config.SHORTS_MAX_DURATION:
            issues.append(Issue("warning", "youtube",
                                "Länger als 3 Minuten – wird ein normales Video statt Short"))

    if "tiktok" in platforms:
        short, long = sorted((info.width, info.height))
        if short < config.TIKTOK_MIN_SIDE or long > config.TIKTOK_MAX_SIDE:
            issues.append(Issue("error", "tiktok",
                                f"Auflösung {info.width}×{info.height} – erlaubt 360–4096 px"))
    return issues


HASHTAG = re.compile(r"(?<!\w)#\w+")
MENTION = re.compile(r"(?<![\w@])@[\w.]+")


def check_texts(meta: PostMeta, platforms: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    if "youtube" in platforms:
        if len(meta.title) > config.YOUTUBE_TITLE_MAX:
            issues.append(Issue("error", "youtube",
                                f"Titel hat {len(meta.title)} Zeichen, höchstens 100"))
        if "<" in meta.title or ">" in meta.title:
            issues.append(Issue("error", "youtube", "Titel darf kein < oder > enthalten"))
        description = meta.caption_for("youtube")
        size = len(description.encode("utf-8"))
        if size > config.YOUTUBE_DESCRIPTION_MAX_BYTES:
            issues.append(Issue("error", "youtube",
                                f"Beschreibung hat {size} Bytes, höchstens 5.000"))
        if "<" in description or ">" in description:
            issues.append(Issue("error", "youtube", "Beschreibung darf kein < oder > enthalten"))
        tags_len = youtube_tags_length(meta.hashtags)
        if tags_len > config.YOUTUBE_TAGS_MAX:
            issues.append(Issue("error", "youtube",
                                f"Tags zusammen {tags_len} Zeichen, höchstens 500"))

    if "instagram" in platforms:
        caption = meta.caption_for("instagram")
        if len(caption) > config.CAPTION_MAX:
            issues.append(Issue("error", "instagram",
                                f"Caption hat {len(caption)} Zeichen, höchstens 2.200"))
        if len(HASHTAG.findall(caption)) > config.INSTAGRAM_MAX_HASHTAGS:
            issues.append(Issue("error", "instagram", "Mehr als 30 Hashtags in der Caption"))
        if len(MENTION.findall(caption)) > config.INSTAGRAM_MAX_MENTIONS:
            issues.append(Issue("error", "instagram", "Mehr als 20 @-Erwähnungen in der Caption"))

    if "tiktok" in platforms:
        length = utf16_len(meta.caption_for("tiktok"))
        if length > config.CAPTION_MAX:
            issues.append(Issue("error", "tiktok",
                                f"Caption hat {length} UTF-16-Einheiten, höchstens 2.200"))
    return issues


def youtube_tags_length(tags: list[str]) -> int:
    # YouTube zählt Kommas mit und setzt Tags mit Leerzeichen in Anführungszeichen
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


def check_all(video: Path, meta: PostMeta, platforms: list[str],
              info: VideoInfo | None = None) -> tuple[VideoInfo, list[Issue]]:
    if not video.is_file():
        raise UploadError(f"Video nicht gefunden: {video}")
    info = info or video_info(video)
    return info, check_video(video, info, platforms) + check_texts(meta, platforms)


def _mp4_info(path: Path) -> VideoInfo:
    info = mp4.probe(path)
    video = info.video
    if video is None:
        raise UploadError(f"{path.name} enthält keine Videospur")
    width, height = round(video.width), round(video.height)
    if video.rotation % 180 == 90:
        width, height = height, width
    seconds = video.seconds or info.duration
    return VideoInfo(
        codec=video.codec,
        width=width,
        height=height,
        fps=round(video.samples / seconds, 3) if seconds else 0.0,
        duration=info.duration,
        size=info.size,
        bitrate=round(video.payload * 8 / seconds) if seconds else None,
        audio_codec=info.audio.codec if info.audio else None,
    )


def faststart_copy(video: Path, sha256: str) -> Path:
    """Verlustfrei umsortieren, damit das moov-Atom vorne steht. Das Original bleibt
    unangetastet; steht moov schon vorne, wird das Original direkt verwendet."""
    try:
        if not mp4.needs_faststart(video):
            return video
        target = config.cache_dir() / f"{sha256[:16]}-faststart{video.suffix.lower()}"
        if target.exists() and target.stat().st_size == video.stat().st_size:
            return target
        tmp = target.with_name(target.name + ".part")
        mp4.faststart(video, tmp)
    except mp4.Mp4Error as exc:
        raise UploadError(f"faststart nicht möglich: {exc}") from exc
    tmp.replace(target)
    return target
