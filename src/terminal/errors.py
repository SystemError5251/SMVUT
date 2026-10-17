"""Fehlerklassen nach der Tabelle „Fehlerbehandlung“ im Konzept."""

from __future__ import annotations

import re

_SECRET_PATTERNS = [
    (re.compile(r"\b(access_token|client_secret|refresh_token|code_verifier|code)=[^&\s\"']+"),
     r"\1=***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-~+/=]+"), r"\1***"),
    (re.compile(r"(X-Amz-[A-Za-z-]+)=[^&\s\"']+"), r"\1=***"),
]


def redact(text: str) -> str:
    """Entfernt Tokens und Signaturen aus Texten, bevor sie angezeigt oder geloggt werden."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class TerminalError(Exception):
    """Basisklasse: alles, was das Programm mit klarer Meldung beendet."""

    def __init__(self, message: str) -> None:
        super().__init__(redact(message))


class ConfigError(TerminalError):
    """Fehlende .env-Einträge oder Dateien."""


class MetaError(TerminalError):
    """YAML-Datei fehlt oder ist ungültig."""


class AuthRequired(TerminalError):
    """Token fehlt, ist abgelaufen oder widerrufen."""

    def __init__(self, platform: str, detail: str = "") -> None:
        hint = f"Bitte `smvut auth {platform}` ausführen"
        super().__init__(f"{detail} – {hint}" if detail else hint)
        self.platform = platform


class QuotaExceeded(TerminalError):
    """Tageslimit erreicht: kein Retry, morgen erneut."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Tageslimit erreicht, morgen erneut versuchen ({detail})")


class UploadError(TerminalError):
    """Endgültiger Fehler: kein Retry, Fehlertext der API anzeigen."""


class TransientApiError(TerminalError):
    """Vorübergehender API-Fehler im Antwort-Body (z. B. TikTok internal_error)."""


class UploadTimeout(TerminalError):
    """Plattform verarbeitet noch; ein erneuter `post` setzt fort."""
