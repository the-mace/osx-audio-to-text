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

echo "Installed Automator service bundle: $DEST"

# On current macOS, Finder Quick Actions that actually appear in the
# right-click menu are Shortcuts (runShortcutAsService), like Rename Invoice.
# If Convert to Text already exists in Shortcuts, register it as a Finder QA.
if command -v shortcuts >/dev/null 2>&1 && shortcuts list 2>/dev/null | grep -qx "Convert to Text"; then
  python3 - <<'PY'
import plistlib, sqlite3, subprocess
from pathlib import Path

con = sqlite3.connect(str(Path.home() / "Library/Shortcuts/Shortcuts.sqlite"))
row = con.execute("SELECT ZWORKFLOWID FROM ZSHORTCUT WHERE ZNAME=?", ("Convert to Text",)).fetchone()
con.close()
if not row:
    raise SystemExit(0)
wid = row[0]
exported = Path("/tmp/pbs-convert-to-text.plist")
subprocess.check_call(["defaults", "export", "pbs", str(exported)])
pbs = plistlib.loads(exported.read_bytes())
key = f"(null) - {wid} - runShortcutAsService"
pbs.setdefault("NSServicesStatus", {})[key] = {
    "enabled_services_menu": False,
    "presentation_modes": {
        "ContextMenu": True,
        "FinderPreview": True,
        "ServicesMenu": False,
        "TouchBar": False,
    },
}
order_key = f"SERVICE-(null) - {wid} - runShortcutAsService"
ordering = pbs.setdefault("FinderOrdering", {})
if order_key not in ordering:
    ordering[order_key] = (max(ordering.values()) + 1) if ordering else 0
exported.write_bytes(plistlib.dumps(pbs, fmt=plistlib.FMT_XML))
subprocess.check_call(["defaults", "import", "pbs", str(exported)])
print(f"Registered Shortcuts Quick Action Convert to Text ({wid})")
PY
  /System/Library/CoreServices/pbs -flush 2>/dev/null || true
fi

echo "Right-click an audio or MP4 file → Quick Actions → Convert to Text"
