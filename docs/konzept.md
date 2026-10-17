> Textfassung des Konzepts aus dem Projekt „WhatIf“. Original mit Tabellen, Diagramm und Quellen-Links:
> https://claude.ai/artifact/41MP3qxE1BfHx2vTpa9Kax
>
> Hinweis: Im Konzept heißt das Tool noch `shortpost`; umgesetzt ist es als **Terminal** (Befehl `terminal`, Paket `src/terminal/`, Daten in `~/.terminal/`).

# Konzept: Video-Uploader für Reels, Shorts & TikTok
 · 

## Ziel & Umfang
Ein Python-Kommandozeilentool namens shortpost lädt ein fertiges Hochformat-Video mit einem Befehl als Instagram Reel, YouTube Short und TikTok hoch – ausschließlich über die offiziellen APIs der drei Plattformen.
- Eingabe: eine MP4-Datei (9:16) plus eine gleichnamige YAML-Datei mit Titel, Beschreibung, Hashtags und Zielplattformen; pro Plattform optional ein eigener Text.

- Aufruf: shortpost post video.mp4 für alle Plattformen, --only youtube,tiktok für eine Auswahl, --dry-run zum reinen Prüfen.

- Ergebnis: Reel und Short sind veröffentlicht, auf TikTok liegt ein Entwurf zum Veröffentlichen in der App; ID, Link und Status landen in einer lokalen Upload-Historie.

Nicht im Umfang: Schnitt, Rendering, Untertitel, Thumbnail-Erzeugung, Analytics und Kommentarverwaltung. Inoffizielle Upload-Wege (nachgebaute App-Schnittstellen, Bots ohne Aufsicht) sind ausgeschlossen. Das gilt auch für Browser-Automatisierung, etwa über Claude in Chrome.

## Plattform-Überblick
Alle drei Plattformen bieten einen offiziellen Upload-Weg per API. Der Engpass ist TikTok: Direkt veröffentlichen geht dort erst nach einem Audit, das ein Tool für den Eigengebrauch kaum bekommt. shortpost lädt TikToks deshalb als Entwurf hoch; du veröffentlichst mit einem Tipp in der App.
 |  | Instagram Reels
 | YouTube Shorts
 | TikTok

 | API
 | Instagram Platform – Content Publishing
 | YouTube Data API v3 – videos.insert
 | Content Posting API – Upload (Entwurf)

 | Konto
 | Instagram-Professional-Konto; keine Facebook-Seite nötig
 | YouTube-Kanal (Google-Konto)
 | TikTok-Konto

 | Login & Rechte
 | Instagram Login: instagram_business_basic, instagram_business_content_publish
 | OAuth 2.0, Scope youtube.upload
 | OAuth 2.0 (Login Kit), Scope video.upload

 | Lokale Datei hochladen
 | Nein: Meta holt das Video von einer öffentlichen URL (video_url); shortpost legt es dafür kurz in einen Bucket
 | Ja, resumable Upload
 | Ja, FILE_UPLOAD in Chunks von 5–64 MB, nacheinander

 | Sichtbarkeit ohne Review
 | Öffentlich
 | Unklar – siehe Hinweis unten
 | Entwurf im Posteingang; die Sichtbarkeit wählst du beim Veröffentlichen in der App (Phase 0 prüft, ob öffentlich geht)

 | Tageslimit
 | 100 API-Posts und 400 Container pro 24 h
 | 100 videos.insert-Aufrufe pro Tag (eigener Upload-Quota-Topf)
 | 6 Init-Aufrufe pro Minute und Token; ca. 15 Posts pro Tag und Konto

 | Videoformat
 | MP4/MOV, H.264/HEVC, AAC, 23–60 fps, 3 s bis 15 min, max. 300 MB, 9:16 empfohlen
 | Quadratisch oder hochkant und max. 3 min → wird automatisch als Short eingestuft
 | MP4/MOV/WebM, H.264/H.265/VP8/VP9, 23–60 fps, 360–4096 px, max. 4 GB, per API bis 10 min

 | Text
 | Caption max. 2.200 Zeichen, 30 Hashtags, 20 @-Erwähnungen
 | Titel, Beschreibung, Tags, Kategorie
 | Caption trägst du in der App ein, die Entwurfs-API überträgt nur das Video; max. 2.200 Zeichen

