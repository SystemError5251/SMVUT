#!/usr/bin/env bash
# Baut die Android-App (APK) mit BeeWare Briefcase.
#
#   Voraussetzung: Python 3.13 und  pip install briefcase==0.4.6
#   bash scripts/build-apk.sh                 → dist-apk/Terminal-<version>.apk
#   ABI=arm64-v8a bash scripts/build-apk.sh   → nur für Handys (halb so groß)
#
# Beim allerersten Mal lädt Briefcase Java 17 und das Android SDK. Fragt es dabei
# nach der Android-SDK-Lizenz, einmal `briefcase create android` ohne --no-input
# ausführen und zustimmen.
set -euo pipefail
cd "$(dirname "$0")/.."

GRADLE=build/terminal_app/android/gradle

# Immer frisch erzeugen: `briefcase update` übernimmt Änderungen an den Gradle-Einstellungen
# aus pyproject.toml nicht (z. B. Signaturschlüssel)
rm -rf build/terminal_app/android
briefcase create android --no-input

# Der Gradle-Konfigurationscache des Briefcase-Templates scheitert am Kotlin-Plugin
sed -i 's/^org.gradle.configuration-cache=true$/org.gradle.configuration-cache=false/' \
    "$GRADLE/gradle.properties"
if [ -n "${ABI:-}" ]; then
    sed -i "s/abiFilters .*/abiFilters \"$ABI\"/" "$GRADLE/app/build.gradle"
fi

# Maven Central antwortet gelegentlich mit 429 (zu viele Anfragen): dann erneut versuchen
for attempt in 1 2 3; do
    if briefcase build android --no-input; then
        break
    fi
    if [ "$attempt" = 3 ]; then
        exit 1
    fi
    echo "Build fehlgeschlagen – neuer Versuch in $((30 * attempt)) s" >&2
    sleep $((30 * attempt))
done

VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['tool']['briefcase']['version'])")
mkdir -p dist-apk
TARGET="dist-apk/Terminal-$VERSION${ABI:+-$ABI}.apk"
cp "$GRADLE/app/build/outputs/apk/debug/app-debug.apk" "$TARGET"
echo "APK: $TARGET ($(du -h "$TARGET" | cut -f1))"
