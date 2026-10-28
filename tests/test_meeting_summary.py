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


def test_dry_run_accepts_qta(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "standup.qta"
    source.write_bytes(b"fake-qta")
    rc = meeting_summary.main(["--dry-run", str(source)])
    assert rc == 0
    out = capsys.readouterr().out
    dest_dir = source.parent / source.stem
    assert str(dest_dir / "summary.md") in out
    assert str(dest_dir / "transcript.txt") in out


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


def test_main_notify_prints_status_not_path(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")

    def fake_post(url, **kwargs):
        if url == audio_to_text.STT_URL:
            return _FakeResponse(200, {"text": "We will ship Friday."})
        if url == meeting_summary.CHAT_URL:
            return _FakeResponse(200, _chat_payload("# Ship plan\n"))
        raise AssertionError(url)

    expected = "File clip.mp4 has been successfully transcribed and summarized"
    with patch("audio_to_text.requests.post", side_effect=fake_post), patch(
        "meeting_summary.requests.post", side_effect=fake_post
    ), patch("audio_to_text.notify") as notify_mock:
        rc = meeting_summary.main(["--notify", str(audio_file)])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == expected
    assert "summary.md" not in out
    assert str(audio_file.parent) not in out
    notify_mock.assert_called_once_with("Convert to Text", expected, error=False)


def test_main_notify_error_prints_helpful_status(
    audio_file: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GROK_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    env_file.write_text("")
    with patch("audio_to_text.notify") as notify_mock:
        rc = meeting_summary.main(["--notify", str(audio_file)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Could not transcribe and summarize clip.mp4:" in captured.out
    assert "API key" in captured.out
    assert str(audio_file.parent) not in captured.out
    notify_mock.assert_called_once()
    assert notify_mock.call_args.kwargs.get("error") is True


def test_main_notify_from_transcript_says_summarized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    source = tmp_path / "notes.txt"
    source.write_text("Speaker 0: Done.\n", encoding="utf-8")
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, _chat_payload("# Notes\n"))
    expected = "File notes.txt has been successfully summarized"
    with patch("meeting_summary.requests.post", session.post), patch(
        "audio_to_text.notify"
    ) as notify_mock:
        rc = meeting_summary.main(
            ["--notify", "--from-transcript", str(source)]
        )
    assert rc == 0
    assert capsys.readouterr().out.strip() == expected
    notify_mock.assert_called_once_with("Convert to Text", expected, error=False)


def test_main_join_writes_one_summary_for_all_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    first = tmp_path / "Call.m4a"
    second = tmp_path / "Call 2.m4a"
    first.write_bytes(b"part-one")
    second.write_bytes(b"part-two")
    workdir = tmp_path / "joined"
    workdir.mkdir()
    joined_calls: list[list[Path]] = []

    def fake_join(paths, *, mono=True):
        joined_calls.append(list(paths))
        dest = workdir / "Call.wav"
        dest.write_bytes(b"RIFFjoined")
        return dest

    stt_uploads: list[str] = []

    def fake_post(url, **kwargs):
        if url == audio_to_text.STT_URL:
            stt_uploads.append(kwargs["files"]["file"][0])
            return _FakeResponse(200, {"text": "First half. Second half."})
        if url == meeting_summary.CHAT_URL:
            return _FakeResponse(200, _chat_payload("# Whole call\n"))
        raise AssertionError(url)

    monkeypatch.setattr(audio_to_text, "join_recordings", fake_join)
    expected = (
        "File Call.m4a (+1 joined) has been successfully transcribed and summarized"
    )
    with patch("audio_to_text.requests.post", side_effect=fake_post), patch(
        "meeting_summary.requests.post", side_effect=fake_post
    ), patch("audio_to_text.notify"):
        rc = meeting_summary.main(["--notify", "--join", str(second), str(first)])
    assert rc == 0
    assert joined_calls == [[first, second]]
    assert stt_uploads == ["Call.wav"]
    out = tmp_path / "Call"
    assert (out / "transcript.txt").read_text(encoding="utf-8") == (
        "First half. Second half.\n"
    )
    assert (out / "summary.md").read_text(encoding="utf-8") == "# Whole call\n"
    assert not (tmp_path / "Call 2").exists()
    assert not workdir.exists()
    assert capsys.readouterr().out.strip() == expected


def test_join_rejects_from_transcript(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        meeting_summary.parse_args(["--join", "--from-transcript", "a.txt", "b.txt"])


def test_add_missing_speech_note_goes_under_title() -> None:
    markdown = "# Call\n\n- **Date:** Unknown"
    noted = meeting_summary.add_missing_speech_note(markdown, [(0.0, 18.4)])
    assert noted.splitlines()[:3] == [
        "# Call",
        "",
        "> **Note: possible missing speech at 0:00–0:19. The audio sounds like "
        "talking there but Grok STT returned no words. Listen to those parts.**",
    ]
    assert meeting_summary.add_missing_speech_note(markdown, []) == markdown


def test_main_records_missing_speech(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    levels = [(round(i * 0.1, 1), -25.0 if i < 20 or 30 <= i < 80 else -60.0) for i in range(100)]
    monkeypatch.setattr(audio_to_text, "speech_levels", lambda path: levels)
    chat_bodies: list[dict] = []

    def fake_post(url, **kwargs):
        if url == audio_to_text.STT_URL:
            return _FakeResponse(
                200,
                {
                    "text": "Good, how are you?",
                    "words": [
                        {"text": "Good,", "speaker": 1, "start": 0.0, "end": 0.5},
                        {"text": "how", "speaker": 1, "start": 0.5, "end": 1.0},
                        {"text": "are", "speaker": 1, "start": 1.0, "end": 1.5},
                        {"text": "you?", "speaker": 1, "start": 1.5, "end": 2.0},
                    ],
                },
            )
        if url == meeting_summary.CHAT_URL:
            chat_bodies.append(kwargs["json"])
            return _FakeResponse(200, _chat_payload("# Call\n\n- **Date:** Unknown\n"))
        raise AssertionError(url)

    with patch("audio_to_text.requests.post", side_effect=fake_post), patch(
        "meeting_summary.requests.post", side_effect=fake_post
    ):
        rc = meeting_summary.main([str(audio_file)])
    assert rc == 0
    out = audio_file.parent / audio_file.stem
    transcript = (out / "transcript.txt").read_text(encoding="utf-8")
    assert transcript.startswith("Note: possible missing speech at 0:03–0:08.")
    assert "Note:" not in chat_bodies[0]["messages"][1]["content"]
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert summary.splitlines()[2].startswith("> **Note: possible missing speech at 0:03–0:08.")
