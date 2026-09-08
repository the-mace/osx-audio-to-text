#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-/usr/local/bin/python3}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi

# Primary path: sign the shipped .wflow and import it as a Shortcuts
# Finder Quick Action. That is what actually appears in Finder's
# right-click menu on current macOS.
"$PYTHON" "$ROOT/scripts/install_shortcut.py"

# Optional Automator bundle. Harmless if unused; kept as a Services fallback.
SRC="$ROOT/finder/Convert to Text.workflow"
DEST="$HOME/Library/Services/Convert to Text.workflow"
if [ -d "$SRC" ]; then
  mkdir -p "$HOME/Library/Services"
  rm -rf "$DEST"
  cp -R "$SRC" "$DEST"
  xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
  touch "$DEST"
fi