Hinweis YouTube: Seit 2020 wurden Uploads aus ungeprüften API-Projekten privat gesperrt (Revision History, 28.07.2020). Die aktuelle Referenzseite von videos.insert (Stand 08.10.2026) sagt dagegen, solche Uploads seien nicht mehr auf privat beschränkt – eine Ankündigung dazu fehlt in der Änderungshistorie. Phase 0 klärt das; das Audit-Formular sollte trotzdem eingeplant werden.
Hinweis Quote: Die Upload-Kosten bei YouTube sind gefallen (Dez. 2025: von ca. 1.600 auf ca. 100 Einheiten); seit Juni 2026 hat videos.insert einen eigenen Quota-Topf. Für ein bis drei Videos pro Tag reicht das Standardkontingent bei allen drei Plattformen.

## Architektur & Projektstruktur
Eine dünne CLI lädt Video und YAML, prüft beides und reicht es an drei Uploader weiter, die dieselbe Schnittstelle erfüllen. Eine weitere Plattform kommt später als neue Datei in platforms/ dazu, ohne den Rest anzufassen.
Nur ein Video, das die Validierung besteht, erreicht die Uploader; Tokens, Historie und Wiederholungen teilen sich alle drei.
shortpost/
├── CLAUDE.md
├── pyproject.toml          # uv, Abhängigkeiten, Befehl "shortpost"
├── .env.example            # Vorlage für Client-IDs und Secrets
├── docs/konzept.md         # dieses Dokument
├── spikes/                 # Phase 0: Wegwerf-Skripte, nicht Teil des Tools
├── src/shortpost/
│   ├── cli.py              # Typer: check, post, auth, history
│   ├── config.py           # Endpunkte, API-Versionen, Pfade
│   ├── meta.py             # PostMeta (Pydantic) + YAML laden
│   ├── validate.py         # ffprobe, Limits, faststart-Remux
│   ├── auth.py             # OAuth-Flows, keyring
│   ├── history.py          # SQLite-Historie
│   ├── retry.py            # tenacity-Regeln
│   ├── hosting.py          # Instagram: Video kurz in R2/S3, signierte URL, danach löschen
│   └── platforms/
│       ├── base.py         # Uploader-Protokoll, UploadResult
│       ├── instagram.py
│       ├── youtube.py
│       └── tiktok.py       # nur Entwurf (inbox)
└── tests/class Uploader(Protocol):
    name: str  # "instagram" | "youtube" | "tiktok"

    def check(self, video: Path, meta: PostMeta) -> list[Issue]: ...
    def upload(self, video: Path, meta: PostMeta,
               progress: Callable[[int, int], None]) -> UploadResult: ...
    def status(self, remote_id: str) -> UploadResult: ...Abhängigkeiten: typer, httpx, google-api-python-client, google-auth-oauthlib, pydantic, pyyaml, tenacity, keyring, rich, boto3 (für R2/S3); zum Entwickeln pytest, respx, ruff, mypy. Dazu ffmpeg/ffprobe als Systemprogramme.

