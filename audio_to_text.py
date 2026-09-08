#!/usr/bin/env python3
"""Transcribe audio/video files to a sidecar .txt via the xAI Grok STT API."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

import requests

STT_URL = "https://api.x.ai/v1/stt"
ENV_FILE_PATH = Path.home() / ".env"
LOG_PATH = Path("/tmp/audio_to_text.log")
MAX_FILE_BYTES = 500 * 1024 * 1024
DEFAULT_LANGUAGE = "en"

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


def transcribe_file(
    path: Path,
    api_key: str,
    language: str = DEFAULT_LANGUAGE,
    session: requests.Session | None = None,
) -> str:
    validate_input(path)
    mime = MIME_TYPES[suffix_of(path)]
    post = session.post if session is not None else requests.post
    data = [("format", "true"), ("language", language)]
    last_error: Exception | None = None

    for attempt in range(1, 4):
        try:
            with path.open("rb") as handle:
                response = post(
                    STT_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files={"file": (path.name, handle, mime)},
                    timeout=timeout_for(path),
                )
        except requests.RequestException as exc:
            last_error = AudioToTextError(f"{path.name}: network error: {exc}")
            log(f"network error attempt={attempt} file={path.name} error={exc}")
            time.sleep(min(2 ** attempt, 8))
            continue

        if response.status_code in {429, 503}:
            log(f"retryable status={response.status_code} attempt={attempt} file={path.name}")
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
                f"{path.name}: STT request failed (HTTP {response.status_code}){detail}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise AudioToTextError(f"{path.name}: STT returned non-JSON") from exc

        text = payload.get("text")
        if text is None:
            raise AudioToTextError(f"{path.name}: STT response missing text")
        log(
            f"transcribed file={path.name} duration={payload.get('duration')} "
            f"language={payload.get('language')} chars={len(text)}"
        )
        return text

    raise last_error or AudioToTextError(f"{path.name}: STT request failed")


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


def notify(title: str, message: str, *, error: bool = False) -> None:
    title = " ".join(title.split())[:80]
    message = " ".join(message.split())[:400]
    if error:
        script = (
            f"display alert {_applescript_quote(title)} "
            f"message {_applescript_quote(message)}"
        )
    else:
        script = (
            f"display notification {_applescript_quote(message)} "
            f"with title {_applescript_quote(title)}"
        )
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except OSError:
        pass


def process_file(
    path: Path,
    api_key: str,
    language: str,
    dry_run: bool,
    do_notify: bool,
) -> Path:
    dest = output_path_for(path)
    if dry_run:
        validate_input(path)
        print(f"Would transcribe {path} -> {dest}")
        return dest
    text = transcribe_file(path, api_key, language=language)
    written = write_transcript(path, text)
    print(written)
    if do_notify:
        notify("Convert to Text", f"Wrote {written.name}")
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
        help="Show a macOS notification (used by the Finder Quick Action)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print output paths without calling the API",
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
                notify("Convert to Text", str(exc), error=True)
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
            )
        except AudioToTextError as exc:
            failures += 1
            log(f"error file={path} error={exc}")
            print(f"Error: {exc}", file=sys.stderr)
            if args.notify:
                notify("Convert to Text", str(exc), error=True)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
