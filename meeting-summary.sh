#!/usr/bin/env bash
# Transcribe a meeting recording and write summary.md + transcript.txt.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

if command -v meeting-summary >/dev/null 2>&1; then
  exec meeting-summary "$@"
fi

if [[ -x /usr/local/bin/python3 ]]; then
  PYTHON=/usr/local/bin/python3
else
  PYTHON="$(command -v python3)"
fi

export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
exec "$PYTHON" -c 'import meeting_summary, sys; raise SystemExit(meeting_summary.main(sys.argv[1:]))' "$@"