## Authentifizierung & Token-Verwaltung
Jede Plattform braucht einmal eine Anmeldung (shortpost auth youtube|instagram|tiktok): bei YouTube und TikTok im Browser, bei Instagram per Token aus dem App-Dashboard. Danach erneuert das Tool die Tokens selbst und legt sie im Schlüsselbund des Betriebssystems ab.
 | Plattform
 | Login-Ablauf
 | Laufzeit
 | Erneuerung

 | YouTube
 | OAuth Installed-App-Flow mit lokalem Redirect (google-auth-oauthlib, run_local_server)
 | Refresh Token im Status „Testing“ nur 7 Tage, im Status „In production“ unbefristet bis Widerruf oder ca. 6 Monate ohne Nutzung (Google)
 | Automatisch durch die Client-Bibliothek

 | Instagram (Instagram Login)
 | Token im App-Dashboard erzeugen (dein Konto als Instagram-Tester); einen kurzlebigen Token tauscht shortpost per ig_exchange_token gegen einen langlebigen
 | 60 Tage
 | Automatisch: GET graph.instagram.com/refresh_access_token, sobald der Token älter als 24 h ist; danach gilt er wieder 60 Tage (Refresh Access Token)

 | TikTok
 | Login Kit Desktop mit PKCE (code_verifier ist Pflicht), lokaler Redirect
 | Access Token 24 h, Refresh Token 365 Tage
 | POST /v2/oauth/token/ mit grant_type=refresh_token; einen neu gelieferten Refresh Token immer übernehmen

- Speicherung: Client-IDs, Secrets und R2-Zugangsdaten in .env (steht in .gitignore), Tokens über die Bibliothek keyring im Schlüsselbund von macOS, Windows oder Linux.

- Vor jedem Upload: Token prüfen und erneuern, wenn er in weniger als einer Stunde abläuft. Bei HTTP 401 einmal erneuern, danach mit klarer Meldung abbrechen („Bitte shortpost auth tiktok ausführen“).

- Google-Projekt: Nach dem ersten erfolgreichen Test den OAuth-Zustimmungsbildschirm auf „In production“ stellen, sonst ist jede Woche ein neuer Login nötig.


## Upload-Ablauf je Plattform
Alle drei Uploads laufen asynchron: Datei hochladen, Verarbeitung abfragen, erst dann gilt der Post als fertig. Code-Skizzen dazu stehen im Tab ; Fehlerbehandlung und Retries kommen aus retry.py (siehe Fehlerbehandlung).

### Instagram Reel (Instagram Login, über kurzes Hosting)
- Video in den Bucket laden (hosting.py) und eine signierte URL erzeugen, gültig eine Stunde.

- Container anlegen: POST graph.instagram.com/{ig_id}/media mit media_type=REELS und video_url → Container-ID. Meta lädt das Video selbst von der URL.

- status_code des Containers abfragen, bis FINISHED (Meta empfiehlt einmal pro Minute, höchstens 5 Minuten).

- Datei im Bucket löschen.

- POST /{ig_id}/media_publish mit creation_id → Media-ID des Reels.

Hosting: ein privater Bucket bei Cloudflare R2 (S3-kompatibel, ohne Gebühren für ausgehenden Traffic) oder AWS S3. Die signierte URL muss ohne Login und ohne Weiterleitung abrufbar sein, weil Meta das Video selbst herunterlädt. Eine Lifecycle-Regel löscht übrig gebliebene Dateien nach einem Tag, etwa nach einem Absturz.

### YouTube Short
- videos.insert mit part=snippet,status als resumable Upload in 8-MB-Stücken.

- Antwort enthält die Video-ID; ein hochkantes oder quadratisches Video bis 3 Minuten wird automatisch als Short eingestuft – einen eigenen Shorts-Parameter gibt es nicht.

- Optional geplant veröffentlichen: status.publishAt setzen, privacyStatus muss dann private sein.


### TikTok
Nur ein Modus: Entwurf. shortpost lädt das Video in deinen TikTok-Posteingang (Scope video.upload, Endpunkt /v2/post/publish/inbox/video/init/). Du tippst in der App auf die Benachrichtigung, ergänzt die Caption und auf Wunsch einen Sound aus TikToks Bibliothek und veröffentlichst selbst.
Die Entwurfs-API überträgt nur das Video, keinen Text. shortpost gibt Caption und Hashtags deshalb am Ende zum Kopieren aus. Direkt veröffentlichen (direct) entfällt: Es braucht das Audit, das ein Eigen-Tool kaum bekommt (siehe Risiken).
Danach POST /v2/post/publish/status/fetch/ abfragen (max. 30 Aufrufe pro Minute): PROCESSING_UPLOAD → SEND_TO_USER_INBOX oder FAILED mit fail_reason. Die upload_url gilt eine Stunde (Media Transfer Guide, Get Post Status).

