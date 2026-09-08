#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/finder/Convert to Text.workflow"
DEST="$HOME/Library/Services/Convert to Text.workflow"

if [ ! -d "$SRC" ]; then
  echo "Missing workflow bundle: $SRC" >&2
  exit 1
fi

mkdir -p "$HOME/Library/Services"
rm -rf "$DEST"
cp -R "$SRC" "$DEST"
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
touch "$DEST"

if [ -x /System/Library/CoreServices/pbs ]; then
  /System/Library/CoreServices/pbs -flush 2>/dev/null || true
fi

echo "Installed Finder Quick Action: $DEST"
echo "Right-click an audio or MP4 file → Quick Actions → Convert to Text"
