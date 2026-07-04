#!/bin/bash
# =============================================================================
#  Budowa pakietu .deb dla Ollama Managera.
#
#  WHAT: pakuje ollama_manager.py + lang/ do /usr/share, dorzuca skrypt
#        startowy w /usr/bin i wpis .desktop w /usr/share/applications,
#        składa to w jeden plik .deb przez dpkg-deb.
#  WHY:  prosty ręczny DEBIAN/control + dpkg-deb --build zamiast pełnego
#        debhelper/dh-python - ten projekt to jeden plik Pythona, nie
#        potrzebuje ciężkiego toolchainu do budowania pakietów (zasada
#        "prostota ponad wszystko", patrz CLAUDE.md). Zależności idą jako
#        pakiety apt (Depends), NIE pip jak w install.sh - to standard na
#        Debianie/Ubuntu i unika mieszania pip-a z systemowym Pythonem.
#
#  Uruchomienie: ./build-deb.sh
#  Wynik: ollama-manager_<wersja>_all.deb w katalogu repo.
#  Instalacja wyniku: sudo apt install ./ollama-manager_<wersja>_all.deb
# =============================================================================
set -euo pipefail

NAZWA_PAKIETU="ollama-manager"
KATALOG_SKRYPTU="$(dirname "$(readlink -f "$0")")"
cd "$KATALOG_SKRYPTU"

if [ ! -f ollama_manager.py ]; then
    echo "Nie widzę ollama_manager.py obok build-deb.sh - uruchom skrypt z katalogu repo." >&2
    exit 1
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "Brak dpkg-deb - ten skrypt działa tylko na Debianie/Ubuntu (i pochodnych)." >&2
    exit 1
fi

# WHY: wersja pakietu ZAWSZE zsynchronizowana ze stałą WERSJA w kodzie -
#      jedno miejsce prawdy, tak jak przy każdej innej zmianie wersji.
WERSJA="$(sed -n 's/^WERSJA = "\(.*\)"/\1/p' ollama_manager.py)"
if [ -z "$WERSJA" ]; then
    echo "Nie udało się wyciągnąć WERSJA z ollama_manager.py" >&2
    exit 1
fi

echo "Buduję pakiet ${NAZWA_PAKIETU} ${WERSJA}..."

KATALOG_BUILD="$(mktemp -d)"
trap 'rm -rf "$KATALOG_BUILD"' EXIT

# --- Struktura pakietu -------------------------------------------------------
mkdir -p "$KATALOG_BUILD/DEBIAN"
mkdir -p "$KATALOG_BUILD/usr/bin"
mkdir -p "$KATALOG_BUILD/usr/share/$NAZWA_PAKIETU"
mkdir -p "$KATALOG_BUILD/usr/share/applications"

# Aplikacja + tłumaczenia (patrz CLAUDE.md - lang/ MUSI jechać ze skryptem,
# _KATALOG_LANG w kodzie liczy się względem lokalizacji ollama_manager.py)
cp ollama_manager.py "$KATALOG_BUILD/usr/share/$NAZWA_PAKIETU/"
cp -r lang "$KATALOG_BUILD/usr/share/$NAZWA_PAKIETU/"

# WHY: mały wrapper zamiast wołania pełnej ścieżki wprost z .desktop - dzięki
#      temu `ollama-manager` da się też odpalić ręcznie z terminala.
cat > "$KATALOG_BUILD/usr/bin/$NAZWA_PAKIETU" <<EOF
#!/bin/sh
exec python3 /usr/share/$NAZWA_PAKIETU/ollama_manager.py "\$@"
EOF
chmod 755 "$KATALOG_BUILD/usr/bin/$NAZWA_PAKIETU"

# Wpis w menu aplikacji - ta sama kategoria/ikona co install.sh
cat > "$KATALOG_BUILD/usr/share/applications/$NAZWA_PAKIETU.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ollama Manager
Comment=Zarządzanie usługą i modelami Ollama
Exec=$NAZWA_PAKIETU
Icon=applications-system
Categories=Utility;
Terminal=false
StartupNotify=true
EOF

# --- Metadane pakietu ---------------------------------------------------------
# WHY: python3-pyqt6/python3-requests jako Depends (apt), nie pip - to one
#      pakiety dostarczają zależności na Debianie/Ubuntu (2026). 'pkexec' jako
#      osobny pakiet, NIE 'policykit-1' - polkit rozdzielono na polkitd/pkexec,
#      'policykit-1' zostało tylko przejściowym metapakietem.
cat > "$KATALOG_BUILD/DEBIAN/control" <<EOF
Package: $NAZWA_PAKIETU
Version: $WERSJA
Section: utils
Priority: optional
Architecture: all
Depends: python3, python3-pyqt6, python3-requests, pkexec, systemd
Maintainer: Cyryl Sochacki <cyrylsochacki@gmail.com>
Homepage: https://github.com/cyryllo/Ollama-manager
Description: Ollama service and model manager for KDE (PyQt6)
 Desktop app for managing a local Ollama instance: start/stop the systemd
 service, autostart, list/download/delete models, Open WebUI integration,
 LiteLLM model aggregator, and Ollama environment tuning (context length,
 keep-alive, Vulkan/iGPU backend, ...). Requires Ollama itself (the app can
 install it with one click) plus systemd and polkit.
EOF

# WHY: odśwież bazę menu po (od)instalowaniu - to samo, co install.sh robi
#      ręcznie po skopiowaniu .desktop, tu jako skrypty pakietu (apt je
#      uruchamia automatycznie po instalacji/usunięciu).
cat > "$KATALOG_BUILD/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications 2>/dev/null || true
EOF
chmod 755 "$KATALOG_BUILD/DEBIAN/postinst"

cat > "$KATALOG_BUILD/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications 2>/dev/null || true
EOF
chmod 755 "$KATALOG_BUILD/DEBIAN/postrm"

# --- Budowa -------------------------------------------------------------------
# WHY: --root-owner-group zapisuje w .deb właściciela root:root bez potrzeby
#      budowania tego skryptu przez sudo (dpkg >= 1.19.0, dawno w Debianie/Ubuntu).
PLIK_DEB="${NAZWA_PAKIETU}_${WERSJA}_all.deb"
dpkg-deb --build --root-owner-group "$KATALOG_BUILD" "$PLIK_DEB"

echo "Gotowe: $PLIK_DEB"
echo "Instalacja: sudo apt install ./$PLIK_DEB"
