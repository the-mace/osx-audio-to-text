from pathlib import Path

import pytest

import audio_to_text


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
