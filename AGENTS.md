# Agent instructions

This is a small macOS utility. Keep the surface area small: two CLIs, one
Finder Quick Action, xAI only.

- Runtime APIs are xAI Grok STT (`POST https://api.x.ai/v1/stt`) and xAI
  chat completions (`POST https://api.x.ai/v1/chat/completions`) for
  `meeting-summary`. Do not add a second provider (no Claude/Anthropic,
  no whisper.cpp). Grok STT is the engine; keep it at least Whisper
  large-v3 quality: default to 16 kHz mono WAV prepare (same input
  whisper.cpp wants) and never auto-send `multichannel=true` on stereo
  Voice Memos — that duplicates every word. `--multichannel` is opt-in
  for true split-channel recordings only.
- Always pin the latest available STT model. Default is
  `grok-voice-transcribe-2.0`. Never omit `model` on `/v1/stt` — xAI
  defaults to `grok-voice-transcribe-1.0`. Override with `GROK_STT_MODEL`.
- `.qta` inputs are remuxed first with
  `ffmpeg -i file.qta -map 0:a:0 -c:a copy file.m4a`, then the usual
  16 kHz mono prepare. ffmpeg is required for `.qta`. Do not send the
  `.qta` file to STT.
- Default summary model is `grok-4.20-0309-non-reasoning` (same family as
  `osx-file-renamer`). Override with `--model` or `MEETING_SUMMARY_MODEL`.
- `audio-to-text` writes a sidecar `.txt` next to the recording.
  `meeting-summary` writes `<stem>/summary.md` (primary) and
  `<stem>/transcript.txt` (source). Do not make the sidecar the meeting
  product.
- API keys live in the environment or `~/.env` as `GROK_API_KEY` or
  `XAI_API_KEY`. Never commit secrets.
- Finder action name is **Convert to Text**. It runs `meeting-summary
  --notify` (folder with `summary.md` + `transcript.txt`), not the sidecar
  CLI. Input is Shortcuts `WFAVAssetContentItem` (audio + MP4). Do not
  show it for PDFs, images, or text.
- The reproducible artifact is `finder/Convert to Text.wflow`. `make install`
  signs it (`shortcuts sign --mode anyone`), imports/replaces the Shortcuts
  item, and registers it in `pbs`. Do not rely on a pre-existing local
  Shortcuts library entry.
