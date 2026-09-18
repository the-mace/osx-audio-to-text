import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import audio_to_text
import meeting_summary


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _chat_payload(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_default_output_dir(tmp_path: Path) -> None:
    source = tmp_path / "130 Uxbridge St.m4a"
    assert meeting_summary.default_output_dir(source) == tmp_path / "130 Uxbridge St"


def test_strip_markdown_fence() -> None:
    raw = "```markdown\n# Title\n\n- **Date:** Unknown\n```\n"
    assert meeting_summary.strip_markdown_fence(raw) == "# Title\n\n- **Date:** Unknown"
    assert meeting_summary.strip_markdown_fence("# Title") == "# Title"


def test_request_summary_posts_chat_completions() -> None:
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, _chat_payload("# Hello\n"))
    text = meeting_summary.request_summary(
        "Speaker 0: Hello.",
        "secret-key",
        source_name="clip.m4a",
        model="grok-test",
        session=session,
    )
    assert text == "# Hello"
    args, kwargs = session.post.call_args
    assert args[0] == meeting_summary.CHAT_URL
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"
    body = kwargs["json"]
    assert body["model"] == "grok-test"
    assert body["stream"] is False
    assert body["messages"][0]["role"] == "system"
    assert "clip.m4a" in body["messages"][1]["content"]
    assert "Speaker 0: Hello." in body["messages"][1]["content"]


def test_request_summary_rejects_empty() -> None:
    with pytest.raises(meeting_summary.MeetingSummaryError, match="empty"):
        meeting_summary.request_summary("   ", "key", source_name="clip.m4a")


def test_request_summary_401() -> None:
    session = MagicMock()
    session.post.return_value = _FakeResponse(401, {"error": "unauthorized"})
    with pytest.raises(meeting_summary.MeetingSummaryError, match="401"):
        meeting_summary.request_summary(
            "Hello", "bad-key", source_name="clip.m4a", session=session
        )


def test_request_summary_network_error() -> None:
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(meeting_summary.MeetingSummaryError, match="network error"):
        meeting_summary.request_summary(
            "Hello", "key", source_name="clip.m4a", session=session
        )


def test_main_dry_run(audio_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = meeting_summary.main(["--dry-run", str(audio_file)])
    assert rc == 0
    out = capsys.readouterr().out
    dest_dir = audio_file.parent / audio_file.stem
    assert str(dest_dir / "transcript.txt") in out
    assert str(dest_dir / "summary.md") in out
    assert not dest_dir.exists()


def test_main_writes_folder(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")

    def fake_post(url, **kwargs):
        if url == audio_to_text.STT_URL:
            return _FakeResponse(
                200,
                {
                    "text": "We will ship Friday.",
                    "words": [
                        {"text": "We", "speaker": 0, "start": 0.0, "end": 0.2},
                        {"text": "will", "speaker": 0, "start": 0.2, "end": 0.4},
                        {"text": "ship", "speaker": 0, "start": 0.4, "end": 0.6},
                        {"text": "Friday.", "speaker": 0, "start": 0.6, "end": 0.9},
                    ],
                },
            )
        if url == meeting_summary.CHAT_URL:
            return _FakeResponse(200, _chat_payload("# Ship plan\n\n## Decisions\n- Ship Friday."))
        raise AssertionError(url)

    with patch("audio_to_text.requests.post", side_effect=fake_post), patch(
        "meeting_summary.requests.post", side_effect=fake_post
    ):
        rc = meeting_summary.main([str(audio_file)])
    assert rc == 0
    dest_dir = audio_file.parent / audio_file.stem
    summary = dest_dir / "summary.md"
    transcript = dest_dir / "transcript.txt"
    assert summary.read_text(encoding="utf-8") == (
        "# Ship plan\n\n## Decisions\n- Ship Friday.\n"
    )
    assert transcript.read_text(encoding="utf-8") == "Speaker 0: We will ship Friday.\n"
    assert str(summary) in capsys.readouterr().out
    assert not audio_file.with_suffix(".txt").exists()


def test_main_out_and_from_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    source = tmp_path / "notes.txt"
    source.write_text("Speaker 0: Done.\n", encoding="utf-8")
    out = tmp_path / "meeting-out"
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, _chat_payload("# Notes\n"))
    with patch("meeting_summary.requests.post", session.post):
        rc = meeting_summary.main(
            ["--from-transcript", "--out", str(out), str(source)]
        )
    assert rc == 0
    assert (out / "transcript.txt").read_text(encoding="utf-8") == "Speaker 0: Done.\n"
    assert (out / "summary.md").read_text(encoding="utf-8") == "# Notes\n"


def test_main_missing_key_is_nonzero(
    audio_file: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    env_file.write_text("")
    rc = meeting_summary.main([str(audio_file)])
    assert rc == 1
