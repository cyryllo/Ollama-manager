#!/bin/bash
# =============================================================================
#  Instalator Ollama Managera.
#
#  WHAT: instaluje zależności Pythona, kopiuje aplikację do katalogu
#        użytkownika i dodaje wpis w menu aplikacji (kategoria Narzędzia).
#  WHY:  wszystko ląduje w $HOME - bez sudo, bez dotykania systemu poza
#        jednym plikiem .desktop, zgodnie z zasadą "bez roota, gdzie się da".
#
#  Uruchomienie: ./install.sh
# =============================================================================
set -euo pipefail

KATALOG_DOCELOWY="$HOME/.local/share/ollama-manager"
PLIK_DESKTOP="$HOME/.local/share/applications/ollama-manager.desktop"
KATALOG_SKRYPTU="$(dirname "$(readlink -f "$0")")"
SKRYPT_ZRODLOWY="$KATALOG_SKRYPTU/ollama_manager.py"
KATALOG_LANG_ZRODLOWY="$KATALOG_SKRYPTU/lang"

if [ ! -f "$SKRYPT_ZRODLOWY" ]; then
    echo "Nie widzę ollama_manager.py obok install.sh - uruchom skrypt z katalogu repo." >&2
    exit 1
fi

echo "Instaluję Ollama Manager..."

# 1) Zależności Pythona - bez roota (--user)
python3 -m pip install --user --upgrade PyQt6 requests

# 2) Kopiowanie skryptu do stabilnej lokalizacji (przeżyje usunięcie repo/klona)
mkdir -p "$KATALOG_DOCELOWY"
cp "$SKRYPT_ZRODLOWY" "$KATALOG_DOCELOWY/ollama_manager.py"
chmod +x "$KATALOG_DOCELOWY/ollama_manager.py"

# WHY: tłumaczenia interfejsu (lang/*.json) muszą leżeć obok skryptu -
#      _KATALOG_LANG w kodzie liczy się względem ollama_manager.py, nie CWD.
if [ -d "$KATALOG_LANG_ZRODLOWY" ]; then
    cp -r "$KATALOG_LANG_ZRODLOWY" "$KATALOG_DOCELOWY/"
fi

# 3) Wpis w menu aplikacji - Categories=Utility to sekcja "Narzędzia" w KDE
mkdir -p "$(dirname "$PLIK_DESKTOP")"
cat > "$PLIK_DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Ollama Manager
Comment=Zarządzanie usługą i modelami Ollama
Exec=python3 "$KATALOG_DOCELOWY/ollama_manager.py"
Icon=applications-system
Categories=Utility;
Terminal=false
StartupNotify=true
EOF

# WHY: odświeża bazę menu od razu - bez tego KDE czasem widzi nowy wpis
#      dopiero po ponownym zalogowaniu.
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "Gotowe. Ollama Manager powinien być w menu aplikacji (Narzędzia)."
echo "Jeśli nie widać go od razu, wyloguj się i zaloguj ponownie."
