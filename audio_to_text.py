#!/usr/bin/env python3
"""Transcribe audio/video files to a sidecar .txt via the xAI Grok STT API."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import requests

STT_URL = "https://api.x.ai/v1/stt"
ENV_FILE_PATH = Path.home() / ".env"
LOG_PATH = Path("/tmp/audio_to_text.log")
MAX_FILE_BYTES = 500 * 1024 * 1024
STT_SAMPLE_RATE = 16000
DEFAULT_STT_MODEL = "grok-voice-transcribe-2.0"
DEFAULT_LANGUAGE = "en"
DEFAULT_MIN_TURN_SECONDS = 1.2
DEFAULT_MIN_TURN_WORDS = 3
BARGE_IN_GAP_SECONDS = 0.35
ISOLATION_GAP_SECONDS = 0.8
INCOMPLETE_WORD_RATIO = 0.85
WORDS_INCOMPLETE_WARNING = "diarization words shorter than text; wrote complete text."
FULL_TEXT_HEADING = "Full STT text (no speakers)"

SUPPORTED_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".ogg",
    ".opus",
    ".flac",
    ".aac",
    ".mp4",
    ".m4a",
    ".mkv",
    ".qta",
}

MIME_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".mp4": "video/mp4",
    ".m4a": "audio/mp4",
    ".mkv": "video/x-matroska",
}


class AudioToTextError(Exception):
    """User-facing transcription error."""


def log(message: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def load_env_file(env_file: Path | None = None) -> None:
    """Load KEY=value pairs from ~/.env without overriding existing env vars."""
    path = env_file if env_file is not None else ENV_FILE_PATH
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Warning: Could not read {path}: {exc}", file=sys.stderr)
        return

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and not os.getenv(key):
            os.environ[key] = value

    if os.getenv("GROK_API_KEY") and not os.getenv("XAI_API_KEY"):
        os.environ["XAI_API_KEY"] = os.getenv("GROK_API_KEY", "")


def stt_model() -> str:
    """Latest Grok Voice Transcribe model. Override with GROK_STT_MODEL."""
    return os.getenv("GROK_STT_MODEL") or DEFAULT_STT_MODEL


def get_api_key() -> str:
    load_env_file()
    key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY") or ""
    if not key:
        raise AudioToTextError(
            "No API key found. Set XAI_API_KEY or GROK_API_KEY in the environment or ~/.env"
        )
    return key


def suffix_of(path: Path) -> str:
    return path.suffix.lower()


def is_supported(path: Path) -> bool:
    return suffix_of(path) in SUPPORTED_EXTENSIONS


def output_path_for(path: Path) -> Path:
    return path.with_suffix(".txt")


def timeout_for(path: Path) -> int:
    try:
        size = path.stat().st_size
    except OSError:
        return 300
    if size > 200 * 1024 * 1024:
        return 1200
    if size > 50 * 1024 * 1024:
        return 600
    return 300


def audio_channel_count(path: Path) -> int | None:
    """Return the first audio stream's channel count, or None if unknown."""
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=channels",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    lines = (completed.stdout or "").strip().splitlines()
    if not lines:
        return None
    raw = lines[0].strip().split(",")[0].strip()
    try:
        count = int(raw)
    except ValueError:
        return None
    if count < 1:
        return None
    return count


