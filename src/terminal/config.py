"""Endpunkte, API-Versionen, Limits und Pfade an einer Stelle."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv, set_key, unset_key

APP_NAME = "terminal"
PLATFORMS = ("youtube", "instagram", "tiktok")

# --- Endpunkte -------------------------------------------------------------
INSTAGRAM_API_VERSION = "v25.0"
INSTAGRAM_GRAPH = f"https://graph.instagram.com/{INSTAGRAM_API_VERSION}"
INSTAGRAM_TOKEN_BASE = "https://graph.instagram.com"  # Token-Tausch ohne Version

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

TIKTOK_AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_API = "https://open.tiktokapis.com/v2"
TIKTOK_SCOPES = "user.info.basic,video.upload"

# --- Upload-Parameter ------------------------------------------------------
MB = 1024 * 1024
YOUTUBE_CHUNK_SIZE = 8 * MB
TIKTOK_CHUNK_SIZE = 10 * MB  # erlaubt: 5–64 MB, letzter Chunk bis 128 MB
INSTAGRAM_POLL_INTERVAL = 60  # Meta empfiehlt einmal pro Minute …
INSTAGRAM_POLL_ATTEMPTS = 5  # … höchstens 5 Minuten
TIKTOK_POLL_INTERVAL = 10  # Limit 30 Abfragen pro Minute
TIKTOK_POLL_ATTEMPTS = 30
PRESIGNED_URL_TTL = 3600

TOKEN_REFRESH_MARGIN = 3600  # erneuern, wenn weniger als 1 h gültig
INSTAGRAM_REFRESH_AFTER = 24 * 3600  # Instagram-Token frühestens nach 24 h verlängerbar
INSTAGRAM_TOKEN_LIFETIME = 60 * 24 * 3600
EXPIRY_WARNING = 7 * 24 * 3600


# --- Plattform-Limits (Plattform-Überblick im Konzept) ---------------------
@dataclass(frozen=True)
class Limits:
    min_duration: float
    max_duration: float
    max_size: int


LIMITS = {
    "instagram": Limits(min_duration=3, max_duration=15 * 60, max_size=300 * MB),
    "youtube": Limits(min_duration=0, max_duration=12 * 3600, max_size=256 * 1024 * MB),
    "tiktok": Limits(min_duration=0, max_duration=10 * 60, max_size=4 * 1024 * MB),
}
ALLOWED_CODECS = {"h264", "hevc"}
MIN_FPS, MAX_FPS = 23.0, 60.0
SHORTS_MAX_DURATION = 3 * 60
INSTAGRAM_MAX_BITRATE = 25_000_000
INSTAGRAM_MAX_WIDTH = 1920
TIKTOK_MIN_SIDE, TIKTOK_MAX_SIDE = 360, 4096
CAPTION_MAX = 2200
INSTAGRAM_MAX_HASHTAGS = 30
INSTAGRAM_MAX_MENTIONS = 20
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESCRIPTION_MAX_BYTES = 5000
YOUTUBE_TAGS_MAX = 500


# --- Pfade -----------------------------------------------------------------
def home() -> Path:
    path = Path(os.environ.get("TERMINAL_HOME", Path.home() / f".{APP_NAME}"))
    if not path.is_dir():
        path.mkdir(parents=True, exist_ok=True)
        # nur für den eigenen Nutzer: Tokens (Termux), Historie, Log, Cache
        # (Termux legt Dateien sonst mit umask 000 an)
        path.chmod(0o700)
    return path


def cache_dir() -> Path:
    path = home() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def history_path() -> Path:
    return home() / "history.db"


def log_path() -> Path:
    return home() / f"{APP_NAME}.log"


def token_file() -> Path:
    return home() / "tokens.json"


# --- Android / Termux ------------------------------------------------------
def is_termux() -> bool:
    return "TERMUX_VERSION" in os.environ or "com.termux" in os.environ.get("PREFIX", "")


def is_android() -> bool:
    """Termux oder die Android-App (APK)."""
    return sys.platform == "android" or is_termux()


def token_backend() -> str:
    """keyring auf Desktop-Systemen, Datei auf Android (dort gibt es keinen Schlüsselbund)."""
    choice = os.environ.get("TERMINAL_TOKEN_STORE", "").strip().lower()
    if choice in ("keyring", "file"):
        return choice
    return "file" if is_android() else "keyring"


def setup_browser() -> None:
    """Auf Termux öffnet termux-open-url den Login im Android-Browser."""
    if is_termux() and "BROWSER" not in os.environ and shutil.which("termux-open-url"):
        os.environ["BROWSER"] = "termux-open-url"


def env_file() -> Path:
    return home() / ".env"


def load_env() -> None:
    """Liest .env aus dem aktuellen Ordner und aus ~/.terminal/.env."""
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(env_file())


def read_env_file() -> dict[str, str]:
    path = env_file()
    if not path.exists():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def write_env_file(values: dict[str, str]) -> None:
    """Speichert Einstellungen in ~/.terminal/.env (Modus 600) und übernimmt sie sofort."""
    path = env_file()
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    path.chmod(0o600)
    for key, value in values.items():
        value = value.strip()
        if value:
            set_key(path, key, value, quote_mode="always")
            os.environ[key] = value
        else:
            unset_key(path, key, quote_mode="always")
            os.environ.pop(key, None)


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value or default


def find_file(value: str) -> Path:
    """Relative Pfade aus der .env: erst im aktuellen Ordner, dann in ~/.terminal/."""
    path = Path(value).expanduser()
    if path.is_absolute() or path.exists():
        return path
    return home() / path


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        from terminal.errors import ConfigError

        raise ConfigError(f"{name} fehlt in der .env (Vorlage: .env.example)")
    return value
