"""Kommandozeile: terminal check | post | auth | history."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from terminal import __version__, config, platforms
from terminal.auth import (
    TokenStore,
    instagram_login,
    tiktok_login,
    token_overview,
    youtube_login,
)
from terminal.errors import AuthRequired, QuotaExceeded, TerminalError, UploadTimeout, redact
from terminal.history import FAILED, STATUS_LABELS, Entry, History, file_sha256
from terminal.meta import PostMeta, load_meta, meta_path_for
from terminal.platforms.base import UploadContext, Uploader, UploadResult
from terminal.validate import Issue, VideoInfo, check_all

app = typer.Typer(
    help="Terminal – Hochformat-Videos als Instagram Reel, YouTube Short und "
         "TikTok-Entwurf hochladen (nur offizielle APIs).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
log = logging.getLogger(config.APP_NAME)


def _setup() -> None:
    config.load_env()
    config.setup_browser()
    if not log.handlers:
        # Nur eigene Fehlermeldungen ins Log: keine Header, keine Request-Inhalte
        handler = logging.FileHandler(config.log_path(), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False


def _fail(message: str) -> typer.Exit:
    console.print(f"[bold red]Fehler:[/] {escape(redact(message))}")
    log.error(redact(message))
    return typer.Exit(1)


def _parse_only(only: str | None, meta: PostMeta) -> list[str]:
    targets = meta.targets
    if not only:
        return targets
    wanted = [p.strip().lower() for p in only.split(",") if p.strip()]
    unknown = [p for p in wanted if p not in config.PLATFORMS]
    if unknown:
        raise TerminalError(f"Unbekannte Plattform: {', '.join(unknown)} "
                            f"(erlaubt: {', '.join(config.PLATFORMS)})")
    missing = [p for p in wanted if p not in targets]
    if missing:
        raise TerminalError(f"{', '.join(missing)} fehlt unter platforms: in der YAML-Datei")
    return [p for p in targets if p in wanted]


def _print_issues(issues: list[Issue]) -> None:
    for issue in issues:
        style, label = ("red", "Fehler") if issue.blocking else ("yellow", "Warnung")
        console.print(f"  [{style}]{label}[/] {escape(f'[{issue.platform}]')} "
                      f"{escape(issue.message)}")


def _print_info(video: Path, info: VideoInfo) -> None:
    rate = f", {info.bitrate / 1e6:.1f} Mbit/s" if info.bitrate else ""
    console.print(f"[bold]{video.name}[/]: {info.width}×{info.height}, {info.codec}, "
                  f"{info.fps:g} fps, {info.duration:.1f} s, "
                  f"{info.size / config.MB:.1f} MB{rate}, Audio: {info.audio_codec or 'keins'}")


def _prepare(video: Path, only: str | None,
             ) -> tuple[PostMeta, list[str], VideoInfo, list[Issue]]:
    meta = load_meta(meta_path_for(video))
    targets = _parse_only(only, meta)
    if not targets:
        raise TerminalError("Keine Zielplattform – in der YAML unter platforms: eintragen")
    info, issues = check_all(video, meta, targets)
    return meta, targets, info, issues


# --- check -----------------------------------------------------------------
@app.command()
def check(
    video: Annotated[Path, typer.Argument(help="MP4-Datei; daneben liegt die gleichnamige .yaml")],
    only: Annotated[str | None, typer.Option(help="Nur diese Plattformen, z. B. youtube,tiktok")]
    = None,
) -> None:
    """Video und Texte gegen die Limits der Zielplattformen prüfen."""
    _setup()
    try:
        meta, targets, info, issues = _prepare(video, only)
    except TerminalError as exc:
        raise _fail(str(exc)) from exc
    _print_info(video, info)
    console.print(f"Ziele: {', '.join(targets)}")
    _print_issues(issues)
    if any(i.blocking for i in issues):
        raise typer.Exit(1)
    console.print("[green]Alles in Ordnung.[/]")


# --- post ------------------------------------------------------------------
@app.command()
def post(
    video: Annotated[Path, typer.Argument(help="MP4-Datei; daneben liegt die gleichnamige .yaml")],
    only: Annotated[str | None, typer.Option(help="Nur diese Plattformen, z. B. youtube,tiktok")]
    = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Nur prüfen, nichts hochladen")]
    = False,
    force: Annotated[bool, typer.Option("--force", help="Auch hochladen, wenn schon erledigt")]
    = False,
) -> None:
    """Video auf alle (oder ausgewählte) Plattformen hochladen."""
    _setup()
    store = TokenStore()
    try:
        meta, targets, info, issues = _prepare(video, only)
        uploaders = {name: platforms.create(name, store) for name in targets}
        for uploader in uploaders.values():
            issues += uploader.check(video, meta, info)
        for token in token_overview(store):
            if token.platform in targets and token.logged_in and token.expiring_soon:
                issues.append(Issue("warning", token.platform,
                                    f"Anmeldung läuft bald ab: {token.detail}"))
    except TerminalError as exc:
        raise _fail(str(exc)) from exc

    _print_info(video, info)
    _print_issues(issues)
    if any(i.blocking for i in issues):
        raise _fail("Prüfung nicht bestanden – nichts hochgeladen")

    history = History()
    sha = file_sha256(video)
    if dry_run:
        for name in targets:
            prev = history.get(sha, name)
            state = STATUS_LABELS.get(prev.status, prev.status) if prev else "neu"
            action = "überspringen" if prev and prev.done and not force else "hochladen"
            console.print(f"  {name}: {state} → würde {action}")
        console.print("[green]Dry-Run: Prüfung bestanden, nichts hochgeladen.[/]")
        return

    results: list[UploadResult] = []
    failed = False
    for name, uploader in uploaders.items():  # nacheinander, unabhängig voneinander
        prev = history.get(sha, name)
        if prev and prev.done and not force:
            console.print(f"[dim]{name}: schon erledigt ({STATUS_LABELS[prev.status]}) – "
                          "übersprungen (--force zum erneuten Hochladen)[/]")
            results.append(UploadResult(name, prev.status, prev.remote_id, prev.link,
                                        "aus der Historie"))
            continue
        if prev and force:
            history.reset(sha, name)
            prev = None
        result = _run_upload(name, uploader, video, meta, sha, prev, history)
        failed = failed or result.status == FAILED
        results.append(result)

    _print_summary(results)
    for result in results:
        if result.platform == "tiktok" and result.note and result.note != "aus der Historie":
            console.print(Panel(result.note, title="TikTok-Caption zum Kopieren",
                                border_style="cyan"))
    history.close()
    if failed:
        raise typer.Exit(1)


def _run_upload(name: str, uploader: Uploader, video: Path, meta: PostMeta,
                sha: str, prev: Entry | None, history: History) -> UploadResult:
    def save(status: str, **kwargs: Any) -> None:
        history.save(sha, name, str(video.resolve()), status, **kwargs)

    with Progress(TextColumn(f"[bold]{name:<9}[/]"), BarColumn(), DownloadColumn(),
                  TransferSpeedColumn(), TimeRemainingColumn(), console=console,
                  transient=False) as bar:
        task = bar.add_task(name, total=video.stat().st_size)

        def progress(done: int, total: int) -> None:
            bar.update(task, completed=done, total=total)

        ctx = UploadContext(sha256=sha, progress=progress,
                            previous=prev,
                            save=save,
                            log=lambda msg: bar.console.print(f"  [dim]{escape(msg)}[/]"))
        try:
            result = uploader.upload(video, meta, ctx)
        except UploadTimeout as exc:
            log.warning("%s: %s", name, exc)
            return UploadResult(name, FAILED, note=str(exc))
        except (AuthRequired, QuotaExceeded, TerminalError) as exc:
            save(FAILED, error=str(exc))
            log.error("%s: %s", name, exc)
            return UploadResult(name, FAILED, note=str(exc))
        except (httpx.HTTPError, OSError) as exc:
            message = redact(f"{type(exc).__name__}: {exc}")
            save(FAILED, error=message)
            log.error("%s: %s", name, message)
            return UploadResult(name, FAILED, note=message)

    save(result.status, remote_id=result.remote_id, link=result.link)
    log.info("%s: %s %s", name, result.status, result.remote_id)
    return result


def _print_summary(results: list[UploadResult]) -> None:
    table = Table(title="Ergebnis")
    table.add_column("Plattform")
    table.add_column("Status")
    table.add_column("ID / Link", overflow="fold")
    table.add_column("Hinweis")
    for r in results:
        ok = r.status != FAILED
        status = STATUS_LABELS.get(r.status, r.status)
        note = r.note if r.platform != "tiktok" or not ok else "Caption siehe unten"
        if r.note == "aus der Historie":
            note = r.note
        table.add_row(r.platform, f"[{'green' if ok else 'red'}]{status}[/]",
                      r.link or r.remote_id or "–", note or "")
    console.print(table)


# --- auth ------------------------------------------------------------------
@app.command()
def auth(
    platform: Annotated[str | None, typer.Argument(help="youtube | instagram | tiktok")] = None,
    token: Annotated[str | None, typer.Option(
        help="Instagram: im App-Dashboard erzeugter Token (sonst wird danach gefragt)")] = None,
    check_only: Annotated[bool, typer.Option("--check", help="Status aller Anmeldungen")]
    = False,
    logout: Annotated[bool, typer.Option("--logout", help="Gespeicherte Anmeldung löschen")]
    = False,
) -> None:
    """Einmalig bei einer Plattform anmelden; Tokens landen im Schlüsselbund."""
    _setup()
    store = TokenStore()
    try:
        if check_only or platform is None:
            table = Table(title="Anmeldungen")
            for col in ("Plattform", "Status", "Details"):
                table.add_column(col)
            for info in token_overview(store):
                state = "[green]ok[/]" if info.logged_in else "[red]fehlt[/]"
                if info.logged_in and info.expiring_soon:
                    state = "[yellow]läuft bald ab[/]"
                table.add_row(info.platform, state, info.detail)
            console.print(table)
            return
        platform = platform.lower()
        if platform not in config.PLATFORMS:
            raise TerminalError(f"Unbekannte Plattform: {platform}")
        if logout:
            store.delete(platform)
            console.print(f"Anmeldung für {platform} gelöscht.")
            return
        with httpx.Client(timeout=60) as client:
            if platform == "youtube":
                message = youtube_login(store)
            elif platform == "instagram":
                value = token or typer.prompt("Instagram-Token aus dem App-Dashboard",
                                              hide_input=True)
                message = instagram_login(store, value, client)
            else:
                message = tiktok_login(store, client)
        console.print(f"[green]{message}[/]")
    except TerminalError as exc:
        raise _fail(str(exc)) from exc


# --- history ---------------------------------------------------------------
@app.command("history")
def show_history(
    limit: Annotated[int, typer.Option(help="Anzahl Einträge")] = 20,
) -> None:
    """Die letzten Uploads mit Links anzeigen."""
    _setup()
    history = History()
    entries = history.recent(limit)
    history.close()
    if not entries:
        console.print("Noch keine Uploads.")
        return
    table = Table(title="Upload-Historie")
    for col in ("Zeit (UTC)", "Video", "Plattform", "Status"):
        table.add_column(col)
    table.add_column("ID / Link", overflow="fold")  # Links vollständig, zum Kopieren
    table.add_column("Fehler")
    for e in entries:
        ok = e.status != FAILED
        table.add_row(e.updated_at.replace("T", " ")[:16], Path(e.video).name, e.platform,
                      f"[{'green' if ok else 'red'}]{STATUS_LABELS.get(e.status, e.status)}[/]",
                      e.link or e.remote_id or "–", e.error or "")
    console.print(table)


@app.command()
def version() -> None:
    """Version anzeigen."""
    console.print(f"terminal {__version__}")
