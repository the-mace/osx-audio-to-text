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
    assert audio_to_text.is_supported(Path("talk.qta"))
    assert audio_to_text.is_supported(Path("talk.QTA"))
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


def test_format_two_speakers_alternating() -> None:
    payload = {
        "text": "Hello there. Hi. How are you?",
        "words": [
            {"text": "Hello", "speaker": 0, "start": 0.0, "end": 0.3},
            {"text": "there.", "speaker": 0, "start": 0.3, "end": 0.6},
            {"text": "Hi.", "speaker": 1, "start": 0.7, "end": 0.9},
            {"text": "How", "speaker": 0, "start": 1.0, "end": 1.2},
            {"text": "are", "speaker": 0, "start": 1.2, "end": 1.4},
            {"text": "you?", "speaker": 0, "start": 1.4, "end": 1.6},
        ],
    }
    assert audio_to_text.format_transcript(payload) == (
        "Speaker 0: Hello there. Hi. How are you?"
    )
    assert audio_to_text.format_transcript(payload, merge_turns=False) == (
        "Speaker 0: Hello there.\n"
        "\n"
        "Speaker 1: Hi.\n"
        "\n"
        "Speaker 0: How are you?"
    )


def test_format_one_speaker() -> None:
    payload = {
        "text": "Hello there.",
        "words": [
            {"text": "Hello", "speaker": 0, "start": 0.0, "end": 0.3},
            {"text": "there.", "speaker": 0, "start": 0.3, "end": 0.6},
        ],
    }
    assert audio_to_text.format_transcript(payload) == "Speaker 0: Hello there."


def test_format_empty_words_falls_back_to_text() -> None:
    payload = {"text": "Hello world.", "words": []}
    assert audio_to_text.format_transcript(payload) == "Hello world."


def test_format_words_without_speaker_falls_back_to_text() -> None:
    payload = {
        "text": "Hello world.",
        "words": [
            {"text": "Hello", "start": 0.0, "end": 0.3},
            {"text": "world.", "start": 0.3, "end": 0.6},
        ],
    }
    assert audio_to_text.format_transcript(payload) == "Hello world."


def test_format_missing_words_falls_back_to_text() -> None:
    payload = {"text": "Hello world."}
    assert audio_to_text.format_transcript(payload) == "Hello world."


def test_format_no_diarization_uses_text_even_with_speakers() -> None:
    payload = {
        "text": "Hello there. Hi.",
        "words": [
            {"text": "Hello", "speaker": 0},
            {"text": "there.", "speaker": 0},
            {"text": "Hi.", "speaker": 1},
        ],
    }
    assert audio_to_text.format_transcript(payload, use_diarization=False) == (
        "Hello there. Hi."
    )


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
    assert kwargs["data"] == [
        ("model", audio_to_text.DEFAULT_STT_MODEL),
        ("format", "true"),
        ("language", "en"),
        ("filler_words", "true"),
        ("diarize", "true"),
    ]
    filename, handle, mime = kwargs["files"]["file"]
    assert filename == "clip.mp4"
    assert mime == "video/mp4"
    handle.close()


def test_stt_model_defaults_to_transcribe_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_STT_MODEL", raising=False)
    assert audio_to_text.stt_model() == "grok-voice-transcribe-2.0"


def test_stt_model_env_override(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROK_STT_MODEL", "grok-voice-transcribe-1.0")
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, {"text": "ok"})
    audio_to_text.transcribe_file(audio_file, "key", session=session)
    assert session.post.call_args.kwargs["data"][0] == (
        "model",
        "grok-voice-transcribe-1.0",
    )


def test_request_transcription_explicit_model(audio_file: Path) -> None:
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, {"text": "ok"})
    audio_to_text.request_transcription(
        audio_file, "key", model="grok-voice-transcribe-1.0", session=session
    )
    assert session.post.call_args.kwargs["data"][0] == (
        "model",
        "grok-voice-transcribe-1.0",
    )


def test_transcribe_no_diarize_omits_flag_and_uses_text(audio_file: Path) -> None:
    session = MagicMock()
    session.post.return_value = _FakeResponse(
        200,
        {
            "text": "Hello there. Hi.",
            "words": [
                {"text": "Hello", "speaker": 0},
                {"text": "Hi.", "speaker": 1},
            ],
        },
    )
    text = audio_to_text.transcribe_file(
        audio_file, "secret-key", session=session, diarize=False
    )
    assert text == "Hello there. Hi."
    assert session.post.call_args.kwargs["data"] == [
        ("model", audio_to_text.DEFAULT_STT_MODEL),
        ("format", "true"),
        ("language", "en"),
        ("filler_words", "true"),
    ]


