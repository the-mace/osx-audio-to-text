import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "finder" / "Convert to Text.workflow"
INFO = WORKFLOW / "Contents" / "Info.plist"
AUTOMATOR_WFLOW = WORKFLOW / "Contents" / "document.wflow"
SHORTCUT_WFLOW = ROOT / "finder" / "Convert to Text.wflow"
INSTALL_PY = ROOT / "scripts" / "install_shortcut.py"


def test_workflow_bundle_exists() -> None:
    assert INFO.is_file()
    assert AUTOMATOR_WFLOW.is_file()
    assert SHORTCUT_WFLOW.is_file()
    assert INSTALL_PY.is_file()


def test_quick_action_only_audio_and_mp4() -> None:
    info = plistlib.loads(INFO.read_bytes())
    service = info["NSServices"][0]
    assert service["NSMenuItem"]["default"] == "Convert to Text"
    assert service["NSMessage"] == "runWorkflowAsService"
    assert service["NSRequiredContext"]["NSApplicationIdentifier"] == "com.apple.finder"
    types = set(service["NSSendFileTypes"])
    assert "public.audio" in types
    assert "public.mpeg-4" in types
    assert "public.image" not in types
    assert "public.plain-text" not in types
    assert "com.adobe.pdf" not in types
    assert "public.item" not in types
    assert "public.data" not in types


def test_shortcut_wflow_is_finder_quick_action() -> None:
    document = plistlib.loads(SHORTCUT_WFLOW.read_bytes())
    assert document["WFWorkflowName"] == "Convert to Text"
    assert document["WFWorkflowTypes"] == ["QuickActions"]
    assert document["WFQuickActionSurfaces"] == ["Finder"]
    assert document["WFWorkflowInputContentItemClasses"] == ["WFAVAssetContentItem"]
    assert document["WFWorkflowHasShortcutInputVariables"] is True
    shell = document["WFWorkflowActions"][1]["WFWorkflowActionParameters"]
    script = shell["Script"]["Value"]["string"]
    assert "meeting-summary" in script
    assert "audio-to-text" not in script
    assert "--notify" in script
    assert "GROK_API_KEY" not in script
    assert "xai-" not in script
    assert INSTALL_PY.read_text(encoding="utf-8").count("shortcuts") >= 1
    assert "sign" in INSTALL_PY.read_text(encoding="utf-8")
    assert "--mode" in INSTALL_PY.read_text(encoding="utf-8")


def test_workflow_is_finder_quick_action() -> None:
    document = plistlib.loads(AUTOMATOR_WFLOW.read_bytes())
    meta = document["workflowMetaData"]
    assert meta["workflowTypeIdentifier"] == "com.apple.Automator.workflowType.userAction"
    assert meta["serviceApplicationBundleID"] == "com.apple.finder"
    command = document["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert "meeting-summary" in command
    assert "bin/audio-to-text" not in command
    assert command.count("meeting-summary") >= 8
    assert "--notify" in command
    assert "GROK_API_KEY" not in command
    assert "xai-" not in command


def test_repo_has_no_secret_files() -> None:
    forbidden = {".env", "id_rsa", "credentials.json"}
    tracked_names = {path.name for path in ROOT.rglob("*") if path.is_file()}
    assert not (tracked_names & forbidden)
    skip_dirs = {".git", ".venv", "venv", "__pycache__", ".egg-info", ".pytest_cache"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix in {".pyc"}:
            continue
        if any(part in skip_dirs or part.endswith(".egg-info") for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "xai-" not in text.lower() or "your_xai_api_key_here" in text