## Metadaten, Video-Anforderungen & Validierung
Neben jedem Video liegt eine gleichnamige YAML-Datei. shortpost check video.mp4 prüft Datei und Texte gegen die Limits aus dem Plattform-Überblick, bevor ein einziges Byte hochgeladen wird.
# clip-017.yaml – liegt neben clip-017.mp4
title: "Drei Fakten über Oktopusse"
description: |
  Warum Oktopusse drei Herzen haben – in 50 Sekunden.
hashtags: [wissenschaft, tiere, fakten]
ai_generated: false          # YouTube: containsSyntheticMedia; TikTok: KI-Label in der App
platforms:                   # fehlt eine Plattform, wird sie übersprungen
  instagram:
    share_to_feed: true
    caption: null            # null = gemeinsamer Text aus description + hashtags
  youtube:
    privacy: public          # public | unlisted | private
    category_id: "27"        # Bildung
    made_for_kids: false
    publish_at: null         # optional, nur YouTube: z. B. 2026-10-20T18:00:00+02:00
  tiktok:
    caption: null            # wird nur zum Kopieren ausgegeben (Entwurf)Die YAML wird mit Pydantic in ein PostMeta-Objekt geladen; Tippfehler in Feldnamen brechen sofort mit Zeilenangabe ab.
Blockierende Fehler (Upload startet nicht):
- Video-Codec nicht H.264 oder HEVC, Bildrate außerhalb 23–60 fps (per ffprobe).

- Dauer oder Größe über dem Limit der Zielplattform.

- YouTube-Titel über 100 Zeichen oder mit < bzw. >, Beschreibung über 5.000 Bytes, alle Tags zusammen über 500 Zeichen (videos-Ressource).

