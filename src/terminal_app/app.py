"""Terminal als Android-App: Video wählen, Texte eintragen, hochladen."""

from __future__ import annotations

import asyncio
import json
import shutil
import textwrap
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import toga
from toga.style.pack import Pack

from terminal import config, service
from terminal.auth import (
    TokenStore,
    instagram_login,
    tiktok_login,
    token_overview,
    youtube_login,
)
from terminal.errors import TerminalError, redact
from terminal.history import FAILED, STATUS_LABELS, History, file_sha256
from terminal.validate import video_info
from terminal_app import device, forms, selftest

GAP = 8


def _column(*children: toga.Widget, margin: int = 0) -> toga.Box:
    return toga.Box(children=list(children), style=Pack(direction="column", margin=margin,
                                                        gap=GAP))


def _row(*children: toga.Widget) -> toga.Box:
    return toga.Box(children=list(children), style=Pack(direction="row", gap=GAP))


def _heading(text: str) -> toga.Label:
    return toga.Label(text, style=Pack(font_weight="bold", margin_top=12))


def _wrap(text: str, width: int = 40) -> str:
    """Labels brechen auf Android nicht selbst um: schmal genug fürs Handy umbrechen."""
    return "\n".join(textwrap.fill(line, width=width, subsequent_indent="   ") or line
                     for line in text.splitlines())


def _hint(text: str) -> toga.Label:
    return toga.Label(_wrap(text), style=Pack(font_size=11))


