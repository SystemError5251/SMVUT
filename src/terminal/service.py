"""Ablauf eines Posts – gemeinsam für Kommandozeile (cli.py) und Android-App."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import httpx

from terminal import config, platforms
from terminal.auth import TokenStore, token_overview
from terminal.errors import TerminalError, UploadTimeout, redact
from terminal.history import FAILED, STATUS_LABELS, Entry, History
from terminal.meta import PostMeta
from terminal.platforms.base import UploadContext, Uploader, UploadResult
from terminal.validate import Issue, VideoInfo, check_all

log = logging.getLogger(config.APP_NAME)

FROM_HISTORY = "aus der Historie"


def setup_logging() -> None:
    """Nur eigene Meldungen ins Log: keine Header, keine Request-Inhalte."""
    if log.handlers:
        return
    handler = logging.FileHandler(config.log_path(), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

ProgressFn = Callable[[str, int, int], None]  # (Plattform, erledigt, gesamt)
LogFn = Callable[[str, str], None]  # (Plattform, Meldung)


def preflight(video: Path, meta: PostMeta, targets: list[str], store: TokenStore,
              ) -> tuple[VideoInfo, list[Issue], dict[str, Uploader]]:
    """Prüft Video, Texte, Anmeldungen und Einstellungen, bevor etwas hochgeladen wird."""
    info, issues = check_all(video, meta, targets)
    uploaders = {name: platforms.create(name, store) for name in targets}
    for uploader in uploaders.values():
        issues += uploader.check(video, meta, info)
    for token in token_overview(store):
        if token.platform in targets and token.logged_in and token.expiring_soon:
            issues.append(Issue("warning", token.platform,
                                f"Anmeldung läuft bald ab: {token.detail}"))
    return info, issues, uploaders


def post(video: Path, meta: PostMeta, uploaders: dict[str, Uploader], history: History,
         sha: str, *, force: bool = False,
         on_start: Callable[[str], None] = lambda name: None,
         progress: ProgressFn = lambda name, done, total: None,
         message: LogFn = lambda name, msg: None) -> list[UploadResult]:
    """Lädt nacheinander und unabhängig hoch; Erledigtes aus der Historie wird übersprungen."""
    results: list[UploadResult] = []
    for name, uploader in uploaders.items():
        prev = history.get(sha, name)
        if prev and prev.done and not force:
            message(name, f"schon erledigt ({STATUS_LABELS[prev.status]}) – übersprungen")
            results.append(UploadResult(name, prev.status, prev.remote_id, prev.link,
                                        FROM_HISTORY))
            continue
        if prev and force:
            history.reset(sha, name)
            prev = None
        on_start(name)
        results.append(run_upload(name, uploader, video, meta, sha, prev, history,
                                  progress=partial(progress, name),
                                  message=partial(message, name)))
    return results


def run_upload(name: str, uploader: Uploader, video: Path, meta: PostMeta, sha: str,
               prev: Entry | None, history: History, *,
               progress: Callable[[int, int], None],
               message: Callable[[str], None]) -> UploadResult:
    def save(status: str, **kwargs: Any) -> None:
        history.save(sha, name, str(video.resolve()), status, **kwargs)

    ctx = UploadContext(sha256=sha, progress=progress, previous=prev, save=save, log=message)
    try:
        result = uploader.upload(video, meta, ctx)
    except UploadTimeout as exc:
        # Historie behält den Zwischenstand, ein erneuter Post setzt dort fort
        log.warning("%s: %s", name, exc)
        return UploadResult(name, FAILED, note=str(exc))
    except TerminalError as exc:
        save(FAILED, error=str(exc))
        log.error("%s: %s", name, exc)
        return UploadResult(name, FAILED, note=str(exc))
    except (httpx.HTTPError, OSError) as exc:
        text = redact(f"{type(exc).__name__}: {exc}")
        save(FAILED, error=text)
        log.error("%s: %s", name, text)
        return UploadResult(name, FAILED, note=text)
    save(result.status, remote_id=result.remote_id, link=result.link)
    log.info("%s: %s %s", name, result.status, result.remote_id)
    return result
