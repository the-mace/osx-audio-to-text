import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "finder" / "Convert to Text.workflow"
INFO = WORKFLOW / "Contents" / "Info.plist"
WFLOW = WORKFLOW / "Contents" / "document.wflow"


def test_workflow_bundle_exists() -> None:
    assert INFO.is_file()
    assert WFLOW.is_file()


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


def test_workflow_is_finder_quick_action() -> None:
    document = plistlib.loads(WFLOW.read_bytes())
    meta = document["workflowMetaData"]
    assert meta["workflowTypeIdentifier"] == "com.apple.Automator.workflowType.userAction"
    assert meta["serviceApplicationBundleID"] == "com.apple.finder"
    command = document["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert "audio-to-text" in command
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
