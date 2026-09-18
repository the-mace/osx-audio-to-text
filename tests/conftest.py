from pathlib import Path

import pytest

import audio_to_text


@pytest.fixture(autouse=True)
def disable_prepare(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests send the original fake bytes unless marked enable_prepare."""
    if request.node.get_closest_marker("enable_prepare"):
        return
    monkeypatch.setattr(audio_to_text, "prepare_stt_wav", lambda path: None)


@pytest.fixture(autouse=True)
def default_stt_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_STT_MODEL", raising=False)


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fake-mp4-bytes")
    return path


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    monkeypatch.setattr(audio_to_text, "ENV_FILE_PATH", path)
    return path
