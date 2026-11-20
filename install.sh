#!/usr/bin/env bash
# Dataset Scout installer — double-click "Install Dataset Scout" or run: bash install.sh
# Installs PySide6 into a private .venv (Mint's Python is externally
# managed, so no --user pip), writes the app-menu launcher, and verifies
# the install works before reporting success.
set -euo pipefail

# When double-clicked, the terminal closes as soon as the script
# ends: pause on the way out so the result (or the error) stays
# visible. Skipped when there is no terminal to read from (a piped
# or automated run). Set early, so it covers early failures too.
pause_on_exit() {
  if [ -t 0 ]; then
    echo
    read -r -p "Press Enter to close this window. " || true
  fi
}
trap pause_on_exit EXIT

DIR="$(cd "$(dirname "$0")" && pwd)"
# Resolve symlinks, so installing through a link still finds this folder.
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
cd "$DIR"

echo "Installing Dataset Scout from $DIR ..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 was not found. Install Python 3 first." >&2
  exit 1
fi

# Private venv (never touches system Python).
if [ ! -x "$DIR/.venv/bin/python" ]; then
  if ! python3 -m venv "$DIR/.venv"; then
    rm -rf "$DIR/.venv"
    echo "ERROR: python3-venv is missing. Run: sudo apt install python3-venv" >&2
    echo "Then run bash install.sh again." >&2
    exit 1
  fi
fi

echo "Setting up Dataset Scout's Python tools..."
"$DIR/.venv/bin/python" -m pip install --upgrade pip

# T34: retry the big download up to 3 times, showing progress.
# pip does not resume a partial download, so each retry starts
# the download again from the beginning. pyarrow (the Parquet
# reader, v0.1.4 T4) is bundled so Parquet datasets convert on a
# real install; it is about 55 MB of the download and about
# 169 MB on disk once installed.
echo "Downloading the interface toolkit and the Parquet reader (one-time, about 200 MB)..."
tries=0
until "$DIR/.venv/bin/python" -m pip install PySide6-Essentials PySide6-Addons pyarrow; do
  tries=$((tries + 1))
  if [ "$tries" -ge 3 ]; then
    echo "ERROR: the download failed 3 times. Check the network and" >&2
    echo "run bash install.sh again." >&2
    exit 1
  fi
  echo "Download interrupted, trying again ($tries of 3)..."
  sleep 2
done
echo "Toolkit downloaded."

# Fatal check: the GUI toolkit must import, or the install has failed.
# The real error is printed (v0.1.4 T9): discarding stderr and
# guessing "network connection, or disk space" sent the user to
# re-run a 6-minute install that would fail identically on a
# missing system library.
if ! import_error="$("$DIR/.venv/bin/python" -c "import PySide6; from PySide6 import QtWidgets" 2>&1)"; then
  echo "ERROR: PySide6 installed but cannot be imported." >&2
  echo "$import_error" >&2
  exit 1
fi

# Icon PNGs: the release zip ships them, but a fresh clone has only
# the SVG — render them now so the launcher never points at a
# missing file. (Needs QtSvg, which is why PySide6-Addons is above.)
# A failed render only warns: the app still works, the icon just
# falls back to the system default.
if [ ! -f "$DIR/assets/scout.png" ]; then
  if ! "$DIR/.venv/bin/python" "$DIR/render_icons.py" >/dev/null 2>&1; then
    echo "WARNING: could not render the app icon from assets/scout.svg." >&2
  fi
fi

# App-menu launcher (absolute paths — no quoting tricks needed).
# NOTE: Icon= must NOT be quoted. GLib strips quotes from Exec= but
# NOT from Icon=, so a quoted Icon= shows the generic gear icon.
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/dataset-scout.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Dataset Scout
Comment=Find training datasets for STELLA
Exec="$DIR/run.sh"
Icon=$DIR/assets/scout.png
Terminal=false
Categories=Utility;
StartupWMClass=Dataset Scout
StartupNotify=false
EOF

# Desktop copy for double-click launch from the desktop too.
# Cinnamon/Nemo refuse to launch an untrusted .desktop file, so the
# copy is made executable AND marked trusted. When there is no
# ~/Desktop the copy is skipped, and the final message says so
# (T40): never promise an icon that was not placed.
DESKTOP_PLACED=0
if [ -d "$HOME/Desktop" ]; then
  if cp "$HOME/.local/share/applications/dataset-scout.desktop" "$HOME/Desktop/Dataset Scout.desktop" 2>/dev/null; then
    DESKTOP_PLACED=1
    chmod +x "$HOME/Desktop/Dataset Scout.desktop" 2>/dev/null || true
    if command -v gio >/dev/null 2>&1; then
      gio set "$HOME/Desktop/Dataset Scout.desktop" metadata::trusted true 2>/dev/null || true
    fi
  fi
fi

# Command line: `scout` on PATH after install.
mkdir -p "$HOME/.local/bin"
ln -sf "$DIR/scout-cli" "$HOME/.local/bin/scout"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

# Final check: Dataset Scout itself must import.
if ! scout_error="$("$DIR/.venv/bin/python" -c "import scout; print(scout.__version__)" 2>&1)"; then
  echo "ERROR: Dataset Scout did not import after install." >&2
  echo "$scout_error" >&2
  exit 1
fi

if [ "$DESKTOP_PLACED" -eq 1 ]; then
  echo "Dataset Scout is installed. Open it from the app menu (Dataset Scout) or"
  echo "double-click Dataset Scout on the desktop. No terminal needed."
else
  echo "Dataset Scout is installed. Open it from the app menu (Dataset Scout)."
  echo "No terminal needed."
fi
