"""Upload-Historie in SQLite: eine Zeile pro Video und Plattform."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from terminal import config

# Status-Werte
UPLOADING = "uploading"
CONTAINER_CREATED = "container_created"  # Instagram: Container-ID gesichert, noch nicht publiziert
PROCESSING = "processing"  # TikTok: Upload fertig, Plattform verarbeitet noch
PUBLISHED = "published"
DRAFT_SENT = "draft_sent"  # TikTok: Entwurf im Posteingang
FAILED = "failed"

DONE = {PUBLISHED, DRAFT_SENT}

STATUS_LABELS = {
    UPLOADING: "lädt hoch",
    CONTAINER_CREATED: "Container angelegt",
    PROCESSING: "wird verarbeitet",
    PUBLISHED: "veröffentlicht",
    DRAFT_SENT: "Entwurf gesendet",
    FAILED: "fehlgeschlagen",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256      TEXT NOT NULL,
    platform    TEXT NOT NULL,
    video       TEXT NOT NULL,
    status      TEXT NOT NULL,
    remote_id   TEXT,
    link        TEXT,
    error       TEXT,
    extra       TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (sha256, platform)
);
"""


@dataclass
class Entry:
    sha256: str
    platform: str
    video: str
    status: str
    remote_id: str | None = None
    link: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    @property
    def done(self) -> bool:
        return self.status in DONE


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(config.MB), b""):
            digest.update(block)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class History:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.history_path()
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def get(self, sha256: str, platform: str) -> Entry | None:
        row = self.db.execute("SELECT * FROM uploads WHERE sha256 = ? AND platform = ?",
                              (sha256, platform)).fetchone()
        return self._entry(row) if row else None

    def save(self, sha256: str, platform: str, video: str, status: str, *,
             remote_id: str | None = None, link: str | None = None,
             error: str | None = None, extra: dict[str, Any] | None = None) -> Entry:
        """Legt die Zeile an oder aktualisiert sie; nicht übergebene Felder bleiben erhalten."""
        now = _now()
        old = self.get(sha256, platform)
        merged_extra = {**(old.extra if old else {}), **(extra or {})}
        if old is None:
            self.db.execute(
                "INSERT INTO uploads (sha256, platform, video, status, remote_id, link, error,"
                " extra, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sha256, platform, video, status, remote_id, link, error,
                 json.dumps(merged_extra), now, now))
        else:
            self.db.execute(
                "UPDATE uploads SET video = ?, status = ?, remote_id = ?, link = ?, error = ?,"
                " extra = ?, updated_at = ? WHERE sha256 = ? AND platform = ?",
                (video, status,
                 remote_id if remote_id is not None else old.remote_id,
                 link if link is not None else old.link,
                 error,  # ein neuer Status löscht einen alten Fehlertext
                 json.dumps(merged_extra), now, sha256, platform))
        self.db.commit()
        entry = self.get(sha256, platform)
        assert entry is not None
        return entry

    def reset(self, sha256: str, platform: str) -> None:
        self.db.execute("DELETE FROM uploads WHERE sha256 = ? AND platform = ?",
                        (sha256, platform))
        self.db.commit()

    def recent(self, limit: int = 20) -> list[Entry]:
        rows = self.db.execute("SELECT * FROM uploads ORDER BY updated_at DESC, id DESC LIMIT ?",
                               (limit,)).fetchall()
        return [self._entry(r) for r in rows]

    @staticmethod
    def _entry(row: sqlite3.Row) -> Entry:
        data = dict(row)
        data.pop("id", None)
        data["extra"] = json.loads(data.get("extra") or "{}")
        return Entry(**data)
