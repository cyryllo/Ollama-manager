#!/bin/bash
# =============================================================================
#  Build a .deb package for Ollama Manager.
#
#  WHAT: packs ollama_manager.py + lang/ into /usr/share, adds a launcher
#        script in /usr/bin and a .desktop entry in /usr/share/applications,
#        then assembles it all into a single .deb via dpkg-deb.
#  WHY:  a plain hand-written DEBIAN/control + dpkg-deb --build instead of
#        full debhelper/dh-python - this project is a single Python file,
#        it doesn't need a heavy packaging toolchain ("simplicity above all",
#        see CLAUDE.md). Dependencies go through apt (Depends), NOT pip like
#        install.sh - that's the standard on Debian/Ubuntu and avoids mixing
#        pip with the system Python.
#
#  Run: ./build-deb.sh
#  Output: ollama-manager_<version>_all.deb in the repo directory.
#  Installing the result: sudo apt install ./ollama-manager_<version>_all.deb
# =============================================================================
set -euo pipefail

PACKAGE_NAME="ollama-manager"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
cd "$SCRIPT_DIR"

if [ ! -f ollama_manager.py ]; then
    echo "Can't find ollama_manager.py next to build-deb.sh - run this script from the repo directory." >&2
    exit 1
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb not found - this script only works on Debian/Ubuntu (and derivatives)." >&2
    exit 1
fi

# WHY: package version ALWAYS synced with the WERSJA constant in the code -
#      a single source of truth, same as for any other version change.
VERSION="$(sed -n 's/^WERSJA = "\(.*\)"/\1/p' ollama_manager.py)"
if [ -z "$VERSION" ]; then
    echo "Could not extract WERSJA from ollama_manager.py" >&2
    exit 1
fi

echo "Building package ${PACKAGE_NAME} ${VERSION}..."

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

# --- Package layout -----------------------------------------------------------
mkdir -p "$BUILD_DIR/DEBIAN"
mkdir -p "$BUILD_DIR/usr/bin"
mkdir -p "$BUILD_DIR/usr/share/$PACKAGE_NAME"
mkdir -p "$BUILD_DIR/usr/share/applications"

# App + translations (see CLAUDE.md - lang/ MUST travel with the script,
# _KATALOG_LANG in the code is resolved relative to ollama_manager.py's location)
cp ollama_manager.py "$BUILD_DIR/usr/share/$PACKAGE_NAME/"
cp -r lang "$BUILD_DIR/usr/share/$PACKAGE_NAME/"

# WHY: a small wrapper instead of calling the full path straight from
#      .desktop - this also lets `ollama-manager` be run from a terminal.
cat > "$BUILD_DIR/usr/bin/$PACKAGE_NAME" <<EOF
#!/bin/sh
exec python3 /usr/share/$PACKAGE_NAME/ollama_manager.py "\$@"
EOF
chmod 755 "$BUILD_DIR/usr/bin/$PACKAGE_NAME"

# Menu entry - same category/icon as install.sh. Comment[pl]: the .desktop
# format supports per-language variants directly (see install.sh).
cat > "$BUILD_DIR/usr/share/applications/$PACKAGE_NAME.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ollama Manager
Comment=Manage the Ollama service and models
Comment[pl]=Zarządzanie usługą i modelami Ollama
Exec=$PACKAGE_NAME
Icon=applications-system
Categories=Utility;
Terminal=false
StartupNotify=true
EOF

# --- Package metadata -----------------------------------------------------------
# WHY: python3-pyqt6/python3-requests as Depends (apt), not pip - these are
#      the packages that provide the dependencies on Debian/Ubuntu (2026).
#      'pkexec' as its own package, NOT 'policykit-1' - polkit was split into
#      polkitd/pkexec, 'policykit-1' is now just a transitional metapackage.
cat > "$BUILD_DIR/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $VERSION
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

# WHY: refresh the menu database after install/removal - the same thing
#      install.sh does by hand after copying .desktop, here as package
#      maintainer scripts (apt runs them automatically after install/removal).
cat > "$BUILD_DIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications 2>/dev/null || true
EOF
chmod 755 "$BUILD_DIR/DEBIAN/postinst"

cat > "$BUILD_DIR/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications 2>/dev/null || true
EOF
chmod 755 "$BUILD_DIR/DEBIAN/postrm"

# --- Build ---------------------------------------------------------------------
# WHY: --root-owner-group records root:root ownership in the .deb without
#      needing to run this script under sudo (dpkg >= 1.19.0, long present
#      on Debian/Ubuntu).
DEB_FILE="${PACKAGE_NAME}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$BUILD_DIR" "$DEB_FILE"

echo "Done: $DEB_FILE"
echo "Install with: sudo apt install ./$DEB_FILE"
