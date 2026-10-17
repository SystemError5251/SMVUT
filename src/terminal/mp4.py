"""MP4/MOV lesen und „faststart“ umsortieren – in reinem Python, ohne ffmpeg.

Auf Android (APK) gibt es kein ffmpeg, und in Termux bricht es gern nach Updates.
Gelesen wird nur, was die Prüfung braucht: Codec, Auflösung, Drehung, Bildrate,
Dauer, Bitrate und Audio-Codec.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts"}
CODECS = {b"avc1": "h264", b"avc3": "h264", b"hvc1": "hevc", b"hev1": "hevc",
          b"mp4a": "aac", b"av01": "av1", b"vp09": "vp9", b"vp08": "vp8",
          b"Opus": "opus", b"ac-3": "ac3", b"ec-3": "eac3"}


class Mp4Error(Exception):
    """Datei ist kein lesbares MP4/MOV."""


@dataclass
class Box:
    type: bytes
    start: int  # Position des Box-Headers
    header: int  # Länge des Headers (8 oder 16)
    size: int  # Gesamtgröße inkl. Header

    @property
    def body(self) -> int:
        return self.start + self.header

    @property
    def end(self) -> int:
        return self.start + self.size


def _boxes(f: BinaryIO, start: int, end: int) -> Iterator[Box]:
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        header = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise Mp4Error(f"beschädigte Box {kind!r} bei Byte {pos}")
        yield Box(kind, pos, header, size)
        pos += size


def _child(f: BinaryIO, box: Box, kind: bytes) -> Box | None:
    return next((b for b in _boxes(f, box.body, box.end) if b.type == kind), None)


def _read(f: BinaryIO, box: Box) -> bytes:
    f.seek(box.body)
    return f.read(box.size - box.header)


@dataclass
class Track:
    handler: bytes = b""
    codec: str = "?"
    width: float = 0.0
    height: float = 0.0
    rotation: int = 0
    timescale: int = 0
    duration: int = 0
    samples: int = 0
    payload: int = 0  # Summe der Sample-Größen in Bytes

    @property
    def seconds(self) -> float:
        return self.duration / self.timescale if self.timescale else 0.0


def _tkhd(data: bytes, track: Track) -> None:
    version = data[0]
    offset = 4 + (32 if version == 1 else 20) + 8 + 8  # Zeiten, IDs, reserved, layer …
    matrix = struct.unpack(">9i", data[offset:offset + 36])
    width, height = struct.unpack(">II", data[offset + 36:offset + 44])
    track.width, track.height = width / 65536, height / 65536
    a, b = matrix[0] / 65536, matrix[1] / 65536
    track.rotation = round(math.degrees(math.atan2(b, a))) % 360


def _mdhd(data: bytes, track: Track) -> None:
    if data[0] == 1:
        track.timescale, track.duration = struct.unpack(">IQ", data[20:32])
    else:
        track.timescale, track.duration = struct.unpack(">II", data[12:20])


def _stsz(data: bytes, track: Track) -> None:
    sample_size, count = struct.unpack(">II", data[4:12])
    track.samples = count
    if sample_size:
        track.payload = sample_size * count
    else:
        sizes = struct.unpack(f">{count}I", data[12:12 + 4 * count])
        track.payload = sum(sizes)


def _track(f: BinaryIO, trak: Box) -> Track:
    track = Track()
    tkhd = _child(f, trak, b"tkhd")
    if tkhd:
        _tkhd(_read(f, tkhd), track)
    mdia = _child(f, trak, b"mdia")
    if not mdia:
        return track
    for box in _boxes(f, mdia.body, mdia.end):
        if box.type == b"mdhd":
            _mdhd(_read(f, box), track)
        elif box.type == b"hdlr":
            track.handler = _read(f, box)[8:12]
    minf = _child(f, mdia, b"minf")
    stbl = _child(f, minf, b"stbl") if minf else None
    if stbl:
        stsd = _child(f, stbl, b"stsd")
        if stsd:
            data = _read(f, stsd)
            if len(data) >= 16:
                fourcc = data[12:16]
                track.codec = CODECS.get(fourcc, fourcc.decode("latin-1").strip().lower())
        stsz = _child(f, stbl, b"stsz")
        if stsz:
            _stsz(_read(f, stsz), track)
    return track


@dataclass
class Mp4Info:
    duration: float
    size: int
    video: Track | None
    audio: Track | None


def probe(path: Path) -> Mp4Info:
    size = path.stat().st_size
    with path.open("rb") as f:
        top = list(_boxes(f, 0, size))
        moov = next((b for b in top if b.type == b"moov"), None)
        if moov is None:
            raise Mp4Error(f"{path.name}: kein MP4/MOV (moov fehlt)")
        duration = 0.0
        tracks: list[Track] = []
        for box in _boxes(f, moov.body, moov.end):
            if box.type == b"mvhd":
                data = _read(f, box)
                if data[0] == 1:
                    scale, dur = struct.unpack(">IQ", data[20:32])
                else:
                    scale, dur = struct.unpack(">II", data[12:20])
                duration = dur / scale if scale else 0.0
            elif box.type == b"trak":
                tracks.append(_track(f, box))
    video = next((t for t in tracks if t.handler == b"vide"), None)
    audio = next((t for t in tracks if t.handler == b"soun"), None)
    return Mp4Info(duration=duration or (video.seconds if video else 0.0), size=size,
                   video=video, audio=audio)


# --- faststart -------------------------------------------------------------
def needs_faststart(path: Path) -> bool:
    size = path.stat().st_size
    with path.open("rb") as f:
        order = [b.type for b in _boxes(f, 0, size) if b.type in (b"moov", b"mdat")]
    if b"moov" not in order:
        raise Mp4Error(f"{path.name}: kein MP4/MOV (moov fehlt)")
    return b"mdat" in order and order.index(b"mdat") < order.index(b"moov")


def _shift_offsets(moov: bytearray, shift: int) -> None:
    """Verschiebt alle Chunk-Offsets (stco/co64) im moov-Baum um `shift` Bytes."""
    def walk(start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            size, kind = struct.unpack(">I4s", moov[pos:pos + 8])
            header = 8
            if size == 1:
                size = struct.unpack(">Q", moov[pos + 8:pos + 16])[0]
                header = 16
            elif size == 0:
                size = end - pos
            if size < header:
                raise Mp4Error("beschädigtes moov")
            body = pos + header
            if kind in CONTAINERS:
                walk(body, pos + size)
            elif kind == b"stco":
                count = struct.unpack(">I", moov[body + 4:body + 8])[0]
                for i in range(count):
                    at = body + 8 + 4 * i
                    value = struct.unpack(">I", moov[at:at + 4])[0] + shift
                    if value > 0xFFFFFFFF:
                        raise Mp4Error("Video zu groß für faststart (über 4 GB)")
                    moov[at:at + 4] = struct.pack(">I", value)
            elif kind == b"co64":
                count = struct.unpack(">I", moov[body + 4:body + 8])[0]
                for i in range(count):
                    at = body + 8 + 8 * i
                    value = struct.unpack(">Q", moov[at:at + 8])[0] + shift
                    moov[at:at + 8] = struct.pack(">Q", value)
            pos += size
    walk(0, len(moov))


def faststart(source: Path, target: Path) -> None:
    """Schreibt `source` nach `target` mit dem moov-Atom vor den Mediendaten."""
    size = source.stat().st_size
    with source.open("rb") as f:
        top = list(_boxes(f, 0, size))
        moov_box = next((b for b in top if b.type == b"moov"), None)
        if moov_box is None:
            raise Mp4Error(f"{source.name}: kein MP4/MOV (moov fehlt)")
        f.seek(moov_box.start)
        moov = bytearray(f.read(moov_box.size))
        # moov kommt direkt hinter ftyp; alles danach rutscht um seine Größe nach hinten
        rest = [b for b in top if b.type != b"moov"]
        head = [b for b in rest if b.type == b"ftyp"]
        tail = [b for b in rest if b.type != b"ftyp"]
        insert_at = head[-1].end if head else 0
        shift = moov_box.size if any(
            b.start >= insert_at and b.start < moov_box.start for b in tail) else 0
        _shift_offsets(moov, shift)
        with target.open("wb") as out:
            for box in head:
                f.seek(box.start)
                out.write(f.read(box.size))
            out.write(moov)
            for box in tail:
                f.seek(box.start)
                remaining = box.size
                while remaining:
                    chunk = f.read(min(remaining, 8 * 1024 * 1024))
                    if not chunk:
                        raise Mp4Error("Datei endet unerwartet")
                    out.write(chunk)
                    remaining -= len(chunk)


