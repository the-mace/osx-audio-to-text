# CLAUDE.md

Standing rules: `AGENTS.md`.

## Commands

```bash
make install       # CLI + Finder Quick Action
make install-dev   # pytest / flake8
make test
make lint
```

CLI: `audio-to-text path/to/file.mp4` writes `path/to/file.txt`.
CLI: `meeting-summary path/to/recording.m4a` writes
`<stem>/summary.md` and `<stem>/transcript.txt`.
The Finder right-click action is **Convert to Text** (`meeting-summary`).
