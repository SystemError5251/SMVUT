# Terminal

CLI, das fertige Hochformat-Videos über die offiziellen APIs als
Instagram Reel und YouTube Short hochlädt und als TikTok-Entwurf
in den Posteingang legt. Konzept (aus dem Projekt „WhatIf“): docs/konzept.md

## Stack
- Python 3.12+, uv, Typer (CLI), httpx, google-api-python-client,
  google-auth-oauthlib, PyYAML, tenacity, keyring, rich,
  boto3 (Cloudflare R2/S3 als Zwischenablage für Instagram), python-dotenv
- Muss auf Android/Termux laufen: nur reine Python-Abhängigkeiten (kein pydantic,
  nichts, was Rust/C braucht; cryptography kommt dort aus `pkg`).
  Test in echtem Termux: Docker-Image termux/termux-docker
- Tests: pytest + respx (HTTP-Mocks); ffmpeg/ffprobe als externe Tools

## Struktur
src/terminal/cli.py, config.py, meta.py, validate.py, auth.py, history.py, retry.py, hosting.py, errors.py
src/terminal/platforms/base.py, instagram.py, youtube.py, tiktok.py

## Regeln
- Jede Plattform implementiert `Uploader` aus platforms/base.py;
  cli.py kennt keine Plattform-Details
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