def unwrap_qta(path: Path) -> Path:
    """Remux the first audio stream of a .qta container to a temp .m4a."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioToTextError(
            "ffmpeg is required to transcribe .qta files. "
            "Install ffmpeg and retry."
        )
    fd, raw = tempfile.mkstemp(prefix="audio_to_text_", suffix=".m4a")
    os.close(fd)
    dest = Path(raw)
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-c:a",
                "copy",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_for(path),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        dest.unlink(missing_ok=True)
        raise AudioToTextError(
            f"{path.name}: could not remux .qta to .m4a: {exc}"
        ) from exc
    if completed.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        err = (completed.stderr or "").strip().replace("\n", " ")[:200]
        dest.unlink(missing_ok=True)
        detail = f": {err}" if err else ""
        raise AudioToTextError(
            f"{path.name}: could not remux .qta to .m4a{detail}"
        )
    log(f"remuxed qta to m4a file={path.name} bytes={dest.stat().st_size}")
    return dest


def prepare_stt_wav(path: Path) -> Path | None:
    """Downmix to 16 kHz mono WAV — the same input whisper.cpp wants."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log(f"ffmpeg not found; sending original {path.name}")
        return None
    fd, raw = tempfile.mkstemp(prefix="audio_to_text_", suffix=".wav")
    os.close(fd)
    dest = Path(raw)
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                str(STT_SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_for(path),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        dest.unlink(missing_ok=True)
        log(f"prepare failed file={path.name} error={exc}")
        return None
    if completed.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        err = (completed.stderr or "").strip().replace("\n", " ")[:200]
        dest.unlink(missing_ok=True)
        log(f"prepare ffmpeg failed file={path.name} {err}")
        return None
    log(f"prepared 16kHz mono wav file={path.name} bytes={dest.stat().st_size}")
    return dest


def validate_input(path: Path) -> None:
    if not path.exists():
        raise AudioToTextError(f"File not found: {path}")
    if not path.is_file():
        raise AudioToTextError(f"Not a file: {path}")
    if not is_supported(path):
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise AudioToTextError(
            f"Unsupported file type: {path.suffix or path.name}. Supported: {supported}"
        )
    size = path.stat().st_size
    if size == 0:
        raise AudioToTextError(f"{path.name} is empty")
    if size > MAX_FILE_BYTES:
        raise AudioToTextError(
            f"{path.name} is {size} bytes; Grok STT accepts at most 500 MB"
        )


@dataclass
class _Turn:
    speaker: Any
    tokens: list[str]
    start: float | None = None
    end: float | None = None

    @property
    def word_count(self) -> int:
        return sum(1 for token in self.tokens if token != "")

    @property
    def duration(self) -> float | None:
        if self.start is None or self.end is None:
            return None
        return max(0.0, self.end - self.start)

    def body(self) -> str:
        return " ".join(token for token in self.tokens if token != "")


def _as_time(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def join_word_texts(words: Sequence[Any]) -> str:
    tokens: list[str] = []
    for word in words:
        if not isinstance(word, dict):
            continue
        token = word.get("text")
        if isinstance(token, str) and token != "":
            tokens.append(token)
    return " ".join(tokens)


def whitespace_normalized_len(text: str) -> int:
    return len(" ".join(text.split()))


def words_incomplete(reconstructed: str, text: str) -> bool:
    text_len = whitespace_normalized_len(text)
    if text_len == 0:
        return False
    return whitespace_normalized_len(reconstructed) < INCOMPLETE_WORD_RATIO * text_len


def _gap_seconds(left: _Turn | None, right: _Turn | None) -> float | None:
    if left is None or right is None:
        return None
    if left.end is None or right.start is None:
        return None
    return max(0.0, right.start - left.end)


def _is_isolated(turns: Sequence[_Turn], index: int) -> bool:
    turn = turns[index]
    prev = turns[index - 1] if index > 0 else None
    nxt = turns[index + 1] if index + 1 < len(turns) else None
    if prev is None:
        isolated_before = True
    else:
        gap = _gap_seconds(prev, turn)
        isolated_before = gap is not None and gap >= ISOLATION_GAP_SECONDS
    if nxt is None:
        isolated_after = True
    else:
        gap = _gap_seconds(turn, nxt)
        isolated_after = gap is not None and gap >= ISOLATION_GAP_SECONDS
    return isolated_before and isolated_after


def _is_flicker(
    turns: Sequence[_Turn],
    index: int,
    *,
    min_turn_seconds: float,
    min_turn_words: int,
) -> bool:
    turn = turns[index]
    duration = turn.duration
    if duration is None or duration >= min_turn_seconds:
        return False
    if turn.word_count > min_turn_words:
        return False
    if _is_isolated(turns, index):
        return False

    prev = turns[index - 1] if index > 0 else None
    nxt = turns[index + 1] if index + 1 < len(turns) else None
    sandwiched = (
        prev is not None
        and nxt is not None
        and prev.speaker != turn.speaker
        and nxt.speaker != turn.speaker
    )
    gap_before = _gap_seconds(prev, turn)
    barge_in = (
        prev is not None
        and turn.word_count <= 2
        and gap_before is not None
        and gap_before < BARGE_IN_GAP_SECONDS
    )
    return sandwiched or barge_in


def _absorb(target: _Turn, source: _Turn, *, prepend: bool = False) -> None:
    if prepend:
        target.tokens = source.tokens + target.tokens
        if source.start is not None:
            target.start = (
                source.start if target.start is None else min(target.start, source.start)
            )
        if source.end is not None and target.end is None:
            target.end = source.end
    else:
        target.tokens = target.tokens + source.tokens
        if source.end is not None:
            target.end = source.end if target.end is None else max(target.end, source.end)
        if source.start is not None and target.start is None:
            target.start = source.start


def _coalesce_adjacent(turns: list[_Turn]) -> None:
    i = 0
    while i < len(turns) - 1:
        if turns[i].speaker == turns[i + 1].speaker:
            _absorb(turns[i], turns[i + 1])
            del turns[i + 1]
            continue
        i += 1


def merge_flicker_turns(
    turns: list[_Turn],
    *,
    min_turn_seconds: float = DEFAULT_MIN_TURN_SECONDS,
    min_turn_words: int = DEFAULT_MIN_TURN_WORDS,
) -> list[_Turn]:
    """Collapse implausible micro-turns into the adjacent speaker."""
    if len(turns) < 2:
        return turns
    merged = [
        _Turn(speaker=turn.speaker, tokens=list(turn.tokens), start=turn.start, end=turn.end)
        for turn in turns
    ]
    i = 0
    while i < len(merged):
        if _is_flicker(
            merged,
            i,
            min_turn_seconds=min_turn_seconds,
            min_turn_words=min_turn_words,
        ):
            if i == 0:
                _absorb(merged[1], merged[0], prepend=True)
                del merged[0]
            else:
                _absorb(merged[i - 1], merged[i])
                del merged[i]
            _coalesce_adjacent(merged)
            i = 0
            continue
        i += 1
    return merged


def _group_speaker_turns(dict_words: list[dict[str, Any]]) -> list[_Turn]:
    turns: list[_Turn] = []
    for word in dict_words:
        token = word.get("text")
        if not isinstance(token, str):
            token = ""
        speaker = word["speaker"]
        start = _as_time(word.get("start"))
        end = _as_time(word.get("end"))
        if turns and turns[-1].speaker == speaker:
            turns[-1].tokens.append(token)
            if turns[-1].start is None:
                turns[-1].start = start
            if end is not None:
                turns[-1].end = end
            continue
        turns.append(_Turn(speaker=speaker, tokens=[token], start=start, end=end))
    return turns


def _labeled_from_turns(turns: Sequence[_Turn]) -> str:
    paragraphs = [f"Speaker {turn.speaker}: {turn.body()}".rstrip() for turn in turns]
    return "\n\n".join(paragraphs)


def _channel_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    channels = payload.get("channels")
    if not isinstance(channels, list):
        return []
    return [channel for channel in channels if isinstance(channel, dict)]


def format_transcript(
    payload: dict[str, Any],
    *,
    use_diarization: bool = True,
    merge_turns: bool = True,
    min_turn_seconds: float = DEFAULT_MIN_TURN_SECONDS,
    min_turn_words: int = DEFAULT_MIN_TURN_WORDS,
    prefer_complete: bool = True,
) -> str:
    """Turn an STT JSON payload into sidecar plaintext.

    When diarization is on and every word has a speaker, consecutive words
    with the same speaker become one ``Speaker N: …`` paragraph. Short
    flicker turns are merged into the adjacent speaker unless disabled.
    Otherwise return ``payload["text"]`` unchanged — no invented labels.

    ``multichannel=true`` returns a ``channels`` array. Identical channel
    texts (typical stereo Voice Memo) collapse to one transcript. Distinct
    channels become ``Channel N:`` paragraphs.
    """
    channel_dicts = _channel_dicts(payload)
    if channel_dicts:
        normalized = []
        for channel in channel_dicts:
            text = channel.get("text")
            normalized.append(" ".join(text.split()) if isinstance(text, str) else "")
        nonempty = [text for text in normalized if text]
        if len(nonempty) >= 2 and len(set(nonempty)) == 1:
            first = channel_dicts[0]
            payload = {
                "text": (
                    first.get("text")
                    if isinstance(first.get("text"), str)
                    else payload.get("text")
                ),
                "words": (
                    first.get("words")
                    if isinstance(first.get("words"), list)
                    else payload.get("words")
                ),
            }
        elif len(nonempty) >= 2:
            paragraphs = []
            for channel in channel_dicts:
                text = channel.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                index = channel.get("index", len(paragraphs))
                paragraphs.append(f"Channel {index}: {text}".rstrip())
            if paragraphs:
                return "\n\n".join(paragraphs)

    fallback = payload.get("text")
    fallback_str = fallback if isinstance(fallback, str) else ""
    if not use_diarization:
        return fallback_str

    words = payload.get("words")
    if not isinstance(words, list) or not words:
        return fallback_str

    dict_words = [word for word in words if isinstance(word, dict)]
    reconstructed = join_word_texts(dict_words)
    incomplete = words_incomplete(reconstructed, fallback_str)

    if not dict_words or not all(
        "speaker" in word and word["speaker"] is not None for word in dict_words
    ):
        return fallback_str

    turns = _group_speaker_turns(dict_words)
    if merge_turns:
        turns = merge_flicker_turns(
            turns,
            min_turn_seconds=min_turn_seconds,
            min_turn_words=min_turn_words,
        )
    labeled = _labeled_from_turns(turns)
    if not labeled:
        return fallback_str
    if not incomplete:
        return labeled
    if prefer_complete:
        print(WORDS_INCOMPLETE_WARNING, file=sys.stderr)
        return fallback_str
    return f"{labeled}\n\n---\n{FULL_TEXT_HEADING}\n\n{fallback_str}"


def request_transcription(
    path: Path,
    api_key: str,
    language: str = DEFAULT_LANGUAGE,
    *,
    diarize: bool = True,
    format_text: bool = True,
    prepare: bool = True,
    multichannel: bool = False,
    model: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    validate_input(path)
    unwrapped: Path | None = None
    prepared: Path | None = None
    send_path = path
    try:
        if suffix_of(path) == ".qta":
            unwrapped = unwrap_qta(path)
            send_path = unwrapped
        if prepare and not multichannel:
            prepared = prepare_stt_wav(send_path)
            if prepared is not None:
                send_path = prepared
        mime = MIME_TYPES.get(suffix_of(send_path), "audio/wav")
        post = session.post if session is not None else requests.post
        chosen_model = model or stt_model()
        data: list[tuple[str, str]] = [("model", chosen_model)]
        if format_text:
            data.append(("format", "true"))
        data.append(("language", language))
        data.append(("filler_words", "true"))
        if diarize:
            data.append(("diarize", "true"))
        if multichannel:
            data.append(("multichannel", "true"))
        last_error: Exception | None = None
        if send_path == path:
            upload_name = path.name
        else:
            upload_name = f"{path.stem}{send_path.suffix}"

        for attempt in range(1, 4):
            try:
                with send_path.open("rb") as handle:
                    response = post(
                        STT_URL,
                        headers={"Authorization": f"Bearer {api_key}"},
                        data=data,
                        files={"file": (upload_name, handle, mime)},
                        timeout=timeout_for(path),
                    )
            except requests.RequestException as exc:
                last_error = AudioToTextError(f"{path.name}: network error: {exc}")
                log(f"network error attempt={attempt} file={path.name} error={exc}")
                time.sleep(min(2 ** attempt, 8))
                continue

            if response.status_code in {429, 503}:
                log(
                    f"retryable status={response.status_code} "
                    f"attempt={attempt} file={path.name}"
                )
                time.sleep(min(2 ** attempt, 8))
                last_error = AudioToTextError(
                    f"{path.name}: STT service busy (HTTP {response.status_code})"
                )
                continue

            if response.status_code == 401:
                raise AudioToTextError("STT API key is missing or invalid (HTTP 401)")
            if response.status_code == 413:
                raise AudioToTextError(f"{path.name}: file exceeds 500 MB (HTTP 413)")
            if response.status_code >= 400:
                detail = _response_detail(response)
                raise AudioToTextError(
                    f"{path.name}: STT request failed "
                    f"(HTTP {response.status_code}){detail}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise AudioToTextError(f"{path.name}: STT returned non-JSON") from exc

            if not isinstance(payload, dict):
                raise AudioToTextError(f"{path.name}: STT returned non-object JSON")
            if payload.get("text") is None and not payload.get("words"):
                raise AudioToTextError(f"{path.name}: STT response missing text")
            words = payload.get("words") if isinstance(payload.get("words"), list) else []
            text = payload.get("text") if isinstance(payload.get("text"), str) else ""
            reconstructed = join_word_texts(words)
            log(
                f"transcribed file={path.name} model={chosen_model} "
                f"duration={payload.get('duration')} "
                f"len(text)={len(text)} len(words)={len(words)} "
                f"reconstructed={len(reconstructed)} "
                f"prepared={prepared is not None} multichannel={multichannel}"
            )
            return payload

        raise last_error or AudioToTextError(f"{path.name}: STT request failed")
    finally:
        if prepared is not None:
            prepared.unlink(missing_ok=True)
        if unwrapped is not None:
            unwrapped.unlink(missing_ok=True)


def transcribe_file(
    path: Path,
    api_key: str,
    language: str = DEFAULT_LANGUAGE,
    session: requests.Session | None = None,
    *,
    diarize: bool = True,
    format_text: bool = True,
    prepare: bool = True,
    multichannel: bool = False,
    merge_turns: bool = True,
    min_turn_seconds: float = DEFAULT_MIN_TURN_SECONDS,
    min_turn_words: int = DEFAULT_MIN_TURN_WORDS,
    prefer_complete: bool = True,
) -> str:
    payload = request_transcription(
        path,
        api_key,
        language=language,
        diarize=diarize,
        format_text=format_text,
        prepare=prepare,
        multichannel=multichannel,
        session=session,
    )
    return format_transcript(
        payload,
        use_diarization=diarize,
        merge_turns=merge_turns,
        min_turn_seconds=min_turn_seconds,
        min_turn_words=min_turn_words,
        prefer_complete=prefer_complete,
    )


def json_sidecar_path(source: Path, json_out: str | None) -> Path | None:
    env_on = os.getenv("AUDIO_TO_TEXT_SAVE_JSON", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if json_out:
        dest = Path(json_out).expanduser()
        if dest.is_dir() or json_out.endswith(("/", os.sep)):
            return dest / f"{source.stem}.stt.json"
        return dest
    if env_on:
        return source.with_suffix(".stt.json")
    return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.write_text(body, encoding="utf-8")
    log(f"wrote {path}")


def _response_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        body = (response.text or "").strip().replace("\n", " ")
        return f": {body[:240]}" if body else ""
    if not isinstance(payload, dict):
        return ""
    for key in ("error", "message", "detail"):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("message") or value.get("code")
        if value:
            return f": {value}"
    return ""


def write_transcript(source: Path, text: str) -> Path:
    dest = output_path_for(source)
    if not text.endswith("\n"):
        text = text + "\n"
    dest.write_text(text, encoding="utf-8")
    log(f"wrote {dest}")
    return dest


def _applescript_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


NOTIFY_TITLE = "Convert to Text"


def status_message(
    path: Path | None,
    *,
    summarized: bool = False,
    from_transcript: bool = False,
    error: BaseException | str | None = None,
) -> str:
    """One-line Finder/notification status. Filename only, never a full path."""
    if from_transcript:
        done = "summarized"
        fail = "summarize"
    elif summarized:
        done = "transcribed and summarized"
        fail = "transcribe and summarize"
    else:
        done = "transcribed"
        fail = "transcribe"

    name = path.name if path is not None else None
    if error is None:
        if name:
            return f"File {name} has been successfully {done}"
        return f"File has been successfully {done}"

    detail = str(error)
    if path is not None:
        for raw in (str(path), str(path.expanduser())):
            if raw:
                detail = detail.replace(raw, path.name)
        labeled = f"{path.name}: "
        if detail.startswith(labeled):
            detail = detail[len(labeled):]
    if name:
        return f"Could not {fail} {name}: {detail}"
    return f"Could not {fail}: {detail}"


def notify(title: str, message: str, *, error: bool = False) -> None:
    title = " ".join(title.split())[:80]
    message = " ".join(message.split())[:400]
    extra = ' subtitle "Failed"' if error else ""
    script = (
        f"display notification {_applescript_quote(message)} "
        f"with title {_applescript_quote(title)}{extra}"
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except OSError:
        pass


def emit_notification(message: str, *, error: bool = False) -> None:
    print(message)
    notify(NOTIFY_TITLE, message, error=error)


def process_file(
    path: Path,
    api_key: str,
    language: str,
    dry_run: bool,
    do_notify: bool,
    *,
    diarize: bool = True,
    json_out: str | None = None,
    format_text: bool = True,
    prepare: bool = True,
    multichannel: bool = False,
    merge_turns: bool = True,
    min_turn_seconds: float = DEFAULT_MIN_TURN_SECONDS,
    min_turn_words: int = DEFAULT_MIN_TURN_WORDS,
    prefer_complete: bool = True,
) -> Path:
    dest = output_path_for(path)
    if dry_run:
        validate_input(path)
        print(f"Would transcribe {path} -> {dest}")
        return dest
    payload = request_transcription(
        path,
        api_key,
        language=language,
        diarize=diarize,
        format_text=format_text,
        prepare=prepare,
        multichannel=multichannel,
    )
    sidecar = json_sidecar_path(path, json_out)
    if sidecar is not None:
        write_json(sidecar, payload)
    text = format_transcript(
        payload,
        use_diarization=diarize,
        merge_turns=merge_turns,
        min_turn_seconds=min_turn_seconds,
        min_turn_words=min_turn_words,
        prefer_complete=prefer_complete,
    )
    written = write_transcript(path, text)
    if do_notify:
        emit_notification(status_message(path))
    else:
        print(written)
    return written


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe audio or MP4 files to a sidecar .txt using Grok STT."
    )
    parser.add_argument("files", nargs="+", help="Audio or MP4 files to transcribe")
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        help="Language code for text formatting (default: en)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help=(
            "macOS notification when finished; print a short status line "
            "instead of the output path"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print output paths without calling the API",
    )
    parser.add_argument(
        "--no-diarize",
        action="store_true",
        help="Do not request speaker labels; write the merged text field",
    )
    parser.add_argument(
        "--no-format",
        action="store_true",
        help="Omit format=true (inverse text normalization) for an STT completeness A/B",
    )
    parser.add_argument(
        "--min-turn-seconds",
        type=float,
        default=DEFAULT_MIN_TURN_SECONDS,
        metavar="SEC",
        help=(
            "Merge speaker turns shorter than this many seconds "
            f"(default: {DEFAULT_MIN_TURN_SECONDS})"
        ),
    )
    parser.add_argument(
        "--min-turn-words",
        type=int,
        default=DEFAULT_MIN_TURN_WORDS,
        metavar="N",
        help=(
            "Merge speaker turns with this many words or fewer "
            f"(default: {DEFAULT_MIN_TURN_WORDS})"
        ),
    )
    parser.add_argument(
        "--no-merge-turns",
        action="store_true",
        help="Do not collapse short flicker turns into the adjacent speaker",
    )
    parser.add_argument(
        "--prefer-complete",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "When diarized words are shorter than text, write the complete text "
            "(default: true)"
        ),
    )
    parser.add_argument(
        "--json-out",
        metavar="PATH",
        help="Write raw STT JSON to PATH (or AUDIO_TO_TEXT_SAVE_JSON=1 for <stem>.stt.json)",
    )
    parser.add_argument(
        "--multichannel",
        action="store_true",
        help=(
            "Transcribe each audio channel separately (true split-channel "
            "recordings). Skips 16 kHz mono prepare. Do not use on Voice Memos "
            "or other mixed stereo — it duplicates every word."
        ),
    )
    parser.add_argument(
        "--no-prepare",
        action="store_true",
        help="Send the original file; do not convert to 16 kHz mono WAV first",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    failures = 0
    api_key = ""
    if not args.dry_run:
        try:
            api_key = get_api_key()
        except AudioToTextError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            if args.notify:
                first = Path(args.files[0]).expanduser() if args.files else None
                emit_notification(status_message(first, error=exc), error=True)
            return 1

    for raw in args.files:
        path = Path(raw).expanduser()
        try:
            process_file(
                path,
                api_key or "",
                language=args.language,
                dry_run=args.dry_run,
                do_notify=args.notify,
                diarize=not args.no_diarize,
                json_out=args.json_out,
                format_text=not args.no_format,
                prepare=not args.no_prepare,
                multichannel=args.multichannel,
                merge_turns=not args.no_merge_turns,
                min_turn_seconds=args.min_turn_seconds,
                min_turn_words=args.min_turn_words,
                prefer_complete=args.prefer_complete,
            )
        except AudioToTextError as exc:
            failures += 1
            log(f"error file={path} error={exc}")
            print(f"Error: {exc}", file=sys.stderr)
            if args.notify:
                emit_notification(status_message(path, error=exc), error=True)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