def test_transcribe_labels_speakers_from_words(audio_file: Path) -> None:
    session = MagicMock()
    session.post.return_value = _FakeResponse(
        200,
        {
            "text": "Hello there. Hi.",
            "words": [
                {"text": "Hello", "speaker": 0, "start": 0.0, "end": 0.3},
                {"text": "there.", "speaker": 0, "start": 0.3, "end": 0.6},
                {"text": "Hi.", "speaker": 1, "start": 0.7, "end": 0.9},
            ],
        },
    )
    text = audio_to_text.transcribe_file(audio_file, "key", session=session)
    assert text == "Speaker 0: Hello there. Hi."


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


def test_main_writes_speaker_labels_and_trailing_newline(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    fake = _FakeResponse(
        200,
        {
            "text": "Hello there. Hi.",
            "words": [
                {"text": "Hello", "speaker": 0},
                {"text": "there.", "speaker": 0},
                {"text": "Hi.", "speaker": 1},
            ],
        },
    )
    with patch("audio_to_text.requests.post", return_value=fake):
        rc = audio_to_text.main([str(audio_file)])
    assert rc == 0
    dest = audio_file.with_suffix(".txt")
    assert dest.read_text(encoding="utf-8") == (
        "Speaker 0: Hello there.\n\nSpeaker 1: Hi.\n"
    )


def test_main_json_out_and_env_sidecar(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    payload = {"text": "Hello world.", "language": "en"}
    fake = _FakeResponse(200, payload)
    json_path = tmp_path / "raw.stt.json"
    with patch("audio_to_text.requests.post", return_value=fake):
        rc = audio_to_text.main(["--json-out", str(json_path), str(audio_file)])
    assert rc == 0
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["text"] == "Hello world."

    monkeypatch.setenv("AUDIO_TO_TEXT_SAVE_JSON", "1")
    with patch("audio_to_text.requests.post", return_value=fake):
        rc = audio_to_text.main([str(audio_file)])
    assert rc == 0
    sidecar = audio_file.with_suffix(".stt.json")
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["text"] == "Hello world."


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


FLICKER_IM_GONNA = {
    "text": "I'm gonna do default rates.",
    "words": [
        {"text": "I'm", "speaker": 1, "start": 0.00, "end": 0.20},
        {"text": "gonna", "speaker": 2, "start": 0.20, "end": 0.45},
        {"text": "do", "speaker": 1, "start": 0.45, "end": 0.60},
        {"text": "default", "speaker": 2, "start": 0.60, "end": 0.95},
        {"text": "rates.", "speaker": 1, "start": 0.95, "end": 1.30},
    ],
}


def test_merge_flicker_im_gonna_do_default_rates() -> None:
    before = audio_to_text.format_transcript(FLICKER_IM_GONNA, merge_turns=False)
    after = audio_to_text.format_transcript(FLICKER_IM_GONNA)
    assert before == (
        "Speaker 1: I'm\n"
        "\n"
        "Speaker 2: gonna\n"
        "\n"
        "Speaker 1: do\n"
        "\n"
        "Speaker 2: default\n"
        "\n"
        "Speaker 1: rates."
    )
    assert after == "Speaker 1: I'm gonna do default rates."


def test_merge_you_have_time_now_split_across_speakers() -> None:
    payload = {
        "text": "You have time now?",
        "words": [
            {"text": "You", "speaker": 0, "start": 0.0, "end": 0.2},
            {"text": "have", "speaker": 0, "start": 0.2, "end": 0.4},
            {"text": "time", "speaker": 1, "start": 0.6, "end": 0.8},
            {"text": "now?", "speaker": 1, "start": 0.8, "end": 1.1},
        ],
    }
    assert audio_to_text.format_transcript(payload, merge_turns=False) == (
        "Speaker 0: You have\n\nSpeaker 1: time now?"
    )
    assert audio_to_text.format_transcript(payload) == "Speaker 0: You have time now?"


def test_keeps_lone_okay_after_pause() -> None:
    payload = {
        "text": "Hello there. Okay.",
        "words": [
            {"text": "Hello", "speaker": 0, "start": 0.0, "end": 0.3},
            {"text": "there.", "speaker": 0, "start": 0.3, "end": 0.6},
            {"text": "Okay.", "speaker": 1, "start": 2.1, "end": 2.4},
        ],
    }
    assert audio_to_text.format_transcript(payload) == (
        "Speaker 0: Hello there.\n\nSpeaker 1: Okay."
    )


def test_keeps_isolated_okay_between_long_turns() -> None:
    payload = {
        "text": "This is a longer stretch of speech. Okay. And then we keep going from here.",
        "words": [
            {"text": "This", "speaker": 0, "start": 0.0, "end": 0.3},
            {"text": "is", "speaker": 0, "start": 0.3, "end": 0.45},
            {"text": "a", "speaker": 0, "start": 0.45, "end": 0.55},
            {"text": "longer", "speaker": 0, "start": 0.55, "end": 0.9},
            {"text": "stretch", "speaker": 0, "start": 0.9, "end": 1.3},
            {"text": "of", "speaker": 0, "start": 1.3, "end": 1.45},
            {"text": "speech.", "speaker": 0, "start": 1.45, "end": 2.0},
            {"text": "Okay.", "speaker": 1, "start": 3.5, "end": 3.8},
            {"text": "And", "speaker": 0, "start": 5.3, "end": 5.5},
            {"text": "then", "speaker": 0, "start": 5.5, "end": 5.7},
            {"text": "we", "speaker": 0, "start": 5.7, "end": 5.85},
            {"text": "keep", "speaker": 0, "start": 5.85, "end": 6.1},
            {"text": "going", "speaker": 0, "start": 6.1, "end": 6.4},
            {"text": "from", "speaker": 0, "start": 6.4, "end": 6.6},
            {"text": "here.", "speaker": 0, "start": 6.6, "end": 7.0},
        ],
    }
    assert audio_to_text.format_transcript(payload) == (
        "Speaker 0: This is a longer stretch of speech.\n"
        "\n"
        "Speaker 1: Okay.\n"
        "\n"
        "Speaker 0: And then we keep going from here."
    )


def test_two_long_monologues_with_pause_unchanged() -> None:
    payload = {
        "text": (
            "This is a longer first monologue here. "
            "And this is the second speaker talking."
        ),
        "words": [
            {"text": "This", "speaker": 0, "start": 0.0, "end": 0.3},
            {"text": "is", "speaker": 0, "start": 0.3, "end": 0.45},
            {"text": "a", "speaker": 0, "start": 0.45, "end": 0.55},
            {"text": "longer", "speaker": 0, "start": 0.55, "end": 0.95},
            {"text": "first", "speaker": 0, "start": 0.95, "end": 1.25},
            {"text": "monologue", "speaker": 0, "start": 1.25, "end": 1.8},
            {"text": "here.", "speaker": 0, "start": 1.8, "end": 2.2},
            {"text": "And", "speaker": 1, "start": 3.2, "end": 3.4},
            {"text": "this", "speaker": 1, "start": 3.4, "end": 3.6},
            {"text": "is", "speaker": 1, "start": 3.6, "end": 3.75},
            {"text": "the", "speaker": 1, "start": 3.75, "end": 3.9},
            {"text": "second", "speaker": 1, "start": 3.9, "end": 4.3},
            {"text": "speaker", "speaker": 1, "start": 4.3, "end": 4.7},
            {"text": "talking.", "speaker": 1, "start": 4.7, "end": 5.2},
        ],
    }
    expected = (
        "Speaker 0: This is a longer first monologue here.\n"
        "\n"
        "Speaker 1: And this is the second speaker talking."
    )
    assert audio_to_text.format_transcript(payload) == expected
    assert audio_to_text.format_transcript(payload, merge_turns=False) == expected


def test_prefer_complete_uses_text_when_words_are_short(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {
        "text": "A" * 100,
        "words": [
            {"text": "Hi", "speaker": 0, "start": 0.0, "end": 0.2},
        ],
    }
    assert audio_to_text.format_transcript(payload) == "A" * 100
    err = capsys.readouterr().err
    assert audio_to_text.WORDS_INCOMPLETE_WARNING in err


def test_no_prefer_complete_appends_full_text() -> None:
    payload = {
        "text": "A" * 100,
        "words": [
            {"text": "Hi", "speaker": 0, "start": 0.0, "end": 0.2},
        ],
    }
    assert audio_to_text.format_transcript(payload, prefer_complete=False) == (
        "Speaker 0: Hi\n"
        "\n"
        "---\n"
        "Full STT text (no speakers)\n"
        "\n" + ("A" * 100)
    )


def test_complete_words_keep_speaker_labels() -> None:
    payload = {
        "text": "Hello there.",
        "words": [
            {"text": "Hello", "speaker": 0, "start": 0.0, "end": 0.3},
            {"text": "there.", "speaker": 0, "start": 0.3, "end": 0.6},
        ],
    }
    assert audio_to_text.format_transcript(payload) == "Speaker 0: Hello there."


def test_no_diarize_still_writes_only_text() -> None:
    payload = {
        "text": "A" * 100,
        "words": [
            {"text": "Hi", "speaker": 0, "start": 0.0, "end": 0.2},
        ],
    }
    assert audio_to_text.format_transcript(payload, use_diarization=False) == "A" * 100


def test_transcribe_no_format_omits_format_flag(audio_file: Path) -> None:
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, {"text": "twenty three"})
    audio_to_text.transcribe_file(
        audio_file, "key", session=session, format_text=False
    )
    data = session.post.call_args.kwargs["data"]
    assert data[0] == ("model", audio_to_text.DEFAULT_STT_MODEL)
    assert ("format", "true") not in data
    assert ("language", "en") in data
    assert ("filler_words", "true") in data
    assert ("diarize", "true") in data


def test_transcribe_skips_multichannel_for_stereo_by_default(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audio_to_text, "audio_channel_count", lambda _: 2)
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, {"text": "ok"})
    audio_to_text.transcribe_file(audio_file, "key", session=session)
    assert ("multichannel", "true") not in session.post.call_args.kwargs["data"]


def test_transcribe_multichannel_opt_in(audio_file: Path) -> None:
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, {"text": "ok"})
    audio_to_text.transcribe_file(
        audio_file, "key", session=session, multichannel=True
    )
    assert ("multichannel", "true") in session.post.call_args.kwargs["data"]
    filename, _handle, mime = session.post.call_args.kwargs["files"]["file"]
    assert filename == "clip.mp4"
    assert mime == "video/mp4"


