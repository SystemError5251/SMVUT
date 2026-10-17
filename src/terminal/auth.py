"""Anmeldung und Token-Verwaltung für YouTube, Instagram und TikTok.

Tokens liegen im Schlüsselbund des Betriebssystems (keyring). Android/Termux hat
keinen Schlüsselbund: Dort liegen sie in ~/.terminal/tokens.json (nur für den
Termux-Nutzer lesbar, Modus 600).
"""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from terminal import config
from terminal.errors import AuthRequired, ConfigError, TerminalError

KEYRING_SERVICE = config.APP_NAME


# --- Token-Speicher --------------------------------------------------------
class TokenStore:
    """Schlüsselbund (keyring) oder, auf Termux, eine Datei mit Modus 600."""

    def __init__(self, backend: str | None = None) -> None:
        self.backend = backend or config.token_backend()

    def load(self, platform: str) -> dict[str, Any] | None:
        if self.backend == "file":
            data = self._read_file().get(platform)
            return data if isinstance(data, dict) else None
        keyring, errors = _keyring()
        try:
            raw = keyring.get_password(KEYRING_SERVICE, platform)
        except errors.KeyringError as exc:
            raise ConfigError(_keyring_hint(exc)) from exc
        return json.loads(raw) if raw else None

    def save(self, platform: str, data: dict[str, Any]) -> None:
        if self.backend == "file":
            tokens = self._read_file()
            tokens[platform] = data
            self._write_file(tokens)
            return
        keyring, errors = _keyring()
        try:
            keyring.set_password(KEYRING_SERVICE, platform, json.dumps(data))
        except errors.KeyringError as exc:
            raise ConfigError(_keyring_hint(exc)) from exc

    def delete(self, platform: str) -> None:
        if self.backend == "file":
            tokens = self._read_file()
            if tokens.pop(platform, None) is not None:
                self._write_file(tokens)
            return
        keyring, errors = _keyring()
        with contextlib.suppress(errors.PasswordDeleteError):
            keyring.delete_password(KEYRING_SERVICE, platform)

    @staticmethod
    def _read_file() -> dict[str, Any]:
        path = config.token_file()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError(f"{path} ist beschädigt – löschen und neu anmelden") from exc
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_file(tokens: dict[str, Any]) -> None:
        path = config.token_file()
        tmp = path.with_suffix(".tmp")
        # Datei von Anfang an nur für den eigenen Nutzer lesbar anlegen
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(tokens, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)


def _keyring() -> tuple[Any, Any]:
    try:
        import keyring
        import keyring.errors
    except ImportError as exc:
        raise ConfigError("keyring ist nicht installiert – `pip install keyring` oder "
                          "TERMINAL_TOKEN_STORE=file in die .env schreiben") from exc
    return keyring, keyring.errors


def _keyring_hint(exc: Exception) -> str:
    return (f"Kein Schlüsselbund verfügbar ({type(exc).__name__}). Unter Linux ohne Desktop "
            "gnome-keyring/KWallet einrichten oder TERMINAL_TOKEN_STORE=file in die .env "
            "schreiben (Tokens dann in ~/.terminal/tokens.json, nur für dich lesbar).")


def _now() -> float:
    return time.time()


@dataclass
class TokenInfo:
    platform: str
    logged_in: bool
    detail: str
    expires_at: float | None = None

    @property
    def expiring_soon(self) -> bool:
        return self.expires_at is not None and self.expires_at - _now() < config.EXPIRY_WARNING


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


# --- Hilfen für JSON-APIs --------------------------------------------------
def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


# --- YouTube ---------------------------------------------------------------
def youtube_login(store: TokenStore) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    path = config.find_file(config.require_env("YOUTUBE_CLIENT_SECRETS"))
    try:
        flow = InstalledAppFlow.from_client_secrets_file(path, config.YOUTUBE_SCOPES)
    except FileNotFoundError as exc:
        raise ConfigError(f"OAuth-Client-Datei nicht gefunden: {path}") from exc
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline",
                                  authorization_prompt_message="Browser öffnet sich für den "
                                                               "Google-Login: {url}",
                                  success_message="Login erfolgreich – du kannst das "
                                                  "Fenster schließen.")
    store.save("youtube", json.loads(creds.to_json()))
    return "YouTube-Login gespeichert"


def youtube_credentials(store: TokenStore) -> Any:
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    data = store.load("youtube")
    if not data:
        raise AuthRequired("youtube", "Nicht bei YouTube angemeldet")
    creds = Credentials.from_authorized_user_info(  # type: ignore[no-untyped-call]
        data, config.YOUTUBE_SCOPES)
    expiry = creds.expiry.replace(tzinfo=UTC) if creds.expiry else None
    soon = expiry is None or expiry - datetime.now(UTC) < timedelta(
        seconds=config.TOKEN_REFRESH_MARGIN)
    if soon:
        if not creds.refresh_token:
            raise AuthRequired("youtube", "Kein Refresh Token vorhanden")
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AuthRequired("youtube", "Google hat die Anmeldung abgelehnt "
                                          "(im Status „Testing“ gilt sie nur 7 Tage)") from exc
        store.save("youtube", json.loads(creds.to_json()))
    return creds


