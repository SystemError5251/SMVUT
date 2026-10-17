#!/usr/bin/env bash
# Prüft die APK in einem laufenden Android-Emulator (GitHub Actions, siehe
# .github/workflows/android.yml): installieren, Selbsttest, Oberfläche, Screenshots.
#
#   bash scripts/android-smoke-test.sh dist-apk/Terminal-0.1.0.apk
set -euo pipefail

APK="$1"
PKG=io.github.systemerror5251.terminal_app
OUT=test-output
mkdir -p "$OUT"

adb wait-for-device
adb install -r "$APK"
adb logcat -c

ui_dump() {  # aktuelle Oberfläche als XML nach $OUT/$1.xml
    adb shell uiautomator dump /sdcard/ui.xml >/dev/null
    adb pull /sdcard/ui.xml "$OUT/$1.xml" >/dev/null
}

expect_text() {  # Datei, Text – ohne Groß-/Kleinschreibung (Buttons sind in Großbuchstaben,
                 # casefold statt grep -i, damit auch „ä“/„Ä“ unabhängig vom Locale passen)
    if ! python3 -c 'import sys; t = open(sys.argv[1], encoding="utf-8").read().casefold()
sys.exit(sys.argv[2].casefold() not in t)' "$OUT/$1.xml" "$2"; then
        echo "FEHLER: Text „$2“ nicht auf dem Bildschirm ($1)" >&2
        return 1
    fi
}

tap_text() {  # Datei, Text: Mittelpunkt des Elements mit diesem Text antippen
    local xy
    xy=$(python3 - "$OUT/$1.xml" "$2" <<'EOF'
import re, sys
import xml.etree.ElementTree as ET
for node in ET.parse(sys.argv[1]).iter("node"):
    if node.get("text") == sys.argv[2]:
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds")))
        print((x1 + x2) // 2, (y1 + y2) // 2)
        break
EOF
)
    [ -n "$xy" ] || { echo "FEHLER: „$2“ zum Antippen nicht gefunden" >&2; return 1; }
    adb shell input tap $xy
}

# 1. App mit Selbsttest starten (Python-Umgebung, Speicher, MP4, TLS, Java-Anbindung)
adb shell am start -W -n "$PKG/org.beeware.android.MainActivity" --ez selftest true
result=""
for _ in $(seq 1 120); do
    result=$(adb logcat -d -s python.stdout:I | grep "SELFTEST RESULT" | tail -1 || true)
    [ -n "$result" ] && break
    sleep 2
done
adb logcat -d -s python.stdout:I python.stderr:W > "$OUT/python.txt"
grep "SELFTEST" "$OUT/python.txt" || true

# 2. Oberfläche: Tab „Hochladen“
sleep 2
ui_dump hochladen
adb exec-out screencap -p > "$OUT/screenshot-hochladen.png"
expect_text hochladen "Video auswählen"
expect_text hochladen "YouTube Short"

# 3. Tab „Konten“ öffnen: Status ohne Anmeldung. Die Zwischenablage-Vorschau von Android
#    (vom Selbsttest) liegt kurz über der Tab-Leiste – darum notfalls mehrmals tippen.
for _ in 1 2 3; do
    tap_text hochladen "Konten"
    sleep 4
    ui_dump konten
    grep -q "nicht angemeldet" "$OUT/konten.xml" && break
done
adb exec-out screencap -p > "$OUT/screenshot-konten.png"
expect_text konten "YouTube: nicht angemeldet"
expect_text konten "client_secret.json importieren"

# 4. Abstürze? Python-Fehler?
adb logcat -d > "$OUT/logcat.txt"
if grep -q "FATAL EXCEPTION" "$OUT/logcat.txt"; then
    echo "FEHLER: App ist abgestürzt (siehe logcat.txt)" >&2
    exit 1
fi

case "$result" in
    *PASS*) echo "Emulator-Test bestanden: $result" ;;
    *) echo "FEHLER: Selbsttest ${result:-ohne Ergebnis}" >&2; exit 1 ;;
esac