def test_format_collapses_identical_multichannel_words() -> None:
    payload = {
        "text": "This This is is a a test.",
        "channels": [
            {
                "index": 0,
                "text": "This is a test.",
                "words": [
                    {"text": "This", "speaker": 0, "start": 0.0, "end": 0.2},
                    {"text": "is", "speaker": 0, "start": 0.2, "end": 0.3},
                    {"text": "a", "speaker": 0, "start": 0.3, "end": 0.4},
                    {"text": "test.", "speaker": 0, "start": 0.4, "end": 0.6},
                ],
            },
            {
                "index": 1,
                "text": "This is a test.",
                "words": [
                    {"text": "This", "speaker": 0, "start": 0.0, "end": 0.2},
                    {"text": "is", "speaker": 0, "start": 0.2, "end": 0.3},
                    {"text": "a", "speaker": 0, "start": 0.3, "end": 0.4},
                    {"text": "test.", "speaker": 0, "start": 0.4, "end": 0.6},
                ],
            },
        ],
        "words": [
            {"text": "This", "speaker": 0, "start": 0.0, "end": 0.2},
            {"text": "This", "speaker": 0, "start": 0.0, "end": 0.2},
            {"text": "is", "speaker": 0, "start": 0.2, "end": 0.3},
            {"text": "is", "speaker": 0, "start": 0.2, "end": 0.3},
            {"text": "a", "speaker": 0, "start": 0.3, "end": 0.4},
            {"text": "a", "speaker": 0, "start": 0.3, "end": 0.4},
            {"text": "test.", "speaker": 0, "start": 0.4, "end": 0.6},
            {"text": "test.", "speaker": 0, "start": 0.4, "end": 0.6},
        ],
    }
    assert audio_to_text.format_transcript(payload) == "Speaker 0: This is a test."