- Instagram-Caption über 2.200 Zeichen, 30 Hashtags oder 20 @-Erwähnungen; TikTok-Caption über 2.200 UTF-16-Einheiten (len(s.encode("utf-16-le")) // 2).

- Instagram: Bitrate über 25 Mbit/s oder Breite über 1.920 px. shortpost zeigt dann den passenden ffmpeg-Befehl zum Neu-Encodieren (H.264, AAC 48 kHz).

Warnungen (Upload läuft, Hinweis im Terminal):
- Seitenverhältnis nicht 9:16 – Instagram schneidet zu.

- YouTube: Querformat oder länger als 3 Minuten → wird ein normales Video statt Short.

Automatisch vor dem Instagram-Upload: verlustfrei remuxen, damit das moov-Atom vorne steht (ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4). Die Kopie landet in .shortpost/cache/, das Original bleibt unangetastet. Neu encodiert wird nie automatisch.

## Fehlerbehandlung, Status & Historie
Die Plattformen laufen nacheinander und unabhängig voneinander: Scheitert TikTok, wird YouTube trotzdem fertig. Am Ende steht eine Übersicht pro Plattform; schlägt eine fehl, endet das Programm mit Exit-Code 1.
 | Fehlerart
 | Beispiele
 | Verhalten

 | Vorübergehend
 | Netzwerkabbruch, HTTP 5xx, 429, TikTok internal
 | Bis zu 5 Versuche, Wartezeit exponentiell (2, 4, 8 … max. 60 s)

 | Token abgelaufen
 | HTTP 401, access_token_invalid
 | Einmal erneuern, sonst Abbruch mit Hinweis auf shortpost auth

 | Tageslimit
 | YouTube quotaExceeded, TikTok spam_risk_too_many_posts, Instagram-Publish-Limit
 | Kein Retry, Meldung „morgen erneut“

 | Endgültig
 | Formatfehler, spam_risk_text, auth_removed, uploadLimitExceeded
 | Kein Retry, Fehlertext der API anzeigen

Nur sichere Schritte wiederholen. Chunk-Uploads und Statusabfragen sind wiederholbar. Ein erneutes media_publish bei Instagram könnte doppelt posten – vorher den Status prüfen (Container PUBLISHED = schon online). Ein erneutes inbox/video/init bei TikTok erzeugt höchstens einen zweiten Entwurf, keinen zweiten Post. Für YouTube übernimmt request.next_chunk(num_retries=5) die Wiederholung.
Status abfragen:
- Instagram: status_code des Containers einmal pro Minute, höchstens 5 Minuten.

- TikTok: status/fetch alle 10 Sekunden (Limit 30 pro Minute), bis SEND_TO_USER_INBOX oder FAILED. Einen öffentlichen Link gibt es erst, wenn du den Entwurf in der App veröffentlichst; die Historie vermerkt bis dahin „Entwurf gesendet“.

- YouTube: optional videos.list mit part=processingDetails, bis processingStatus = succeeded.

Upload-Historie in SQLite (~/.shortpost/history.db), eine Zeile pro Video und Plattform: SHA-256 der Datei, Plattform, Status, Remote-ID (Container-, Publish- oder Video-ID), Link, Zeitstempel, Fehlertext.
- Vor jedem Upload: Gibt es für diesen Hash und diese Plattform schon published (bei TikTok: Entwurf gesendet), wird übersprungen (außer mit --force). So postet ein zweiter Aufruf nichts doppelt.

- Abgebrochene Läufe setzt ein erneutes shortpost post fort; einen eigenen resume-Befehl gibt es nicht. Damit dabei kein zweites Reel entsteht, speichert shortpost die Instagram-Container-ID vor dem Veröffentlichen und prüft beim nächsten Lauf deren Status.

- shortpost history zeigt die letzten Uploads mit Links.

Logging: Fortschrittsbalken mit rich im Terminal; Fehler zusätzlich als Textzeilen in ~/.shortpost/shortpost.log. Tokens und Secrets landen nie im Log, weil shortpost keine Header und keine Request-Inhalte protokolliert.

## Setup-Checkliste
Diese Schritte erledigst du einmal von Hand in den Entwicklerportalen, bevor Claude Code mit dem Programmieren beginnt. Bei TikTok reicht für den Anfang eine Sandbox; eine Prüfung beantragst du nur, falls Phase 0 zeigt, dass sie nötig ist.
Instagram (Meta)
- Instagram-Konto auf Professional umstellen (Creator oder Business)

- Auf developers.facebook.com eine App anlegen und das Produkt Instagram mit „API-Setup mit Instagram-Login“ hinzufügen

- Unter App-Rollen dein Instagram-Konto als Instagram-Tester eintragen und die Einladung in der Instagram-App annehmen (Einstellungen → Apps und Websites → Tester-Einladungen; Menünamen können abweichen)

- Berechtigungen instagram_business_basic und instagram_business_content_publish hinzufügen – für das eigene Konto mit Rolle in der App reicht in der Regel Standard Access

- Im API-Setup einen Token für dein Konto erzeugen; shortpost auth instagram übernimmt ihn und liest die Instagram-User-ID aus

YouTube (Google Cloud)
- Google-Cloud-Projekt anlegen und „YouTube Data API v3“ aktivieren

- OAuth-Zustimmungsbildschirm (extern) einrichten, Scope youtube.upload, eigenes Konto als Testnutzer

- OAuth-Client vom Typ „Desktop-App“ erstellen, client_secret.json herunterladen

- Nach dem ersten Test den Status auf „In production“ stellen

- Falls Test-Uploads privat gesperrt bleiben: Audit- und Quota-Formular einreichen

TikTok
- Entwicklerkonto auf developers.tiktok.com anlegen und eine App registrieren

- Login Kit (Desktop, Redirect auf http://localhost) und Content Posting API hinzufügen

- Scopes user.info.basic und video.upload beantragen; eine Sandbox mit deinem Konto als Testnutzer anlegen

- Nur falls Entwürfe aus der Sandbox nicht öffentlich gehen: App Review für video.upload beantragen (TikTok prüft die App von Hand)

Hosting (für Instagram)
- Cloudflare-Konto anlegen und einen privaten R2-Bucket erstellen (alternativ AWS S3)

- API-Token mit Lese- und Schreibrecht nur für diesen Bucket erzeugen

- Lifecycle-Regel anlegen: Objekte nach einem Tag löschen

Lokal
- Python 3.12 oder neuer, ffmpeg und ffprobe installieren

- .env aus .env.example anlegen und Client-IDs, Secrets und R2-Zugangsdaten eintragen

- shortpost auth youtube, shortpost auth instagram, shortpost auth tiktok ausführen

- Erster Lauf mit --dry-run, danach ein Testvideo mit privater Sichtbarkeit


## Umsetzung mit Claude Code
Am besten in sechs Phasen. Phase 0 prüft zuerst mit Wegwerf-Skripten die drei Annahmen, an denen das Konzept hängt; fällt eine, ändert sich der Plan, bevor Code fürs Tool entsteht. Danach kommt eine Plattform nach der anderen – YouTube zuerst, weil die Python-Bibliothek den meisten Aufwand abnimmt. Lege dieses Konzept als docs/konzept.md ins Repo (Export als Markdown) und die folgende CLAUDE.md ins Hauptverzeichnis.
# shortpost

CLI, das fertige Hochformat-Videos über die offiziellen APIs als
Instagram Reel und YouTube Short hochlädt und als TikTok-Entwurf
in den Posteingang legt. Konzept: docs/konzept.md

## Stack
- Python 3.12, uv, Typer (CLI), httpx, google-api-python-client,
  google-auth-oauthlib, pydantic, PyYAML, tenacity, keyring, rich,
  boto3 (Cloudflare R2 als Zwischenablage für Instagram)
- Tests: pytest + respx (HTTP-Mocks); ffmpeg/ffprobe als externe Tools

## Struktur
src/shortpost/cli.py, config.py, meta.py, validate.py, auth.py, history.py, retry.py, hosting.py
src/shortpost/platforms/base.py, instagram.py, youtube.py, tiktok.py
spikes/ = Wegwerf-Skripte aus Phase 0, nie importieren

## Regeln
- Jede Plattform implementiert `Uploader` aus platforms/base.py;
  cli.py kennt keine Plattform-Details
- Instagram nur über Instagram Login (graph.instagram.com) mit video_url;
  hosting.py löscht jedes Video, sobald Meta es abgeholt hat
- TikTok nur als Entwurf (inbox/video/init), kein Direct Post
- Plattformen nacheinander hochladen; ein erneutes `post` setzt
  abgebrochene Läufe über die Historie fort
- Keine Secrets in Code, Logs oder Tests; Tokens nur über keyring
- Endpunkte und API-Versionen als Konstanten in config.py
- Nur idempotente Schritte automatisch wiederholen (retry.py)
- Tests machen nie echte Uploads
- Code-Skizzen aus dem Konzept sind Orientierung, keine Vorlage
- Bei Unklarheit zur API: offizielle Doku lesen (Links in docs/konzept.md), nicht raten
- Vor jedem Commit: uv run ruff check · uv run mypy src · uv run pytest | Phase
 | Ergebnis
 | Fertig, wenn …

 | 0 Machbarkeit
 | Je ein Wegwerf-Skript in spikes/ lädt das Testvideo hoch: YouTube nicht gelistet, Instagram als Reel über eine signierte Bucket-URL, TikTok als Entwurf aus der Sandbox
 | Die drei Fragen unten sind beantwortet

 | 1 Gerüst
 | Projekt, CLI-Befehle als Stubs, PostMeta, shortpost check mit ffprobe
 | check bewertet ein Testvideo korrekt, Tests grün

 | 2 YouTube
 | OAuth, resumable Upload, Fortschrittsanzeige
 | Ein privates Testvideo ist auf dem Kanal

 | 3 Instagram
 | Token-Import, Hosting, Container, Polling, Publish, Aufräumen
 | Ein Test-Reel ist online

 | 4 TikTok
 | Login mit PKCE, Upload als Entwurf, Caption zum Kopieren
 | Ein Entwurf liegt im TikTok-Posteingang

 | 5 Robustheit
 | Historie mit Hash-Prüfung, Retries, Fortsetzen per erneutem post
 | Ein zweiter post nach einem Abbruch postet nichts doppelt

Phase 0 beantwortet drei Fragen, bevor Code fürs Tool entsteht:
 | Frage
 | Wenn ja
 | Wenn nein

 | Bleibt ein nicht gelistetes YouTube-Video aus dem ungeprüften Projekt sichtbar?
 | Weiter wie geplant
 | Audit-Formular einreichen; bis dahin nur privat

 | Lässt sich der TikTok-Entwurf aus der Sandbox in der App öffentlich posten?
 | Entwurfsmodus wie geplant
 | App Review beantragen, einen Dienst nutzen (siehe Kaufen statt bauen) oder TikTok von Hand

 | Holt Meta das Video von der signierten Bucket-URL ab?
 | Weiter wie geplant
 | Prüfen, ob die URL ohne Login abrufbar ist; sonst Facebook-Seite anlegen und auf Facebook Login wechseln

Testvideo für alle Phasen (5 s, 1080×1920, mit Ton):
ffmpeg -f lavfi -i testsrc=size=1080x1920:rate=30 -f lavfi -i sine=frequency=440 \
  -t 5 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest test.mp4Prompts zum Kopieren – jeweils erst einen Plan zeigen lassen, dann umsetzen:
- Phase 0: „Lies CLAUDE.md und docs/konzept.md. Schreib unter spikes/ drei kurze Wegwerf-Skripte ohne Projektgerüst, die test.mp4 hochladen: YouTube als ‚nicht gelistet‘, Instagram als Reel über eine signierte Bucket-URL, TikTok als Entwurf aus der Sandbox. Secrets kommen aus .env. Zeig mir zuerst deinen Plan.“

- Phase 1: „Lies CLAUDE.md und docs/konzept.md. Lege das Projekt mit uv an. Baue die Typer-CLI mit den Befehlen check, post, auth, history als Stubs, das Pydantic-Modell PostMeta für die YAML-Datei aus dem Abschnitt ‚Metadaten‘ und validate.py mit ffprobe. Schreib Tests mit dem Testvideo. Zeig mir zuerst deinen Plan.“

- Phase 2: „Setze platforms/youtube.py nach den Abschnitten ‚Authentifizierung‘ und ‚Upload-Ablauf → YouTube Short‘ um: Installed-App-Flow, Token im keyring, resumable Upload mit rich-Fortschritt, Fehlerklassen aus ‚Fehlerbehandlung‘. Tests mit gemockten Antworten; den echten Upload mache ich selbst.“

- Phase 3: „Setze platforms/instagram.py und hosting.py um: Instagram Login, Video per signierter URL aus dem R2-Bucket, Polling, Datei löschen, Publish. shortpost auth instagram soll den im App-Dashboard erzeugten Token annehmen, bei Bedarf gegen einen langlebigen tauschen und ihn automatisch verlängern, sobald er älter als 24 h ist.“

- Phase 4: „Setze platforms/tiktok.py um: Desktop-Login mit PKCE und lokalem Redirect, Upload als Entwurf über inbox/video/init, Status abfragen bis SEND_TO_USER_INBOX. Caption und Hashtags am Ende zum Kopieren ausgeben. Chunk-Berechnung exakt nach dem Konzept, mit Tests für 3 MB, 7 MB und 50.000.123 Byte.“

- Phase 5: „Ergänze history.py (SQLite) und retry.py. post prüft vor jedem Schritt die Historie, damit ein zweiter Aufruf nach einem Abbruch fortsetzt statt doppelt zu posten; die Instagram-Container-ID wird vor media_publish gespeichert. Plattformen nacheinander, am Ende eine Tabelle mit Status und Links.“


## Risiken & Entscheidungen
Das größte Risiko ist TikTok: Die Richtlinien verlangen, dass eine API-App für ein breites Publikum gedacht ist und nicht nur für private Nutzung (Content Sharing Guidelines). Ein reines Eigen-Tool bekommt deshalb kaum das Audit für Direct Post. shortpost setzt darum nur auf Entwürfe; ob die schon aus der Sandbox öffentlich gehen, klärt Phase 0.
 | Risiko
 | Auswirkung
 | Gegenmaßnahme

 | TikTok-Entwürfe aus der Sandbox lassen sich nicht öffentlich posten
 | Kein TikTok-Upload per shortpost
 | Phase 0 klärt es; sonst App Review, ein Dienst (siehe unten) oder TikTok von Hand

 | YouTube sperrt Uploads ungeprüfter Projekte doch auf privat
 | Shorts nur für dich sichtbar
 | Phase 0 klärt es; sonst Audit-Formular

 | Token läuft unbemerkt ab
 | Upload scheitert beim nächsten Lauf
 | shortpost auth --check vor jedem Lauf, Warnung 7 Tage vor Ablauf

 | API-Änderungen
 | Endpunkte, Limits oder Quoten ändern sich (YouTube-Quote zweimal seit Dez. 2025)
 | Versionen als Konstanten, Tests mit gemockten Antworten, Changelogs der drei Plattformen im Blick behalten

 | Lizenzierte Musik im Video
 | Ton stumm geschaltet oder Post blockiert; Instagram erlaubt Musik-Tagging per API nur für Originalton
 | Eigene oder lizenzfreie Musik verwenden; bei TikTok den Sound im Entwurf in der App wählen

 | Spam-Erkennung
 | TikTok bricht bei spam_risk* ab, Instagram drosselt
 | Nicht mehr als ein paar Posts pro Tag, keine identischen Captions in Serie

 | Meta kann das Video nicht abholen, oder es bleibt im Bucket liegen
 | Container endet mit ERROR, oder das Video ist länger abrufbar als nötig
 | URL vor dem Anlegen des Containers selbst abrufen; Lifecycle-Regel löscht Reste nach einem Tag

Kaufen statt bauen: Soll TikTok ohne Tipp in der App öffentlich posten oder alles zeitgesteuert laufen, ist ein Dienst mit bereits geprüfter TikTok-App der kürzere Weg. Ayrshare etwa veröffentlicht per API direkt und standardmäßig öffentlich auf TikTok; Planungs-Apps wie Buffer oder Metricool decken Ähnliches ohne Code ab. Das kostet ein monatliches Abo, und Videos wie Tokens laufen über einen Dritten. Dank der Uploader-Schnittstelle lässt sich so ein Dienst später nur für TikTok einhängen, als weitere Datei in platforms/.
Entscheidungen
- Instagram-Login (entschieden): Instagram Login, weil keine Facebook-Seite mit dem Konto verknüpft ist. Dafür liegt jedes Video kurz in einem Bucket, bis Meta es abgeholt hat (siehe Upload-Ablauf).

- TikTok-Modus (entschieden): nur Entwurf, mit einem Tipp in der App. Wer ohne Tipp posten will, braucht das Audit oder einen Dienst (siehe Kaufen statt bauen).

- Zeitplanung (entschieden): nur sofort posten. Die APIs von Instagram und TikTok können nicht zeitgesteuert veröffentlichen; das Tool müsste dafür als geplanter Job laufen. Bei YouTube bleibt publish_at optional.

- Texte (entschieden): ein gemeinsamer Text aus Beschreibung und Hashtags; pro Plattform kann die YAML ihn mit caption überschreiben.


## Quellen
- Instagram Platform – Content Publishing

- Instagram Platform – IG User Media (Reels-Spezifikation)

- Instagram API with Instagram Login (ohne Facebook-Seite)

- Instagram – Refresh Access Token

- YouTube Data API – Videos: insert

- YouTube Data API – Videos-Ressource

- YouTube Data API – Revision History

- TikTok – Content Posting API, Direct Post

- TikTok – Content Posting API, Upload (Entwurf)

- TikTok – Upload-Referenz (inbox/video/init)

- TikTok – Direct Post Referenz

- TikTok – Media Transfer Guide

- TikTok – Get Post Status

- TikTok – User Access Token Management

- Ayrshare – TikTok-API