# --- Instagram (Instagram Login) ------------------------------------------
def _instagram_error(response: httpx.Response) -> str:
    error = _json(response).get("error", {})
    if isinstance(error, dict):
        return str(error.get("message") or f"HTTP {response.status_code}")
    return f"HTTP {response.status_code}"


def instagram_login(store: TokenStore, token: str, client: httpx.Client) -> str:
    """Übernimmt einen im App-Dashboard erzeugten Token, tauscht ihn bei Bedarf und liest die ID."""
    token = token.strip()
    expires_in = config.INSTAGRAM_TOKEN_LIFETIME  # Dashboard-Tokens sind meist schon langlebig
    secret = config.env("INSTAGRAM_APP_SECRET")
    if secret:
        r = client.get(f"{config.INSTAGRAM_TOKEN_BASE}/access_token", params={
            "grant_type": "ig_exchange_token", "client_secret": secret, "access_token": token})
        if r.status_code == 200 and "access_token" in _json(r):
            data = _json(r)
            token = data["access_token"]
            expires_in = int(data.get("expires_in", expires_in))
        # sonst: Token ist bereits langlebig – so übernehmen

    r = client.get(f"{config.INSTAGRAM_GRAPH}/me",
                   params={"fields": "user_id,username", "access_token": token})
    if r.status_code != 200:
        raise AuthRequired("instagram", f"Token ungültig: {_instagram_error(r)}")
    me = _json(r)
    user_id = str(me.get("user_id") or me.get("id"))
    now = _now()
    store.save("instagram", {"access_token": token, "user_id": user_id,
                             "username": me.get("username", ""),
                             "refreshed_at": now, "expires_at": now + expires_in})
    return f"Instagram-Konto @{me.get('username', '?')} gespeichert (ID {user_id})"


def instagram_token(store: TokenStore, client: httpx.Client) -> tuple[str, str]:
    """Liefert (Token, IG-User-ID) und verlängert den Token, sobald er älter als 24 h ist."""
    data = store.load("instagram")
    if not data:
        raise AuthRequired("instagram", "Nicht bei Instagram angemeldet")
    now = _now()
    if now >= data["expires_at"]:
        raise AuthRequired("instagram", "Instagram-Token ist abgelaufen")
    if now - data["refreshed_at"] > config.INSTAGRAM_REFRESH_AFTER:
        r = client.get(f"{config.INSTAGRAM_TOKEN_BASE}/refresh_access_token", params={
            "grant_type": "ig_refresh_token", "access_token": data["access_token"]})
        body = _json(r)
        if r.status_code == 200 and "access_token" in body:
            data.update(access_token=body["access_token"], refreshed_at=now,
                        expires_at=now + int(body.get("expires_in",
                                                      config.INSTAGRAM_TOKEN_LIFETIME)))
            store.save("instagram", data)
        elif r.status_code in (400, 401):
            raise AuthRequired("instagram", f"Verlängern fehlgeschlagen: {_instagram_error(r)}")
        # andere Fehler: alter Token gilt noch, beim nächsten Lauf erneut versuchen
    return data["access_token"], data["user_id"]


# --- TikTok (Login Kit Desktop mit PKCE) -----------------------------------
def pkce_pair() -> tuple[str, str]:
    """TikTok Desktop verlangt die Hex-Kodierung von SHA-256 (nicht base64url)."""
    verifier = secrets.token_urlsafe(72)[:96]  # 43–128 Zeichen aus A-Z a-z 0-9 - _
    challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    return verifier, challenge


