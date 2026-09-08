import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import audio_to_text


def test_output_path_uses_same_basename_txt(tmp_path: Path) -> None:
    source = tmp_path / "Interview 2026.mp4"
    assert audio_to_text.output_path_for(source) == tmp_path / "Interview 2026.txt"


def test_supported_extensions() -> None:
    assert audio_to_text.is_supported(Path("talk.MP4"))
    assert audio_to_text.is_supported(Path("talk.m4a"))
    assert audio_to_text.is_supported(Path("talk.mp3"))
    assert not audio_to_text.is_supported(Path("talk.pdf"))
    assert not audio_to_text.is_supported(Path("talk.txt"))
    assert not audio_to_text.is_supported(Path("talk.jpg"))


def test_validate_rejects_missing_and_empty(tmp_path: Path) -> None:
    missing = tmp_path / "nope.mp4"
    with pytest.raises(audio_to_text.AudioToTextError, match="File not found"):
        audio_to_text.validate_input(missing)

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(audio_to_text.AudioToTextError, match="empty"):
        audio_to_text.validate_input(empty)


def test_validate_rejects_unsupported(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(audio_to_text.AudioToTextError, match="Unsupported"):
        audio_to_text.validate_input(pdf)


def test_validate_rejects_oversize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "huge.mp4"
    path.write_bytes(b"abc")
    monkeypatch.setattr(audio_to_text, "MAX_FILE_BYTES", 2)
    with pytest.raises(audio_to_text.AudioToTextError, match="500 MB"):
        audio_to_text.validate_input(path)


def test_load_env_maps_grok_key(env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    env_file.write_text("GROK_API_KEY=test-secret\n")
    audio_to_text.load_env_file()
    assert audio_to_text.get_api_key() == "test-secret"
    assert audio_to_text.os.getenv("XAI_API_KEY") == "test-secret"


def test_load_env_does_not_override_existing(
    env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "already-set")
    env_file.write_text("XAI_API_KEY=from-file\n")
    audio_to_text.load_env_file()
    assert audio_to_text.os.getenv("XAI_API_KEY") == "already-set"


def test_missing_api_key(env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    env_file.write_text("# no keys\n")
    with pytest.raises(audio_to_text.AudioToTextError, match="No API key"):
        audio_to_text.get_api_key()


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_transcribe_posts_multipart(audio_file: Path) -> None:
    session = MagicMock()
    session.post.return_value = _FakeResponse(
        200, {"text": "Hello world.", "language": "en", "duration": 1.2}
    )
    text = audio_to_text.transcribe_file(audio_file, "secret-key", session=session)
    assert text == "Hello world."
    args, kwargs = session.post.call_args
    assert args[0] == audio_to_text.STT_URL
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
    assert kwargs["data"] == [("format", "true"), ("language", "en")]
    filename, handle, mime = kwargs["files"]["file"]
    assert filename == "clip.mp4"
    assert mime == "video/mp4"
    handle.close()


def test_transcribe_401(audio_file: Path) -> None:
    session = MagicMock()
    session.post.return_value = _FakeResponse(401, {"error": "unauthorized"})
    with pytest.raises(audio_to_text.AudioToTextError, match="401"):
        audio_to_text.transcribe_file(audio_file, "bad-key", session=session)


def test_write_transcript_adds_newline(audio_file: Path) -> None:
    dest = audio_to_text.write_transcript(audio_file, "Hello")
    assert dest == audio_file.with_suffix(".txt")
    assert dest.read_text(encoding="utf-8") == "Hello\n"


def test_write_transcript_overwrites(audio_file: Path) -> None:
    dest = audio_file.with_suffix(".txt")
    dest.write_text("old\n", encoding="utf-8")
    audio_to_text.write_transcript(audio_file, "new")
    assert dest.read_text(encoding="utf-8") == "new\n"


def test_main_dry_run(audio_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = audio_to_text.main(["--dry-run", str(audio_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(audio_file.with_suffix(".txt")) in out


def test_main_writes_txt(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    fake = _FakeResponse(200, {"text": "Hello, this is a speech to text test."})
    with patch("audio_to_text.requests.post", return_value=fake):
        rc = audio_to_text.main([str(audio_file)])
    assert rc == 0
    dest = audio_file.with_suffix(".txt")
    assert dest.read_text(encoding="utf-8") == "Hello, this is a speech to text test.\n"
    assert str(dest) in capsys.readouterr().out


def test_main_missing_key_is_nonzero(
    audio_file: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    env_file.write_text("")
    rc = audio_to_text.main([str(audio_file)])
    assert rc == 1


def test_retry_on_429_then_success(audio_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio_to_text.time, "sleep", lambda _: None)
    session = MagicMock()
    session.post.side_effect = [
        _FakeResponse(429, {"error": "rate limited"}),
        _FakeResponse(200, {"text": "ok"}),
    ]
    assert audio_to_text.transcribe_file(audio_file, "key", session=session) == "ok"


def test_network_error(audio_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio_to_text.time, "sleep", lambda _: None)
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(audio_to_text.AudioToTextError, match="network error"):
        audio_to_text.transcribe_file(audio_file, "key", session=session)
