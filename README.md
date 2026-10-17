# Terminal

Lädt ein fertiges Hochformat-Video mit **einem Befehl** (oder einem Tipp in der Android-App) hoch:

| Plattform | Ergebnis | Weg |
|---|---|---|
| **YouTube Shorts** | veröffentlicht (oder privat/nicht gelistet/geplant) | YouTube Data API v3, `videos.insert`, resumable |
| **Instagram Reels** | veröffentlicht | Instagram Login (`graph.instagram.com`), Video kurz über R2/S3-Bucket |
| **TikTok** | **Entwurf** im Posteingang – du veröffentlichst mit einem Tipp in der App | Content Posting API, `inbox/video/init` |

Ausschließlich offizielle APIs – keine Browser-Automatisierung, keine nachgebauten App-Schnittstellen.
Das ausführliche Konzept (Limits, Entscheidungen, Risiken, Quellen) steht in [`docs/konzept.md`](docs/konzept.md).

## Android-App (APK)

Die einfachste Variante fürs Handy – ohne Termux, mit Oberfläche.

1. **Herunterladen:** Im Handy-Browser das Release
   [`android-latest`](https://github.com/SystemError5251/SMVUT/releases/tag/android-latest) öffnen
   und **Terminal-0.1.0.apk** antippen. Jede neue Version entsteht automatisch (GitHub Actions baut sie
   und testet sie vorher in einem Android-Emulator).
2. **Installieren:** Die heruntergeladene Datei antippen. Android fragt einmal, ob der Browser/die
   GitHub-App Apps installieren darf → erlauben. Play Protect warnt eventuell vor einer
   „unbekannten App“ → „Trotzdem installieren“.
3. **Tab „Konten“:** Unten die Einstellungen eintragen (Bucket für Instagram, TikTok Client Key/Secret)
   und speichern. Dann:
   - YouTube: `client_secret.json` (OAuth-Client „Desktop-App“) importieren → „Bei YouTube anmelden“
   - Instagram: Token einfügen → „Instagram-Token übernehmen“
   - TikTok: „Bei TikTok anmelden“
   Logins öffnen den Handy-Browser; nach „Login erfolgreich“ zurück zur App wechseln.
4. **Tab „Hochladen“:** Video auswählen, Titel/Beschreibung/Hashtags eintragen, Plattformen wählen,
   „Hochladen“. Während des Uploads bleibt der Bildschirm an – die App dabei im Vordergrund lassen.
   Für TikTok danach „TikTok-Caption kopieren“ und in der TikTok-App den Entwurf öffnen.
5. **Tab „Verlauf“:** frühere Uploads mit Links. Ein zweites „Hochladen“ desselben Videos lädt
   nichts doppelt hoch und setzt abgebrochene Uploads fort.

Updates: neue APK einfach drüber installieren – Logins und Einstellungen bleiben erhalten (alle
Versionen sind mit demselben Schlüssel `android/debug.keystore` signiert).

Selbsttest auf dem Gerät (optional, per USB/adb):
`adb shell am start -n io.github.systemerror5251.terminal_app/org.beeware.android.MainActivity --ez selftest true`
– das Ergebnis steht danach im Ergebnisfeld der App.

APK selbst bauen: `pip install briefcase==0.4.6` (Python 3.13), dann `bash scripts/build-apk.sh`.

## Android (Termux)

Terminal läuft auch direkt auf dem Handy in [Termux](https://termux.dev). Alles ist reines Python, es muss nichts kompiliert werden.

1. **Termux installieren** – aus [F-Droid](https://f-droid.org/packages/com.termux/) oder von [GitHub](https://github.com/termux/termux-app/releases) (die Play-Store-Version ist veraltet).
2. **Projekt holen und einrichten** (dauert ca. 1–3 Minuten). Das Repository ist privat, deshalb erst mit der GitHub-App anmelden. GitHub nimmt beim Klonen kein Passwort an. Die Befehle **einzeln** eingeben, nicht alle auf einmal einfügen:
   ```bash
   pkg install -y gh git
   gh auth login        # GitHub.com → HTTPS → Y → Login with a web browser
   gh repo clone SystemError5251/SMVUT
   cd SMVUT
   bash scripts/termux-setup.sh
   ```
   Das Skript installiert `python`, `python-cryptography` (fertig gebaut) und Terminal, legt `~/.terminal/.env` an und fragt nach dem Zugriff auf den Handy-Speicher (`termux-setup-storage` – bitte erlauben).
3. **Zugangsdaten eintragen:** `nano ~/.terminal/.env` (Speichern: Strg+O, Enter; Beenden: Strg+X).
   Die Google-Datei `client_secret.json` aus den Downloads übernehmen:
   ```bash
   cp ~/storage/downloads/client_secret*.json ~/.terminal/client_secret.json
   ```
4. **Anmelden:** `terminal auth youtube` bzw. `terminal auth tiktok` öffnen den Login im Android-Browser. Nach „Login erfolgreich“ zurück zu Termux wechseln – das Programm hat den Login dann schon übernommen. Instagram: `terminal auth instagram --token …`.
5. **Hochladen** – Handy-Videos liegen unter `~/storage/dcim/Camera`, Downloads unter `~/storage/downloads`:
   ```bash
   cd ~/storage/dcim/Camera
   cp ~/SMVUT/examples/clip.yaml mein-video.yaml   # dann mit nano anpassen
   terminal post mein-video.mp4
   ```

Tipps für Termux:
- Bei großen Videos vorher `termux-wake-lock` ausführen (und für Termux in den Android-Einstellungen die Akku-Optimierung abschalten), damit Android den Upload nicht anhält. Danach `termux-wake-unlock`.
- Android hat keinen Schlüsselbund: Tokens liegen deshalb in `~/.terminal/tokens.json` (nur für Termux lesbar, Modus 600). Diese Datei nie weitergeben.
- Aktualisieren: `cd ~/SMVUT && git pull` – fertig (die Installation verweist direkt auf den Ordner).
- `pip install --upgrade pip` in Termux nicht ausführen, das beschädigt Termux’ Python-Paket.

## Schnellstart (Windows, Mac, Linux)

```bash
# Voraussetzungen: Python 3.12+ und uv (ffmpeg nicht nötig)
uv sync
cp .env.example .env          # oder ~/.terminal/.env – Client-IDs, Secrets, Bucket eintragen

uv run terminal auth youtube              # Browser-Login bei Google
uv run terminal auth instagram --token …  # Token aus dem Meta-App-Dashboard
uv run terminal auth tiktok               # Browser-Login bei TikTok (PKCE)
uv run terminal auth --check              # Status aller Anmeldungen

uv run terminal check clip-017.mp4        # nur prüfen
uv run terminal post clip-017.mp4 --dry-run
uv run terminal post clip-017.mp4         # alle Plattformen aus der YAML
uv run terminal post clip-017.mp4 --only youtube,tiktok
uv run terminal history                   # letzte Uploads mit Links
```

## Metadaten: YAML neben dem Video

Neben `clip-017.mp4` liegt `clip-017.yaml`. Fehlt eine Plattform unter `platforms:`, wird sie übersprungen; ein leerer Abschnitt wie `tiktok:` reicht, um sie mit Standardwerten einzuschalten.

```yaml
title: "Drei Fakten über Oktopusse"
description: |
  Warum Oktopusse drei Herzen haben – in 50 Sekunden.
hashtags: [wissenschaft, tiere, fakten]
ai_generated: false          # YouTube: containsSyntheticMedia; TikTok: Hinweis fürs KI-Label in der App
platforms:
  instagram:
    share_to_feed: true
    caption: null            # null = gemeinsamer Text aus description + hashtags
  youtube:
    privacy: public          # public | unlisted | private
    category_id: "27"        # Bildung
    made_for_kids: false
    publish_at: null         # optional, z. B. 2026-10-20T18:00:00+02:00 (setzt privacy automatisch auf private)
    description: null        # optional eigener Text für YouTube
  tiktok:
    caption: null            # wird am Ende zum Kopieren ausgegeben (Entwurf)
```

Tippfehler in Feldnamen brechen sofort mit Zeilenangabe ab. Eine Vorlage liegt in [`examples/clip.yaml`](examples/clip.yaml).

## Was geprüft wird (`terminal check`)

**Blockierend:** Codec nicht H.264/HEVC · Bildrate außerhalb 23–60 fps · Dauer/Größe über dem Plattform-Limit ·
YouTube-Titel > 100 Zeichen oder mit `<`/`>` · YouTube-Beschreibung > 5.000 Bytes · Tags > 500 Zeichen ·
Instagram-Caption > 2.200 Zeichen, > 30 Hashtags, > 20 @-Erwähnungen · Instagram-Bitrate > 25 Mbit/s oder Breite > 1.920 px
(mit passendem ffmpeg-Befehl zum Neu-Encodieren) · TikTok-Caption > 2.200 UTF-16-Einheiten · TikTok-Auflösung außerhalb 360–4096 px.

**Warnungen:** nicht 9:16 · YouTube Querformat oder > 3 min (wird normales Video statt Short) · Instagram-Audio nicht AAC ·
Anmeldung läuft in weniger als 7 Tagen ab.

Videos (MP4/MOV) liest Terminal selbst, ohne ffmpeg (`src/terminal/mp4.py`). Vor dem Instagram-Upload wird automatisch verlustfrei „faststart“ umsortiert (Kopie in `~/.terminal/cache/`, das Original bleibt unangetastet). Neu encodiert wird nie automatisch.

## Robustheit

- Plattformen laufen **nacheinander und unabhängig**: scheitert TikTok, wird YouTube trotzdem fertig. Exit-Code 1, wenn eine Plattform scheitert.
- **Upload-Historie** in `~/.terminal/history.db` (SHA-256 der Datei je Plattform). Ein zweiter `post` lädt nichts doppelt hoch (außer mit `--force`) und **setzt abgebrochene Läufe fort** – die Instagram-Container-ID wird vor `media_publish` gesichert, ein laufender TikTok-Entwurf wird weiter abgefragt.
- **Wiederholungen** nur für vorübergehende Fehler (Netzwerk, 429, 5xx) und nur für sichere Schritte: bis 5 Versuche, 2/4/8 … max. 60 s. Tageslimits und endgültige Fehler werden nicht wiederholt.
- **Tokens** im Schlüsselbund (keyring); auf Termux in `~/.terminal/tokens.json` mit Modus 600 (Ordner `~/.terminal` ist nur für dich lesbar). Instagram wird nach 24 h automatisch verlängert, TikTok eine Stunde vor Ablauf, YouTube über die Google-Bibliothek. Bei HTTP 401 einmal erneuern, danach klare Meldung („Bitte `terminal auth tiktok` ausführen“).
- **Log** `~/.terminal/terminal.log` enthält nur eigene Meldungen – keine Header, keine Request-Inhalte; Tokens werden zusätzlich herausgefiltert.

## Setup (einmalig in den Entwicklerportalen)

**YouTube (Google Cloud)**
1. Projekt anlegen, „YouTube Data API v3“ aktivieren.
2. OAuth-Zustimmungsbildschirm (extern), Scope `youtube.upload`, dein Konto als Testnutzer.
3. OAuth-Client „Desktop-App“ erstellen, `client_secret.json` herunterladen → Pfad in `YOUTUBE_CLIENT_SECRETS`.
4. Nach dem ersten Test auf „In production“ stellen – sonst ist jede Woche ein neuer Login nötig.
5. Falls Uploads privat gesperrt bleiben: Audit-Formular einreichen.

**Instagram (Meta)**
1. Instagram-Konto auf Professional (Creator/Business) umstellen.
2. Auf developers.facebook.com eine App anlegen, Produkt Instagram mit „API-Setup mit Instagram-Login“.
3. Dein Konto unter App-Rollen als Instagram-Tester eintragen und die Einladung in der App annehmen.
4. Berechtigungen `instagram_business_basic` und `instagram_business_content_publish`.
5. Im API-Setup einen Token erzeugen → `terminal auth instagram --token <TOKEN>`.

**Bucket für Instagram (Cloudflare R2 oder AWS S3)**
1. Privaten Bucket anlegen, API-Token mit Lese-/Schreibrecht nur für diesen Bucket.
2. Lifecycle-Regel: Objekte nach 1 Tag löschen.
3. `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` in `.env`.

**TikTok**
1. Entwicklerkonto auf developers.tiktok.com, App registrieren.
2. Login Kit (Desktop) mit Redirect-URI `http://localhost:3455/callback/` und Content Posting API hinzufügen.
3. Scopes `user.info.basic` und `video.upload`; Sandbox mit deinem Konto als Testnutzer.
4. Nur falls Entwürfe aus der Sandbox nicht öffentlich gehen: App Review beantragen.

**Linux ohne Desktop:** keyring braucht einen Schlüsselbund (gnome-keyring/KWallet). Alternativ `TERMINAL_TOKEN_STORE=file` in die `.env` schreiben – dann wie auf Termux in `~/.terminal/tokens.json`.

## Testvideo

Mit ffmpeg (am PC oder `pkg install ffmpeg` in Termux):

```bash
ffmpeg -f lavfi -i testsrc=size=1080x1920:rate=30 -f lavfi -i sine=frequency=440 \
  -t 5 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest test.mp4
```

Erster echter Lauf am besten mit `privacy: private` bei YouTube.

## Entwicklung

```bash
uv run ruff check
uv run mypy src
uv run pytest          # Tests machen nie echte Uploads (HTTP gemockt mit respx)
```

Aufbau: `src/terminal/` enthält die gesamte Logik (CLI in `cli.py`, gemeinsamer Ablauf in
`service.py`), `src/terminal_app/` die Android-Oberfläche. YouTube-Upload und S3-Signatur sind
direkt mit httpx umgesetzt – ohne google-api-python-client und boto3, die die APK um ~140 MB
vergrößern würden.

Neue Plattform: eine Datei in `src/terminal/platforms/`, die das `Uploader`-Protokoll aus `platforms/base.py` erfüllt, und ein Eintrag in `platforms/__init__.py`.