def test_format_distinct_channels_as_paragraphs() -> None:
    payload = {
        "text": "Hello. Hi.",
        "channels": [
            {"index": 0, "text": "Hello."},
            {"index": 1, "text": "Hi."},
        ],
    }
    assert audio_to_text.format_transcript(payload) == (
        "Channel 0: Hello.\n\nChannel 1: Hi."
    )


@pytest.mark.enable_prepare
def test_prepare_sends_16k_mono_wav(
    audio_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF")

    def fake_prepare(path: Path) -> Path:
        assert path == audio_file
        return wav

    monkeypatch.setattr(audio_to_text, "prepare_stt_wav", fake_prepare)
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, {"text": "ok"})
    audio_to_text.transcribe_file(audio_file, "key", session=session)
    filename, handle, mime = session.post.call_args.kwargs["files"]["file"]
    assert filename == "clip.wav"
    assert mime == "audio/wav"
    handle.close()
    assert not wav.exists()


@pytest.mark.enable_prepare
def test_prepare_stt_wav_runs_ffmpeg(
    audio_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest_holder: dict[str, str] = {}

    def fake_run(argv, **kwargs):
        dest_holder["dest"] = argv[-1]
        Path(argv[-1]).write_bytes(b"RIFFWAV")
        completed = MagicMock()
        completed.returncode = 0
        completed.stderr = ""
        return completed

    monkeypatch.setattr(audio_to_text.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio_to_text.subprocess, "run", fake_run)
    prepared = audio_to_text.prepare_stt_wav(audio_file)
    assert prepared is not None
    assert prepared.read_bytes() == b"RIFFWAV"
    argv_dest = dest_holder["dest"]
    assert argv_dest.endswith(".wav")
    prepared.unlink()


def test_unwrap_qta_runs_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    qta = tmp_path / "talk.qta"
    qta.write_bytes(b"fake-qta")
    dest_holder: dict[str, list[str]] = {}

    def fake_run(argv, **kwargs):
        dest_holder["argv"] = list(argv)
        Path(argv[-1]).write_bytes(b"fake-m4a")
        completed = MagicMock()
        completed.returncode = 0
        completed.stderr = ""
        return completed

    monkeypatch.setattr(audio_to_text.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio_to_text.subprocess, "run", fake_run)
    unwrapped = audio_to_text.unwrap_qta(qta)
    assert unwrapped is not None
    assert unwrapped.read_bytes() == b"fake-m4a"
    argv = dest_holder["argv"]
    assert argv[argv.index("-i") + 1] == str(qta)
    assert argv[argv.index("-map") + 1] == "0:a:0"
    assert argv[argv.index("-c:a") + 1] == "copy"
    assert argv[-1].endswith(".m4a")
    unwrapped.unlink()


def test_unwrap_qta_requires_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qta = tmp_path / "talk.qta"
    qta.write_bytes(b"fake-qta")
    monkeypatch.setattr(audio_to_text.shutil, "which", lambda _: None)
    with pytest.raises(audio_to_text.AudioToTextError, match="ffmpeg is required"):
        audio_to_text.unwrap_qta(qta)


def test_unwrap_qta_failure_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qta = tmp_path / "talk.qta"
    qta.write_bytes(b"fake-qta")
    dest_holder: dict[str, str] = {}

    def fake_run(argv, **kwargs):
        dest_holder["dest"] = argv[-1]
        completed = MagicMock()
        completed.returncode = 1
        completed.stderr = "no audio stream"
        return completed

    monkeypatch.setattr(audio_to_text.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(audio_to_text.subprocess, "run", fake_run)
    with pytest.raises(audio_to_text.AudioToTextError, match="could not remux"):
        audio_to_text.unwrap_qta(qta)
    assert not Path(dest_holder["dest"]).exists()


@pytest.mark.enable_prepare
def test_qta_remuxes_to_m4a_then_prepares(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qta = tmp_path / "talk.qta"
    qta.write_bytes(b"fake-qta")
    m4a = tmp_path / "talk.m4a"
    m4a.write_bytes(b"fake-m4a")
    wav = tmp_path / "talk.wav"
    wav.write_bytes(b"RIFF")

    def fake_unwrap(path: Path) -> Path:
        assert path == qta
        return m4a

    def fake_prepare(path: Path) -> Path:
        assert path == m4a
        return wav

    monkeypatch.setattr(audio_to_text, "unwrap_qta", fake_unwrap)
    monkeypatch.setattr(audio_to_text, "prepare_stt_wav", fake_prepare)
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, {"text": "ok"})
    audio_to_text.transcribe_file(qta, "key", session=session)
    filename, handle, mime = session.post.call_args.kwargs["files"]["file"]
    assert filename == "talk.wav"
    assert mime == "audio/wav"
    handle.close()
    assert not m4a.exists()
    assert not wav.exists()


def test_qta_no_prepare_still_sends_m4a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qta = tmp_path / "talk.qta"
    qta.write_bytes(b"fake-qta")
    m4a = tmp_path / "talk.m4a"
    m4a.write_bytes(b"fake-m4a")

    def fake_unwrap(path: Path) -> Path:
        assert path == qta
        return m4a

    monkeypatch.setattr(audio_to_text, "unwrap_qta", fake_unwrap)
    session = MagicMock()
    session.post.return_value = _FakeResponse(200, {"text": "ok"})
    audio_to_text.request_transcription(
        qta, "key", prepare=False, session=session
    )
    filename, handle, mime = session.post.call_args.kwargs["files"]["file"]
    assert filename == "talk.m4a"
    assert mime == "audio/mp4"
    handle.close()
    assert not m4a.exists()


def test_logs_duration_and_lengths(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "audio_to_text.log"
    monkeypatch.setattr(audio_to_text, "LOG_PATH", log_path)
    session = MagicMock()
    session.post.return_value = _FakeResponse(
        200,
        {
            "text": "Hello world.",
            "duration": 1.25,
            "words": [
                {"text": "Hello", "speaker": 0, "start": 0.0, "end": 0.3},
                {"text": "world.", "speaker": 0, "start": 0.3, "end": 0.6},
            ],
        },
    )
    audio_to_text.transcribe_file(audio_file, "key", session=session)
    logged = log_path.read_text(encoding="utf-8")
    assert "duration=1.25" in logged
    assert "len(text)=12" in logged
    assert "len(words)=2" in logged
    assert "reconstructed=12" in logged


def test_parse_args_merge_and_format_defaults() -> None:
    args = audio_to_text.parse_args(["clip.m4a"])
    assert args.min_turn_seconds == 1.2
    assert args.min_turn_words == 3
    assert args.no_merge_turns is False
    assert args.prefer_complete is True
    assert args.no_format is False
    disabled = audio_to_text.parse_args(
        [
            "--no-merge-turns",
            "--no-prefer-complete",
            "--no-format",
            "--min-turn-seconds",
            "0.8",
            "--min-turn-words",
            "2",
            "clip.m4a",
        ]
    )
    assert disabled.no_merge_turns is True
    assert disabled.prefer_complete is False
    assert disabled.no_format is True
    assert disabled.min_turn_seconds == 0.8
    assert disabled.min_turn_words == 2


def test_main_no_prefer_complete_appends_full_text(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    fake = _FakeResponse(
        200,
        {
            "text": "A" * 100,
            "words": [
                {"text": "Hi", "speaker": 0, "start": 0.0, "end": 0.2},
            ],
        },
    )
    with patch("audio_to_text.requests.post", return_value=fake):
        rc = audio_to_text.main(["--no-prefer-complete", str(audio_file)])
    assert rc == 0
    dest = audio_file.with_suffix(".txt")
    assert dest.read_text(encoding="utf-8") == (
        "Speaker 0: Hi\n"
        "\n"
        "---\n"
        "Full STT text (no speakers)\n"
        "\n" + ("A" * 100) + "\n"
    )


def test_status_message_success_uses_filename_only(tmp_path: Path) -> None:
    path = tmp_path / "voicemail-ABC.m4a"
    msg = audio_to_text.status_message(path)
    assert msg == "File voicemail-ABC.m4a has been successfully transcribed"
    assert str(tmp_path) not in msg


def test_status_message_summarized_and_from_transcript() -> None:
    path = Path("/Users/rob/Downloads/standup.m4a")
    assert audio_to_text.status_message(path, summarized=True) == (
        "File standup.m4a has been successfully transcribed and summarized"
    )
    assert audio_to_text.status_message(
        path, summarized=True, from_transcript=True
    ) == "File standup.m4a has been successfully summarized"


def test_status_message_error_strips_full_path(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    err = audio_to_text.AudioToTextError(f"File not found: {path}")
    msg = audio_to_text.status_message(path, error=err)
    assert msg == f"Could not transcribe {path.name}: File not found: {path.name}"
    assert str(tmp_path) not in msg
    labeled = audio_to_text.AudioToTextError(f"{path.name}: STT request failed (HTTP 500)")
    assert audio_to_text.status_message(path, summarized=True, error=labeled) == (
        "Could not transcribe and summarize clip.mp4: STT request failed (HTTP 500)"
    )


def test_main_notify_prints_status_not_path(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    fake = _FakeResponse(200, {"text": "Hello."})
    with patch("audio_to_text.requests.post", return_value=fake), patch(
        "audio_to_text.notify"
    ) as notify_mock:
        rc = audio_to_text.main(["--notify", str(audio_file)])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    expected = "File clip.mp4 has been successfully transcribed"
    assert out == expected
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
        rc = audio_to_text.main(["--notify", str(audio_file)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Could not transcribe clip.mp4:" in captured.out
    assert "API key" in captured.out
    assert str(audio_file.parent) not in captured.out
    assert "Error:" in captured.err
    notify_mock.assert_called_once()
    assert notify_mock.call_args.kwargs.get("error") is True
    assert "Could not transcribe clip.mp4:" in notify_mock.call_args.args[1]


def test_order_parts_matches_finder_numbering(tmp_path: Path) -> None:
    names = ["Call 10.m4a", "Call 2.m4a", "Call.m4a"]
    ordered = audio_to_text.order_parts([tmp_path / name for name in names])
    assert [path.name for path in ordered] == ["Call.m4a", "Call 2.m4a", "Call 10.m4a"]


def test_plan_jobs_join_is_one_job(tmp_path: Path) -> None:
    files = [str(tmp_path / "b.m4a"), str(tmp_path / "a.m4a")]
    assert audio_to_text.plan_jobs(files, join=False) == [
        (tmp_path / "b.m4a", []),
        (tmp_path / "a.m4a", []),
    ]
    assert audio_to_text.plan_jobs(files, join=True) == [
        (tmp_path / "a.m4a", [tmp_path / "b.m4a"])
    ]
    assert audio_to_text.plan_jobs(files[:1], join=True) == [(tmp_path / "b.m4a", [])]


def _two_parts(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path / "Call.m4a"
    second = tmp_path / "Call 2.m4a"
    first.write_bytes(b"part-one")
    second.write_bytes(b"part-two")
    return first, second


def test_join_recordings_runs_ffmpeg_concat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _two_parts(tmp_path)
    monkeypatch.setattr(audio_to_text.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    seen: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        Path(command[-1]).write_bytes(b"RIFFjoined")
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(audio_to_text.subprocess, "run", fake_run)
    joined = audio_to_text.join_recordings([first, second])
    try:
        command = seen["command"]
        assert joined.name == "Call.wav"
        assert joined.parent != tmp_path
        assert [command[i + 1] for i, arg in enumerate(command) if arg == "-i"] == [
            str(first),
            str(second),
        ]
        graph = command[command.index("-filter_complex") + 1]
        assert graph == "[0:a:0][1:a:0]concat=n=2:v=0:a=1[joined]"
        assert command[command.index("-ac") + 1] == "1"
        assert command[command.index("-ar") + 1] == "16000"
    finally:
        audio_to_text.shutil.rmtree(joined.parent)


def test_join_recordings_requires_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _two_parts(tmp_path)
    monkeypatch.setattr(audio_to_text.shutil, "which", lambda name: None)
    with pytest.raises(audio_to_text.AudioToTextError, match="ffmpeg is required to join"):
        audio_to_text.join_recordings([first, second])


def test_join_recordings_failure_names_parts_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _two_parts(tmp_path)
    monkeypatch.setattr(audio_to_text.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    workdirs: list[Path] = []

    def fake_run(command, **kwargs):
        workdirs.append(Path(command[-1]).parent)
        return MagicMock(returncode=1, stderr="Invalid data found")

    monkeypatch.setattr(audio_to_text.subprocess, "run", fake_run)
    with pytest.raises(
        audio_to_text.AudioToTextError,
        match=r"Could not join Call\.m4a \+ Call 2\.m4a: Invalid data found",
    ):
        audio_to_text.join_recordings([first, second])
    assert workdirs and not workdirs[0].exists()


def test_main_join_writes_one_txt_named_after_first_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = _two_parts(tmp_path)
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    workdir = tmp_path / "joined"
    workdir.mkdir()
    joined_calls: list[list[Path]] = []

    def fake_join(paths, *, mono=True):
        joined_calls.append(list(paths))
        dest = workdir / "Call.wav"
        dest.write_bytes(b"RIFFjoined")
        return dest

    uploads: list[tuple[str, bytes]] = []

    def fake_post(url, **kwargs):
        name, handle, _mime = kwargs["files"]["file"]
        uploads.append((name, handle.read()))
        return _FakeResponse(200, {"text": "Both halves."})

    monkeypatch.setattr(audio_to_text, "join_recordings", fake_join)
    expected = "File Call.m4a (+1 joined) has been successfully transcribed"
    with patch("audio_to_text.requests.post", side_effect=fake_post), patch(
        "audio_to_text.notify"
    ):
        # Reverse order on the command line: parts are joined in name order.
        rc = audio_to_text.main(["--notify", "--join", str(second), str(first)])
    assert rc == 0
    assert joined_calls == [[first, second]]
    assert uploads == [("Call.wav", b"RIFFjoined")]
    assert (tmp_path / "Call.txt").read_text(encoding="utf-8") == "Both halves.\n"
    assert not (tmp_path / "Call 2.txt").exists()
    assert not workdir.exists()
    assert capsys.readouterr().out.strip() == expected


def test_main_without_join_keeps_one_txt_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _two_parts(tmp_path)
    monkeypatch.setenv("XAI_API_KEY", "secret-key")
    fake = _FakeResponse(200, {"text": "One half."})
    with patch("audio_to_text.requests.post", return_value=fake):
        rc = audio_to_text.main([str(first), str(second)])
    assert rc == 0
    assert (tmp_path / "Call.txt").is_file()
    assert (tmp_path / "Call 2.txt").is_file()


def _levels(seconds: float, loud: dict[tuple[float, float], float]) -> list[tuple[float, float]]:
    frames = []
    for i in range(int(seconds * 10)):
        stamp = round(i * 0.1, 1)
        level = -60.0
        for (start, end), db in loud.items():
            if start <= stamp < end:
                level = db
        frames.append((stamp, level))
    return frames


def _word(text: str, start: float, end: float, speaker: int = 0) -> dict:
    return {"text": text, "speaker": speaker, "start": start, "end": end}


def test_parse_speech_levels_reads_frames_and_silence() -> None:
    output = (
        "frame:0    pts:0       pts_time:0\n"
        "lavfi.astats.Overall.RMS_level=-26.5\n"
        "frame:1    pts:1600    pts_time:0.1\n"
        "lavfi.astats.Overall.RMS_level=-inf\n"
    )
    assert audio_to_text.parse_speech_levels(output) == [
        (0.0, -26.5),
        (0.1, audio_to_text.SILENCE_DB),
    ]


def test_find_missing_speech_flags_untranscribed_talking() -> None:
    words = [_word("Hello", 0.0, 1.0), _word("there.", 1.0, 2.0), _word("Bye.", 10.0, 11.0)]
    levels = _levels(12, {(0, 2): -25, (3, 8): -30, (10, 11): -25})
    spans = audio_to_text.find_missing_speech(words, levels)
    assert len(spans) == 1
    assert spans[0][0] == pytest.approx(3.0)
    assert spans[0][1] == pytest.approx(8.0)


def test_find_missing_speech_ignores_quiet_pauses_and_short_blips() -> None:
    words = [_word("Hello", 0.0, 1.0), _word("Bye.", 10.0, 11.0)]
    levels = _levels(12, {(0, 1): -25, (5, 6): -28, (10, 11): -25})
    assert audio_to_text.find_missing_speech(words, levels) == []


def test_find_missing_speech_sees_through_stretched_word() -> None:
    # Regression: STT returned "Yes." spanning 7.5-12.6 s over a dropped passage.
    words = [_word("Good,", 2.6, 2.8), _word("Yes.", 7.5, 12.6), _word("Bye.", 14.0, 14.5)]
    levels = _levels(15, {(2.6, 2.8): -25, (3.5, 12.6): -30, (14.0, 14.5): -25})
    spans = audio_to_text.find_missing_speech(words, levels)
    assert len(spans) == 1
    assert spans[0][0] == pytest.approx(3.5)
    assert spans[0][1] == pytest.approx(10.85, abs=0.1)


def test_find_missing_speech_needs_timed_words() -> None:
    levels = _levels(5, {(0, 5): -25})
    assert audio_to_text.find_missing_speech([{"text": "Hi"}], levels) == []


def test_format_spans_uses_clock_times() -> None:
    assert audio_to_text.format_spans([(3.0, 18.2), (3700.0, 3725.0)]) == (
        "0:03–0:19, 1:01:40–1:02:05"
    )


def test_transcribe_file_notes_missing_speech(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    levels = _levels(12, {(0, 2): -25, (3, 8): -30, (10, 11): -25})
    monkeypatch.setattr(audio_to_text, "speech_levels", lambda path: levels)
    session = MagicMock()
    session.post.return_value = _FakeResponse(
        200,
        {
            "text": "Hello there. Bye.",
            "words": [
                _word("Hello", 0.0, 1.0),
                _word("there.", 1.0, 2.0),
                _word("Bye.", 10.0, 11.0),
            ],
        },
    )
    text = audio_to_text.transcribe_file(audio_file, "key", session=session)
    assert text.startswith("Note: possible missing speech at 0:03–0:08.")
    assert text.endswith("Speaker 0: Hello there. Bye.")
    assert "possible missing speech at 0:03–0:08" in capsys.readouterr().err


def test_check_missing_speech_skips_multichannel(
    audio_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(path):
        raise AssertionError("should not probe levels")

    monkeypatch.setattr(audio_to_text, "speech_levels", fail)
    payload = {"words": [_word("Hi", 0.0, 1.0)]}
    assert audio_to_text.check_missing_speech(audio_file, payload, multichannel=True) == []


def test_status_message_warns_about_missing_speech(tmp_path: Path) -> None:
    msg = audio_to_text.status_message(
        tmp_path / "call.m4a", summarized=True, missing_speech=[(0.0, 18.4)]
    )
    assert msg == (
        "File call.m4a has been successfully transcribed and summarized, "
        "but may be missing speech at 0:00–0:19"
    )
