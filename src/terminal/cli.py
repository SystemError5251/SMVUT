"""Kommandozeile: smvut check | post | auth | history (auch als `terminal`)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from terminal import __version__, config, service
from terminal.auth import (
    TokenStore,
    instagram_login,
    tiktok_login,
    token_overview,
    youtube_login,
)
from terminal.errors import TerminalError, redact
from terminal.history import FAILED, STATUS_LABELS, History, file_sha256
from terminal.meta import PostMeta, load_meta, meta_path_for
from terminal.platforms.base import UploadResult
from terminal.validate import Issue, VideoInfo, check_all

app = typer.Typer(
    help="SMVUT (Social Media Upload Tool) – Hochformat-Videos als Instagram Reel, "
         "YouTube Short und "
         "TikTok-Entwurf hochladen (nur offizielle APIs).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
log = logging.getLogger(config.APP_NAME)


def _setup() -> None:
    config.load_env()
    config.setup_browser()
    service.setup_logging()


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


def _load(video: Path, only: str | None) -> tuple[PostMeta, list[str]]:
    meta = load_meta(meta_path_for(video))
    targets = _parse_only(only, meta)
    if not targets:
        raise TerminalError("Keine Zielplattform – in der YAML unter platforms: eintragen")
    return meta, targets


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
        meta, targets = _load(video, only)
        info, issues = check_all(video, meta, targets)
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
        meta, targets = _load(video, only)
        info, issues, uploaders = service.preflight(video, meta, targets, store)
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

    with Progress(TextColumn("[bold]{task.description:<9}[/]"), BarColumn(), DownloadColumn(),
                  TransferSpeedColumn(), TimeRemainingColumn(), console=console) as bar:
        tasks: dict[str, TaskID] = {}

        def on_start(name: str) -> None:
            tasks[name] = bar.add_task(name, total=video.stat().st_size)

        def progress(name: str, done: int, total: int) -> None:
            bar.update(tasks[name], completed=done, total=total)

        def message(name: str, msg: str) -> None:
            bar.console.print(f"  [dim]{escape(name)}: {escape(msg)}[/]")

        results = service.post(video, meta, uploaders, history, sha, force=force,
                               on_start=on_start, progress=progress, message=message)

    _print_summary(results)
    for result in results:
        if result.platform == "tiktok" and result.note and result.note != service.FROM_HISTORY:
            console.print(Panel(result.note, title="TikTok-Caption zum Kopieren",
                                border_style="cyan"))
    history.close()
    if any(r.status == FAILED for r in results):
        raise typer.Exit(1)


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
    console.print(f"SMVUT {__version__}")
