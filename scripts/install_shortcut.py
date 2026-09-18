#!/usr/bin/env python3
"""Sign the shipped .wflow and install it as a Finder Quick Action.

Finder's right-click Quick Actions on current macOS are Shortcuts
(runShortcutAsService). This signs finder/Convert to Text.wflow with
`shortcuts sign --mode anyone`, imports or replaces the Shortcuts item, then
registers it in pbs so it appears in the Finder context menu.

The .wflow is the source of truth. A fresh clone can recreate the menu item
without a pre-existing Shortcuts library entry.
"""

from __future__ import annotations

import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from shutil import which

NAME = "Convert to Text"
SHORTCUTS_DB = Path.home() / "Library/Shortcuts/Shortcuts.sqlite"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def wflow_path() -> Path:
    return repo_root() / "finder" / "Convert to Text.wflow"


def shortcut_names() -> list[str]:
    result = subprocess.run(
        ["shortcuts", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def workflow_id_for(name: str) -> str | None:
    if not SHORTCUTS_DB.exists():
        return None
    con = sqlite3.connect(str(SHORTCUTS_DB))
    try:
        row = con.execute(
            "SELECT ZWORKFLOWID FROM ZSHORTCUT WHERE ZNAME=? AND IFNULL(ZTOMBSTONED,0)=0",
            (name,),
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def sign_wflow(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            "shortcuts",
            "sign",
            "--mode",
            "anyone",
            "--input",
            str(src),
            "--output",
            str(dest),
        ]
    )


def applescript(source: str) -> str:
    result = subprocess.run(
        ["osascript"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"osascript failed: {err}")
    return (result.stdout or "").strip()


def quit_shortcuts() -> None:
    subprocess.run(
        ["osascript", "-e", 'tell application "Shortcuts" to quit'],
        capture_output=True,
        check=False,
    )
    subprocess.run(["killall", "Shortcuts"], capture_output=True, check=False)
    time.sleep(1)


def import_or_replace(signed: Path) -> None:
    """Open the signed shortcut and Add (fresh) or Replace (already installed)."""
    subprocess.run(["open", str(signed)], check=True)
    deadline = time.time() + 20
    last = ""
    while time.time() < deadline:
        last = applescript(
            r"""
tell application "System Events"
    if not (exists process "Shortcuts") then return "no-process"
    tell process "Shortcuts"
        set frontmost to true
        repeat with w in windows
            try
                if (count of sheets of w) > 0 then
                    set btns to name of every button of sheet 1 of w
                    if btns contains "Replace" then
                        click button "Replace" of sheet 1 of w
                        return "replaced"
                    end if
                end if
            end try
        end repeat
        repeat with cand in windows
            if (name of cand as text) is "" then
                try
                    tell scroll area 1 of group 1 of cand
                        if (count of buttons) >= 2 then
                            perform action "AXPress" of button 2
                            return "added"
                        end if
                    end tell
                end try
            end if
        end repeat
        return "waiting"
    end tell
end tell
"""
        )
        if last in {"replaced", "added"}:
            time.sleep(1.5)
            return
        time.sleep(0.4)
    raise RuntimeError(
        f"Timed out importing {signed.name} (last UI state: {last or 'unknown'}). "
        f"Open {signed} and click Add Shortcut / Replace."
    )


def register_pbs(workflow_id: str) -> None:
    exported = Path(tempfile.mkstemp(prefix="pbs-", suffix=".plist")[1])
    try:
        subprocess.check_call(["defaults", "export", "pbs", str(exported)])
        pbs = plistlib.loads(exported.read_bytes())
        key = f"(null) - {workflow_id} - runShortcutAsService"
        pbs.setdefault("NSServicesStatus", {})[key] = {
            "enabled_services_menu": False,
            "presentation_modes": {
                "ContextMenu": True,
                "FinderPreview": True,
                "ServicesMenu": False,
                "TouchBar": False,
            },
        }
        order_key = f"SERVICE-(null) - {workflow_id} - runShortcutAsService"
        ordering = pbs.setdefault("FinderOrdering", {})
        if order_key not in ordering:
            ordering[order_key] = (max(ordering.values()) + 1) if ordering else 0
        exported.write_bytes(plistlib.dumps(pbs, fmt=plistlib.FMT_XML))
        subprocess.check_call(["defaults", "import", "pbs", str(exported)])
    finally:
        exported.unlink(missing_ok=True)
    subprocess.run(["killall", "pbs"], capture_output=True, check=False)
    pbs_bin = Path("/System/Library/CoreServices/pbs")
    if pbs_bin.is_file():
        subprocess.run([str(pbs_bin), "-flush"], capture_output=True, check=False)


def sync_wflow_into_db(src: Path, name: str) -> str:
    """Write shipped actions/input types into the installed shortcut.

    Import/Replace creates the CloudKit-backed row; this makes the repo
    .wflow the body even if Replace left the previous actions in place.
    """
    quit_shortcuts()
    document = plistlib.loads(src.read_bytes())
    actions_blob = plistlib.dumps(document["WFWorkflowActions"], fmt=plistlib.FMT_BINARY)
    input_blob = plistlib.dumps(
        document["WFWorkflowInputContentItemClasses"], fmt=plistlib.FMT_BINARY
    )
    con = sqlite3.connect(str(SHORTCUTS_DB))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        row = con.execute(
            "SELECT Z_PK, ZWORKFLOWID FROM ZSHORTCUT WHERE ZNAME=? AND IFNULL(ZTOMBSTONED,0)=0",
            (name,),
        ).fetchone()
        if not row:
            raise RuntimeError(f"Shortcut {name!r} missing from Shortcuts.sqlite after import")
        pk, wid = row
        n_actions = len(document["WFWorkflowActions"])
        con.execute(
            "UPDATE ZSHORTCUT SET ZINPUTCLASSESDATA=?, ZACTIONCOUNT=?, "
            "ZWORKFLOWSUBTITLE=? WHERE Z_PK=?",
            (input_blob, n_actions, f"{n_actions} actions", pk),
        )
        con.execute("UPDATE ZSHORTCUTACTIONS SET ZDATA=? WHERE ZSHORTCUT=?", (actions_blob, pk))
        con.commit()
    finally:
        con.close()
    return wid


def wait_for_shortcut(name: str, timeout: float = 15) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if name in shortcut_names():
            wid = workflow_id_for(name)
            if wid:
                return wid
        time.sleep(0.4)
    raise RuntimeError(f"Shortcut {name!r} did not appear in `shortcuts list`")


def main() -> int:
    src = wflow_path()
    if not src.is_file():
        print(f"Missing {src}", file=sys.stderr)
        return 1
    if not which("shortcuts"):
        print("macOS `shortcuts` CLI not found", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="osx-audio-to-text-") as raw_tmp:
        tmp = Path(raw_tmp)
        signed = tmp / f"{NAME}.shortcut"
        print(f"Signing {src.name} for anyone…")
        sign_wflow(src, signed)
        print(f"Importing {signed.name} into Shortcuts…")
        import_or_replace(signed)
        wait_for_shortcut(NAME)
        wid = sync_wflow_into_db(src, NAME)
        print(f"Registering Finder Quick Action ({wid})…")
        register_pbs(wid)

    print(f"Installed Shortcuts Quick Action: {NAME}")
    print(
        "Right-click an audio or MP4 file → Quick Actions → Convert to Text "
        "(writes <stem>/summary.md and transcript.txt)"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
