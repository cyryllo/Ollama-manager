#!/bin/bash
# =============================================================================
#  Ollama Manager installer.
#
#  WHAT: installs Python dependencies, copies the app to a user directory
#        and adds a menu entry (Utility category). Detects an existing
#        install - asks about updating (newer version in the repo),
#        reinstalling (same version), or warns before overwriting a newer
#        installed version with an older one. A separate flag removes the app.
#  WHY:  everything lands in $HOME - no sudo, no touching the system beyond
#        one .desktop file, per the "no root where avoidable" principle.
#
#  Run:      ./install.sh
#  Uninstall: ./install.sh --uninstall
# =============================================================================
set -euo pipefail

TARGET_DIR="$HOME/.local/share/ollama-manager"
DESKTOP_FILE="$HOME/.local/share/applications/ollama-manager.desktop"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
SOURCE_SCRIPT="$SCRIPT_DIR/ollama_manager.py"
SOURCE_LANG_DIR="$SCRIPT_DIR/lang"
TARGET_SCRIPT="$TARGET_DIR/ollama_manager.py"

_version_from_file() {
    # WHAT: extracts the value of the WERSJA constant from the given ollama_manager.py.
    sed -n 's/^WERSJA = "\(.*\)"/\1/p' "$1" 2>/dev/null || true
}

_compare_versions() {
    # WHAT: "older"/"same"/"newer" for version $1 relative to $2.
    # WHY:  plain string comparison would misorder e.g. "0.3.9" and "0.3.10"
    #       (alphabetically "9" > "1") - sort -V understands version numbers.
    if [ "$1" = "$2" ]; then
        echo "same"
    elif [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$1" ]; then
        echo "older"
    else
        echo "newer"
    fi
}

remove_app() {
    if [ ! -d "$TARGET_DIR" ] && [ ! -f "$DESKTOP_FILE" ]; then
        echo "Ollama Manager is not installed - nothing to remove." >&2
        exit 1
    fi
    echo "Removing Ollama Manager..."
    rm -rf "$TARGET_DIR"
    rm -f "$DESKTOP_FILE"
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo "Done. Ollama Manager has been removed (pip dependencies are kept - other tools may share them)."
}

# --- Removal flag --------------------------------------------------------------
if [ "${1:-}" = "--uninstall" ]; then
    remove_app
    exit 0
fi

if [ ! -f "$SOURCE_SCRIPT" ]; then
    echo "Can't find ollama_manager.py next to install.sh - run this script from the repo directory." >&2
    exit 1
fi

NEW_VERSION="$(_version_from_file "$SOURCE_SCRIPT")"

# --- Detecting an existing install ---------------------------------------------
if [ -f "$TARGET_SCRIPT" ]; then
    INSTALLED_VERSION="$(_version_from_file "$TARGET_SCRIPT")"
    case "$(_compare_versions "$INSTALLED_VERSION" "$NEW_VERSION")" in
        older)
            echo "Installed version: $INSTALLED_VERSION. Update available: $NEW_VERSION."
            read -r -p "Update now? [Y/n] " ANSWER || true
            case "$ANSWER" in
                [nN]*) echo "Cancelled." ; exit 0 ;;
            esac
            ;;
        same)
            echo "Version $NEW_VERSION is already installed."
            read -r -p "Reinstall it anyway? [y/N] " ANSWER || true
            case "$ANSWER" in
                [yY]*) ;;
                *) echo "Cancelled." ; exit 0 ;;
            esac
            ;;
        newer)
            echo "WARNING: the installed version ($INSTALLED_VERSION) is newer than the one in this repo ($NEW_VERSION)."
            read -r -p "Overwrite the newer version with the older one? [y/N] " ANSWER || true
            case "$ANSWER" in
                [yY]*) ;;
                *) echo "Cancelled." ; exit 0 ;;
            esac
            ;;
    esac
fi

echo "Installing Ollama Manager $NEW_VERSION..."

# 1) Python dependencies - no root (--user)
python3 -m pip install --user --upgrade PyQt6 requests

# 2) Copy the script to a stable location (survives removing/moving the cloned repo)
mkdir -p "$TARGET_DIR"
cp "$SOURCE_SCRIPT" "$TARGET_SCRIPT"
chmod +x "$TARGET_SCRIPT"

# WHY: interface translations (lang/*.json) must live next to the script -
#      _KATALOG_LANG in the code is resolved relative to ollama_manager.py, not the CWD.
if [ -d "$SOURCE_LANG_DIR" ]; then
    cp -r "$SOURCE_LANG_DIR" "$TARGET_DIR/"
fi

# 3) Application menu entry - Categories=Utility is the "Utilities" section in KDE.
#    WHY Comment[pl]: the .desktop format supports per-language variants
#    directly - default (no suffix) in English, Polish as an explicit [pl] variant.
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Ollama Manager
Comment=Manage the Ollama service and models
Comment[pl]=Zarządzanie usługą i modelami Ollama
Exec=python3 "$TARGET_DIR/ollama_manager.py"
Icon=applications-system
Categories=Utility;
Terminal=false
StartupNotify=true
EOF

# WHY: refreshes the menu database right away - without this KDE sometimes
#      only picks up the new entry after the next login.
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

echo "Done. Ollama Manager $NEW_VERSION should now be in the application menu (Utilities)."
echo "If it doesn't show up right away, log out and log back in."
echo "To uninstall: ./install.sh --uninstall"
