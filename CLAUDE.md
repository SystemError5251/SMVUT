# Terminal

CLI, das fertige Hochformat-Videos über die offiziellen APIs als
Instagram Reel und YouTube Short hochlädt und als TikTok-Entwurf
in den Posteingang legt. Konzept (aus dem Projekt „WhatIf“): docs/konzept.md

## Stack
- Python 3.12+, uv, Typer (CLI), httpx, google-auth-oauthlib, PyYAML, tenacity,
  keyring, rich, python-dotenv
- Bewusst ohne google-api-python-client und boto3 (zusammen ~140 MB in der APK):
  YouTube-Resumable-Upload und S3-Signatur (SigV4) sind direkt mit httpx umgesetzt;
  ohne ffmpeg: MP4 lesen/faststart in src/terminal/mp4.py
- Android-App: BeeWare Toga + Briefcase (src/terminal_app), Build: scripts/build-apk.sh
- Muss auf Android/Termux laufen: nur reine Python-Abhängigkeiten (kein pydantic,
  nichts, was Rust/C braucht; cryptography kommt dort aus `pkg`).
  Test in echtem Termux: Docker-Image termux/termux-docker
- Tests: pytest + respx (HTTP-Mocks), toga-dummy für die App; ffmpeg optional

## Struktur
src/terminal/cli.py, service.py (Ablauf für CLI und App), config.py, meta.py, validate.py,
mp4.py, auth.py, history.py, retry.py, hosting.py, errors.py
src/terminal/platforms/base.py, instagram.py, youtube.py, tiktok.py
src/terminal_app/app.py (Oberfläche), device.py (Android-Java-Aufrufe), forms.py, selftest.py

## Regeln
- Jede Plattform implementiert `Uploader` aus platforms/base.py;
  cli.py und die App kennen keine Plattform-Details, beide laufen über service.py
- Instagram nur über Instagram Login (graph.instagram.com) mit video_url;
  hosting.py löscht jedes Video, sobald Meta es abgeholt hat
- TikTok nur als Entwurf (inbox/video/init), kein Direct Post
- Plattformen nacheinander hochladen; ein erneutes `post` setzt
  abgebrochene Läufe über die Historie fort
- Keine Secrets in Code, Logs oder Tests; Tokens nur über TokenStore
  (keyring, auf Termux ~/.terminal/tokens.json mit Modus 600)
- Endpunkte und API-Versionen als Konstanten in config.py
- Nur idempotente Schritte automatisch wiederholen (retry.py)
- Tests machen nie echte Uploads
- Bei Unklarheit zur API: offizielle Doku lesen (Links in docs/konzept.md), nicht raten
- Vor jedem Commit: uv run ruff check · uv run mypy src · uv run pytest
