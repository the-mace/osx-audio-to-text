#!/usr/bin/env python3
"""Transcribe a meeting recording and write a structured summary via Grok."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Sequence

import requests

import audio_to_text

CHAT_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-4.20-0309-non-reasoning"
CHAT_TIMEOUT = 180
TRANSCRIPT_NAME = "transcript.txt"
SUMMARY_NAME = "summary.md"

SUMMARY_SYSTEM = """\
You write structured meeting summaries from transcripts.

Output GitHub-flavored markdown only. No preamble. No wrapping code fences.

Use this structure:

# <short meeting title>

- **Date:** <date from the transcript, or Unknown>
- **Participants:** <names if spoken, otherwise Speaker N labels>

## Key points
- …

## Decisions
- … (or "None recorded.")

## Action items
| Owner | Action | Due |
| --- | --- | --- |
| … | … | … |

Rules:
- Use only the transcript. Do not invent facts, owners, dates, or decisions.
- If a speaker gives their name, use that name instead of Speaker N.
- If ownership is unclear, put Owner as Unassigned.
- If no due date is stated, put Due as —.
- If there are no action items, write "None recorded." under the heading
  instead of an empty table.
- Keep bullets short.
"""


class MeetingSummaryError(audio_to_text.AudioToTextError):
    """User-facing meeting-summary error."""


def default_output_dir(source: Path) -> Path:
    return source.parent / source.stem


def transcript_path_for(output_dir: Path) -> Path:
    return output_dir / TRANSCRIPT_NAME


def summary_path_for(output_dir: Path) -> Path:
    return output_dir / SUMMARY_NAME


def strip_markdown_fence(text: str) -> str:
    body = text.strip()
    if not body.startswith("```"):
        return body
    lines = body.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def summary_model() -> str:
    return os.getenv("MEETING_SUMMARY_MODEL", DEFAULT_MODEL)


def write_text(path: Path, text: str) -> Path:
    if not text.endswith("\n"):
        text = text + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    audio_to_text.log(f"wrote {path}")
    return path


def request_summary(
    transcript: str,
    api_key: str,
    *,
    source_name: str,
    model: str | None = None,
    session: requests.Session | None = None,
) -> str:
    if not transcript.strip():
        raise MeetingSummaryError(f"{source_name}: transcript is empty")
    chosen = model or summary_model()
    payload: dict[str, Any] = {
        "model": chosen,
        "stream": False,
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Summarize this meeting transcript from {source_name}.\n\n"
                    f"{transcript}"
                ),
            },
        ],
    }
    post = session.post if session is not None else requests.post
    try:
        response = post(
            CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=CHAT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise MeetingSummaryError(f"{source_name}: summary network error: {exc}") from exc

    if response.status_code == 401:
        raise MeetingSummaryError("Chat API key is missing or invalid (HTTP 401)")
    if response.status_code >= 400:
        detail = audio_to_text._response_detail(response)
        raise MeetingSummaryError(
            f"{source_name}: summary request failed "
            f"(HTTP {response.status_code}){detail}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise MeetingSummaryError(f"{source_name}: summary returned non-JSON") from exc
    if not isinstance(body, dict):
        raise MeetingSummaryError(f"{source_name}: summary returned non-object JSON")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MeetingSummaryError(f"{source_name}: summary response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise MeetingSummaryError(f"{source_name}: summary choice is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise MeetingSummaryError(f"{source_name}: summary message is missing")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise MeetingSummaryError(f"{source_name}: summary content is empty")
    markdown = strip_markdown_fence(content)
    if not markdown:
        raise MeetingSummaryError(f"{source_name}: summary content is empty")
    audio_to_text.log(
        f"summarized source={source_name} model={chosen} chars={len(markdown)}"
    )
    return markdown


def transcribe_recording(
    path: Path,
    api_key: str,
    language: str,
    *,
    diarize: bool = True,
    format_text: bool = True,
    prepare: bool = True,
    multichannel: bool = False,
    merge_turns: bool = True,
    min_turn_seconds: float = audio_to_text.DEFAULT_MIN_TURN_SECONDS,
    min_turn_words: int = audio_to_text.DEFAULT_MIN_TURN_WORDS,
    prefer_complete: bool = True,
    session: requests.Session | None = None,
) -> str:
    return audio_to_text.transcribe_file(
        path,
        api_key,
        language=language,
        session=session,
        diarize=diarize,
        format_text=format_text,
        prepare=prepare,
        multichannel=multichannel,
        merge_turns=merge_turns,
        min_turn_seconds=min_turn_seconds,
        min_turn_words=min_turn_words,
        prefer_complete=prefer_complete,
    )


def process_meeting(
    path: Path,
    api_key: str,
    language: str,
    dry_run: bool,
    do_notify: bool,
    *,
    output_dir: Path | None = None,
    from_transcript: bool = False,
    model: str | None = None,
    diarize: bool = True,
    format_text: bool = True,
    prepare: bool = True,
    multichannel: bool = False,
    merge_turns: bool = True,
    min_turn_seconds: float = audio_to_text.DEFAULT_MIN_TURN_SECONDS,
    min_turn_words: int = audio_to_text.DEFAULT_MIN_TURN_WORDS,
    prefer_complete: bool = True,
    session: requests.Session | None = None,
) -> Path:
    dest_dir = output_dir if output_dir is not None else default_output_dir(path)
    transcript_dest = transcript_path_for(dest_dir)
    summary_dest = summary_path_for(dest_dir)
    if dry_run:
        if from_transcript:
            if not path.exists() or not path.is_file():
                raise MeetingSummaryError(f"File not found: {path}")
        else:
            audio_to_text.validate_input(path)
        print(f"Would transcribe {path} -> {transcript_dest}")
        print(f"Would summarize -> {summary_dest}")
        return summary_dest

    if from_transcript:
        try:
            transcript = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MeetingSummaryError(f"Could not read transcript {path}: {exc}") from exc
        if not transcript.strip():
            raise MeetingSummaryError(f"{path.name} is empty")
    else:
        transcript = transcribe_recording(
            path,
            api_key,
            language=language,
            diarize=diarize,
            format_text=format_text,
            prepare=prepare,
            multichannel=multichannel,
            merge_turns=merge_turns,
            min_turn_seconds=min_turn_seconds,
            min_turn_words=min_turn_words,
            prefer_complete=prefer_complete,
            session=session,
        )
    write_text(transcript_dest, transcript)
    markdown = request_summary(
        transcript,
        api_key,
        source_name=path.name,
        model=model,
        session=session,
    )
    written = write_text(summary_dest, markdown)
    print(written)
    if do_notify:
        audio_to_text.notify("Meeting summary", f"Wrote {written.name}")
    return written


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transcribe a meeting recording with Grok STT and write "
            "summary.md plus transcript.txt in an output folder."
        )
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="Audio/MP4 recordings, or existing transcripts with --from-transcript",
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        help="Output folder (default: <parent>/<stem>/ next to the input)",
    )
    parser.add_argument(
        "--from-transcript",
        action="store_true",
        help="Treat inputs as existing transcripts; skip STT",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Grok chat model for the summary "
            f"(default: MEETING_SUMMARY_MODEL or {DEFAULT_MODEL})"
        ),
    )
    parser.add_argument(
        "--language",
        default=audio_to_text.DEFAULT_LANGUAGE,
        help="Language code for STT formatting (default: en)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Show a macOS notification when the summary is written",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print output paths without calling the API",
    )
    parser.add_argument(
        "--no-diarize",
        action="store_true",
        help="Do not request speaker labels for STT",
    )
    parser.add_argument(
        "--multichannel",
        action="store_true",
        help=(
            "Transcribe each audio channel separately (true split-channel "
            "recordings). Skips 16 kHz mono prepare."
        ),
    )
    parser.add_argument(
        "--no-prepare",
        action="store_true",
        help="Send the original file; do not convert to 16 kHz mono WAV first",
    )
    parser.add_argument(
        "--no-format",
        action="store_true",
        help="Omit format=true (inverse text normalization) for STT",
    )
    parser.add_argument(
        "--min-turn-seconds",
        type=float,
        default=audio_to_text.DEFAULT_MIN_TURN_SECONDS,
        metavar="SEC",
        help=(
            "Merge speaker turns shorter than this many seconds "
            f"(default: {audio_to_text.DEFAULT_MIN_TURN_SECONDS})"
        ),
    )
    parser.add_argument(
        "--min-turn-words",
        type=int,
        default=audio_to_text.DEFAULT_MIN_TURN_WORDS,
        metavar="N",
        help=(
            "Merge speaker turns with this many words or fewer "
            f"(default: {audio_to_text.DEFAULT_MIN_TURN_WORDS})"
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    failures = 0
    api_key = ""
    if not args.dry_run:
        try:
            api_key = audio_to_text.get_api_key()
        except audio_to_text.AudioToTextError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            if args.notify:
                audio_to_text.notify("Meeting summary", str(exc), error=True)
            return 1

    shared_out = Path(args.out).expanduser() if args.out else None
    multiple = len(args.files) > 1
    for raw in args.files:
        path = Path(raw).expanduser()
        if shared_out is None:
            dest_dir = None
        elif multiple:
            dest_dir = shared_out / path.stem
        else:
            dest_dir = shared_out
        try:
            process_meeting(
                path,
                api_key or "",
                language=args.language,
                dry_run=args.dry_run,
                do_notify=args.notify,
                output_dir=dest_dir,
                from_transcript=args.from_transcript,
                model=args.model,
                diarize=not args.no_diarize,
                format_text=not args.no_format,
                prepare=not args.no_prepare,
                multichannel=args.multichannel,
                merge_turns=not args.no_merge_turns,
                min_turn_seconds=args.min_turn_seconds,
                min_turn_words=args.min_turn_words,
                prefer_complete=args.prefer_complete,
            )
        except audio_to_text.AudioToTextError as exc:
            failures += 1
            audio_to_text.log(f"error file={path} error={exc}")
            print(f"Error: {exc}", file=sys.stderr)
            if args.notify:
                audio_to_text.notify("Meeting summary", str(exc), error=True)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