def _tiktok_token_request(client: httpx.Client, form: dict[str, str]) -> dict[str, Any]:
    r = client.post(f"{config.TIKTOK_API}/oauth/token/", data=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    body = _json(r)
    if r.status_code != 200 or "access_token" not in body:
        detail = body.get("error_description") or body.get("error") or f"HTTP {r.status_code}"
        raise AuthRequired("tiktok", f"TikTok-Token abgelehnt: {detail}")
    return body


def _store_tiktok(store: TokenStore, body: dict[str, Any],
                  old: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _now()
    data = {
        "access_token": body["access_token"],
        "expires_at": now + int(body.get("expires_in", 86400)),
        # einen neu gelieferten Refresh Token immer übernehmen
        "refresh_token": body.get("refresh_token") or (old or {}).get("refresh_token"),
        "refresh_expires_at": now + int(body["refresh_expires_in"])
        if "refresh_expires_in" in body else (old or {}).get("refresh_expires_at"),
        "open_id": body.get("open_id") or (old or {}).get("open_id"),
        "scope": body.get("scope") or (old or {}).get("scope", ""),
    }
    store.save("tiktok", data)
    return data


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    expected_path = "/"

    def do_GET(self) -> None:  # noqa: N802 – Name von BaseHTTPRequestHandler vorgegeben
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") != self.expected_path.rstrip("/"):
            self.send_response(404)
            self.end_headers()
            return
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        type(self).result.update(query)
        ok = "code" in query
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        text = ("Login erfolgreich – du kannst das Fenster schließen." if ok
                else "Login fehlgeschlagen – Details stehen im Terminal.")
        self.wfile.write(f"<html><body><p>{text}</p></body></html>".encode())

    def log_message(self, format: str, *args: Any) -> None:  # keine Query-Strings loggen
        return


def tiktok_login(store: TokenStore, client: httpx.Client, timeout: float = 300) -> str:
    client_key = config.require_env("TIKTOK_CLIENT_KEY")
    client_secret = config.require_env("TIKTOK_CLIENT_SECRET")
    redirect_uri = config.env("TIKTOK_REDIRECT_URI", "http://localhost:3455/callback/") or ""
    target = urllib.parse.urlparse(redirect_uri)
    if target.hostname not in ("localhost", "127.0.0.1") or not target.port:
        raise ConfigError("TIKTOK_REDIRECT_URI muss http://localhost:<port>/… sein")

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = config.TIKTOK_AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_key": client_key, "scope": config.TIKTOK_SCOPES, "response_type": "code",
        "redirect_uri": redirect_uri, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})

    class Handler(_CallbackHandler):
        result: dict[str, str] = {}
        expected_path = target.path or "/"

    handler = Handler
    server = http.server.HTTPServer((target.hostname, target.port), handler)
    server.timeout = 1
    print(f"Browser öffnet sich für den TikTok-Login. Falls nicht, diese Adresse öffnen:\n{url}")
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    deadline = _now() + timeout
    try:
        while not handler.result and _now() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    result = handler.result
    if not result:
        raise TerminalError("Kein Login innerhalb von 5 Minuten – bitte erneut versuchen")
    if result.get("state") != state:
        raise TerminalError("TikTok-Login abgebrochen: state stimmt nicht überein")
    if "code" not in result:
        raise AuthRequired("tiktok", f"TikTok-Login fehlgeschlagen: "
                                     f"{result.get('error_description') or result.get('error')}")

    body = _tiktok_token_request(client, {
        "client_key": client_key, "client_secret": client_secret, "code": result["code"],
        "grant_type": "authorization_code", "redirect_uri": redirect_uri,
        "code_verifier": verifier})
    data = _store_tiktok(store, body)
    if "video.upload" not in data["scope"]:
        raise AuthRequired("tiktok", "Scope video.upload wurde nicht freigegeben")
    return "TikTok-Login gespeichert"


def tiktok_token(store: TokenStore, client: httpx.Client, force_refresh: bool = False) -> str:
    data = store.load("tiktok")
    if not data:
        raise AuthRequired("tiktok", "Nicht bei TikTok angemeldet")
    if not force_refresh and data["expires_at"] - _now() > config.TOKEN_REFRESH_MARGIN:
        return str(data["access_token"])
    if not data.get("refresh_token") or (data.get("refresh_expires_at") or 0) < _now():
        raise AuthRequired("tiktok", "TikTok-Anmeldung abgelaufen")
    body = _tiktok_token_request(client, {
        "client_key": config.require_env("TIKTOK_CLIENT_KEY"),
        "client_secret": config.require_env("TIKTOK_CLIENT_SECRET"),
        "grant_type": "refresh_token", "refresh_token": data["refresh_token"]})
    return str(_store_tiktok(store, body, old=data)["access_token"])


# --- Übersicht für `terminal auth --check` ---------------------------------
def token_overview(store: TokenStore) -> list[TokenInfo]:
    infos: list[TokenInfo] = []
    yt = store.load("youtube")
    if yt:
        detail = "angemeldet" + ("" if yt.get("refresh_token") else ", ohne Refresh Token!")
        infos.append(TokenInfo("youtube", bool(yt.get("refresh_token")),
                               detail + " (im Google-Status „Testing“ nur 7 Tage gültig)"))
    else:
        infos.append(TokenInfo("youtube", False, "nicht angemeldet"))

    ig = store.load("instagram")
    if ig:
        valid = ig["expires_at"] > _now()
        infos.append(TokenInfo("instagram", valid,
                               f"@{ig.get('username', '?')}, gültig bis {_fmt_ts(ig['expires_at'])}"
                               " (verlängert sich bei jeder Nutzung)", ig["expires_at"]))
    else:
        infos.append(TokenInfo("instagram", False, "nicht angemeldet"))

    tt = store.load("tiktok")
    if tt:
        until = tt.get("refresh_expires_at")
        valid = bool(until and until > _now())
        infos.append(TokenInfo("tiktok", valid, f"angemeldet, Refresh Token gültig bis "
                                                f"{_fmt_ts(until) if until else '?'}", until))
    else:
        infos.append(TokenInfo("tiktok", False, "nicht angemeldet"))
    return infos