class TerminalApp(toga.App):
    def startup(self) -> None:
        # Zuerst: legt auf Android TERMINAL_HOME fest (terminal.config liest es bei Bedarf)
        device.setup(self)
        config.load_env()
        service.setup_logging()
        self.store = TokenStore()
        self.video: Path | None = None
        self.caption: str | None = None
        self.busy = False

        self.tabs = toga.OptionContainer(content=[
            ("Hochladen", toga.ScrollContainer(content=self._upload_tab(), horizontal=False)),
            ("Konten", toga.ScrollContainer(content=self._accounts_tab(), horizontal=False)),
            ("Verlauf", self._history_tab()),
        ], on_select=self._on_tab)
        self.window = toga.MainWindow(title=self.formal_name)  # type: ignore[no-untyped-call]
        self.window.content = self.tabs
        self.main_window = self.window
        self.window.show()

    async def on_running(self) -> None:  # type: ignore[override]  # Toga erlaubt async
        if device.selftest_requested(self):
            await self.run_selftest()

    async def run_selftest(self) -> str:
        self.status.text = "Selbsttest läuft …"
        checks = await asyncio.to_thread(selftest.background_checks)
        checks += selftest.ui_checks(self)
        text = selftest.report(checks)
        for line in text.splitlines():
            print(f"SELFTEST {line}", flush=True)  # landet im Logcat (python.stdout)
        self.results.value = text
        self.status.text = text.splitlines()[-1]
        return text

    # --- Hochladen ---------------------------------------------------------
    def _upload_tab(self) -> toga.Box:
        self.video_label = toga.Label("Noch kein Video gewählt")
        self.title_input = toga.TextInput(placeholder="Titel (YouTube, max. 100 Zeichen)")
        self.description_input = toga.MultilineTextInput(
            placeholder="Beschreibung / Caption", style=Pack(height=140))
        self.hashtags_input = toga.TextInput(placeholder="Hashtags, z. B. tiere fakten natur")
        self.switches = {
            "youtube": toga.Switch("YouTube Short", value=True),
            "instagram": toga.Switch("Instagram Reel", value=True),
            "tiktok": toga.Switch("TikTok (Entwurf)", value=True),
        }
        self.privacy = toga.Selection(items=list(forms.PRIVACY_LABELS),
                                      value="öffentlich")
        self.ai_switch = toga.Switch("KI-generierter Inhalt")
        self.post_button = toga.Button("Hochladen", on_press=self.on_post,
                                       style=Pack(margin_top=8))
        self.progress = toga.ProgressBar(max=1.0, value=0)
        self.status = toga.Label("")
        self.results = toga.MultilineTextInput(readonly=True, style=Pack(height=160))
        self.copy_button = toga.Button("TikTok-Caption kopieren", enabled=False,
                                       on_press=self.on_copy_caption)
        return _column(
            toga.Button("Video auswählen", on_press=self.on_pick_video),
            self.video_label,
            _heading("Texte"),
            self.title_input, self.description_input, self.hashtags_input,
            _heading("Plattformen"),
            *self.switches.values(),
            _row(toga.Label("YouTube-Sichtbarkeit"), self.privacy),
            self.ai_switch,
            self.post_button, self.progress, self.status,
            self.results, self.copy_button,
            margin=12)

    async def on_pick_video(self, widget: toga.Widget) -> None:
        if self.busy:
            return
        target_dir = config.home() / "import"
        # Alte Importe und faststart-Kopien entfernen: Videos sind groß
        for folder in (target_dir, config.cache_dir()):
            shutil.rmtree(folder, ignore_errors=True)
        self.video_label.text = "Video wird geladen …"
        try:
            path = await device.pick_file(self, "video/*", target_dir)
            if path is None:
                self.video_label.text = "Noch kein Video gewählt"
                return
            info = await asyncio.to_thread(video_info, path)
        except Exception as exc:  # Auswahl/Kopie/Lesen – dem Nutzer zeigen
            self.video = None
            self.video_label.text = "Noch kein Video gewählt"
            await self._error("Video nicht lesbar", exc)
            return
        self.video = path
        self.video_label.text = _wrap(forms.describe_video(path.name, info))
        if not self.title_input.value:
            self.title_input.value = path.stem.replace("_", " ")[:100]

    async def on_post(self, widget: toga.Widget) -> None:
        if self.busy:
            return
        if self.video is None:
            await self._info("Kein Video", "Bitte zuerst ein Video auswählen.")
            return
        video = self.video
        try:
            meta = forms.build_meta(
                title=self.title_input.value, description=self.description_input.value,
                hashtags=self.hashtags_input.value,
                platforms=[n for n, s in self.switches.items() if s.value],
                privacy=str(self.privacy.value), ai_generated=bool(self.ai_switch.value))
            self._set_busy(True, "Prüfe Video und Anmeldungen …")
            info, issues, uploaders = await asyncio.to_thread(
                service.preflight, video, meta, meta.targets, self.store)
        except Exception as exc:
            self._set_busy(False, "")
            await self._error("Prüfung fehlgeschlagen", exc)
            return

        if any(i.blocking for i in issues):
            self._set_busy(False, "Nicht hochgeladen – bitte Hinweise beachten")
            await self._info("Prüfung nicht bestanden", forms.format_issues(issues))
            return
        if issues and not await self.window.dialog(toga.ConfirmDialog(
                "Hinweise", forms.format_issues(issues) + "\n\nTrotzdem hochladen?")):
            self._set_busy(False, "")
            return

        self.results.value = ""
        self.copy_button.enabled = False
        device.keep_screen_on(self, True)
        try:
            results = await asyncio.to_thread(self._run_post, video, meta, uploaders)
        except Exception as exc:
            await self._error("Upload abgebrochen", exc)
            return
        finally:
            device.keep_screen_on(self, False)
            self._set_busy(False, "")
        self.progress.value = 1.0
        self.results.value = forms.format_results(results)
        self.caption = forms.tiktok_caption(results, meta)
        self.copy_button.enabled = self.caption is not None
        failed = sum(r.status == FAILED for r in results)
        self.status.text = (f"{failed} Plattform(en) fehlgeschlagen – erneut „Hochladen“ "
                            "setzt fort" if failed else "Fertig")

    def _run_post(self, video: Path, meta: Any, uploaders: dict[str, Any]) -> list[Any]:
        """Läuft im Hintergrund-Thread; die Oberfläche wird über die Event-Loop aktualisiert."""
        ui: Callable[..., Any] = self.loop.call_soon_threadsafe
        history = History()  # SQLite-Verbindung gehört zu diesem Thread
        try:
            sha = file_sha256(video)
            return service.post(
                video, meta, uploaders, history, sha,
                on_start=lambda name: ui(self._show_status,
                                         f"{forms.PLATFORM_LABELS[name]}: lädt hoch …", 0.0),
                progress=lambda name, done, total: ui(self._show_progress, done, total),
                message=lambda name, msg: ui(self._show_status,
                                             f"{forms.PLATFORM_LABELS[name]}: {msg}", None))
        finally:
            history.close()

    def _show_status(self, text: str, progress: float | None) -> None:
        self.status.text = _wrap(text)
        if progress is not None:
            self.progress.value = progress

    def _show_progress(self, done: int, total: int) -> None:
        self.progress.value = min(done / total, 1.0) if total else 0.0

    async def on_copy_caption(self, widget: toga.Widget) -> None:
        if not self.caption:
            return
        if device.copy_text(self, "TikTok-Caption", self.caption):
            await self._info("Kopiert", "Caption ist in der Zwischenablage. Jetzt in der "
                                        "TikTok-App den Entwurf öffnen und einfügen.")
        else:
            await self._info("TikTok-Caption", self.caption)

    def _set_busy(self, busy: bool, text: str) -> None:
        self.busy = busy
        self.post_button.enabled = not busy
        self.status.text = text
        if busy:
            self.progress.value = 0

    # --- Konten ------------------------------------------------------------
    def _accounts_tab(self) -> toga.Box:
        self.accounts_status = toga.Label("")
        self.instagram_token = toga.PasswordInput(placeholder="Instagram-Token einfügen")
        values = config.read_env_file()
        self.settings: dict[str, toga.TextInput] = {}
        rows: list[toga.Widget] = []
        for key, label, secret in forms.SETTINGS:
            field = (toga.PasswordInput if secret else toga.TextInput)(value=values.get(key, ""))
            self.settings[key] = field
            rows += [_hint(label), field]
        self._refresh_accounts()
        return _column(
            _heading("Status"), self.accounts_status,
            toga.Button("Aktualisieren", on_press=lambda w: self._refresh_accounts()),
            _heading("YouTube"),
            _hint("1. OAuth-Client (Desktop-App) aus der Google Cloud Console als JSON "
                  "herunterladen  2. hier importieren  3. anmelden"),
            toga.Button("client_secret.json importieren", on_press=self.on_import_google),
            toga.Button("Bei YouTube anmelden", on_press=self.on_login_youtube),
            _heading("Instagram"),
            _hint("Token im Meta-App-Dashboard unter „API-Setup mit Instagram-Login“ "
                  "erzeugen und hier einfügen"),
            self.instagram_token,
            toga.Button("Instagram-Token übernehmen", on_press=self.on_login_instagram),
            _heading("TikTok"),
            _hint("Vorher unten Client Key und Secret speichern. Redirect-URI in der "
                  "TikTok-App: http://localhost:3455/callback/"),
            toga.Button("Bei TikTok anmelden", on_press=self.on_login_tiktok),
            _heading("Einstellungen"),
            *rows,
            toga.Button("Einstellungen speichern", on_press=self.on_save_settings),
            margin=12)

    def _refresh_accounts(self) -> None:
        lines = []
        for info in token_overview(self.store):
            mark = "✓" if info.logged_in else "✗"
            soon = " – läuft bald ab!" if info.logged_in and info.expiring_soon else ""
            lines.append(f"{mark} {forms.PLATFORM_LABELS[info.platform]}: {info.detail}{soon}")
        google = config.find_file(config.env("YOUTUBE_CLIENT_SECRETS") or "client_secret.json")
        lines.append(f"{'✓' if google.exists() else '✗'} Google-OAuth-Datei "
                     f"{'vorhanden' if google.exists() else 'fehlt'}")
        self.accounts_status.text = _wrap("\n".join(lines))

    async def on_import_google(self, widget: toga.Widget) -> None:
        try:
            path = await device.pick_file(self, "*/*", config.home() / "import-json")
            if path is None:
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            if "installed" not in data:
                raise TerminalError("Das ist kein OAuth-Client vom Typ „Desktop-App“ "
                                    "(Schlüssel „installed“ fehlt)")
            target = config.home() / "client_secret.json"
            shutil.move(str(path), target)
            target.chmod(0o600)
            config.write_env_file({"YOUTUBE_CLIENT_SECRETS": "client_secret.json"})
        except Exception as exc:
            await self._error("Import fehlgeschlagen", exc)
            return
        self._refresh_accounts()
        await self._info("Importiert", "Jetzt „Bei YouTube anmelden“ tippen.")

    async def on_login_youtube(self, widget: toga.Widget) -> None:
        await self._login("YouTube", youtube_login, self.store)

    async def on_login_instagram(self, widget: toga.Widget) -> None:
        token = self.instagram_token.value.strip()
        if not token:
            await self._info("Token fehlt", "Bitte zuerst den Instagram-Token einfügen.")
            return

        def login() -> str:
            with httpx.Client(timeout=60) as client:
                return instagram_login(self.store, token, client)

        if await self._login("Instagram", login):
            self.instagram_token.value = ""

    async def on_login_tiktok(self, widget: toga.Widget) -> None:
        def login() -> str:
            with httpx.Client(timeout=60) as client:
                return tiktok_login(self.store, client)

        await self._login("TikTok", login)

    async def _login(self, name: str, fn: Callable[..., str], *args: Any) -> bool:
        if self.busy:
            return False
        self.busy = True
        try:
            message = await asyncio.to_thread(fn, *args)
        except Exception as exc:
            await self._error(f"{name}-Anmeldung fehlgeschlagen", exc)
            return False
        finally:
            self.busy = False
            self._refresh_accounts()
        await self._info(f"{name} verbunden", message)
        return True

    async def on_save_settings(self, widget: toga.Widget) -> None:
        try:
            config.write_env_file({key: str(field.value) for key, field in
                                   self.settings.items()})
        except Exception as exc:
            await self._error("Speichern fehlgeschlagen", exc)
            return
        self._refresh_accounts()
        await self._info("Gespeichert", "Einstellungen gespeichert.")

    # --- Verlauf -----------------------------------------------------------
    def _history_tab(self) -> toga.Box:
        self.history_table: toga.Table[Any] = toga.Table(
            columns=["Zeit", "Video", "Plattform", "Status"],
            on_select=self._on_history_select, style=Pack(flex=1))
        self.open_link = toga.Button("Link öffnen", enabled=False, on_press=self.on_open_link)
        self._selected_link: str | None = None
        return toga.Box(children=[
            self.history_table,
            _row(toga.Button("Aktualisieren", on_press=lambda w: self._refresh_history()),
                 self.open_link),
        ], style=Pack(direction="column", margin=12, gap=GAP))

    def _refresh_history(self) -> None:
        history = History()
        try:
            entries = history.recent(50)
        finally:
            history.close()
        self.history_table.data = [
            {"zeit": f"{datetime.fromisoformat(e.updated_at).astimezone():%d.%m. %H:%M}",
             "video": Path(e.video).name,
             "plattform": forms.PLATFORM_LABELS.get(e.platform, e.platform),
             "status": STATUS_LABELS.get(e.status, e.status), "link": e.link}
            for e in entries]
        self._selected_link = None
        self.open_link.enabled = False

    def _on_history_select(self, widget: toga.Table[Any], **kwargs: Any) -> None:
        row = widget.selection
        link = getattr(row, "link", None) if row is not None else None
        self._selected_link = link if isinstance(link, str) and link else None
        self.open_link.enabled = self._selected_link is not None

    def on_open_link(self, widget: toga.Widget) -> None:
        if self._selected_link:
            device.open_url(self, self._selected_link)

    def _on_tab(self, widget: toga.OptionContainer, **kwargs: Any) -> None:
        current = widget.current_tab.text if widget.current_tab else ""
        if current == "Verlauf":
            self._refresh_history()
        elif current == "Konten":
            self._refresh_accounts()

    # --- Dialoge -----------------------------------------------------------
    async def _info(self, title: str, message: str) -> None:
        await self.window.dialog(toga.InfoDialog(title, message))

    async def _error(self, title: str, exc: BaseException) -> None:
        if isinstance(exc, TerminalError):
            message = str(exc)
        else:
            message = redact(f"{type(exc).__name__}: {exc}")
            service.log.error("%s: %s\n%s", title, message,
                              redact("".join(traceback.format_exception(exc))))
        await self.window.dialog(toga.ErrorDialog(title, message))


def main() -> TerminalApp:
    return TerminalApp("Terminal", "io.github.systemerror5251.terminal_app")
