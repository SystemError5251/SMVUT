#!/data/data/com.termux/files/usr/bin/bash
# Einrichtung von SMVUT (Social Media Upload Tool) in Termux (Android).
# Aufruf aus dem Projektordner:  bash scripts/termux-setup.sh
set -euo pipefail

if [ -z "${PREFIX:-}" ] || [[ "$PREFIX" != *com.termux* ]]; then
    echo "Dieses Skript ist für Termux gedacht." >&2
    exit 1
fi

cd "$(dirname "$0")/.."

echo "==> Pakete installieren (python, python-cryptography, git)"
# Erst alle Pakete aktualisieren: neue Pakete brauchen die aktuellen
# Systembibliotheken, sonst "CANNOT LINK EXECUTABLE ... cannot locate symbol"
pkg upgrade -y
# python-cryptography kommt fertig gebaut aus Termux (google-auth braucht es;
# per pip müsste es mit Rust kompiliert werden)
# ffmpeg wird nicht gebraucht: Videos liest SMVUT selbst (src/terminal/mp4.py)
pkg install -y python python-cryptography git

echo "==> SMVUT installieren (reines Python, kein Kompilieren nötig)"
# Hinweis: pip in Termux nie mit 'pip install --upgrade pip' aktualisieren
pip install -e .

echo "==> Konfiguration anlegen"
mkdir -p "$HOME/.terminal"
chmod 700 "$HOME/.terminal"
if [ ! -f "$HOME/.terminal/.env" ]; then
    cp .env.example "$HOME/.terminal/.env"
    chmod 600 "$HOME/.terminal/.env"
    echo "    ~/.terminal/.env angelegt – Zugangsdaten dort eintragen: nano ~/.terminal/.env"
else
    echo "    ~/.terminal/.env existiert schon – unverändert gelassen"
fi

if [ ! -d "$HOME/storage" ]; then
    echo "==> Zugriff auf Handy-Speicher (Videos in DCIM/Download) erlauben"
    termux-setup-storage || echo "    Später nachholen mit: termux-setup-storage"
fi

echo
echo "Fertig. Nächste Schritte:"
echo "  nano ~/.terminal/.env              # Zugangsdaten eintragen"
echo "  smvut auth --check"
echo "  smvut check ~/storage/dcim/Camera/video.mp4"
