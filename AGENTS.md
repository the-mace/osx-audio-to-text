# Agent instructions

This is a small macOS utility. Keep the surface area small: one CLI, one
Finder Quick Action, Grok STT only.

- Runtime API is xAI Grok STT (`POST https://api.x.ai/v1/stt`). Do not add a
  second provider.
- API keys live in the environment or `~/.env` as `GROK_API_KEY` or
  `XAI_API_KEY`. Never commit secrets.
- Finder action name is **Convert to Text**. It must stay limited to audio
  UTIs plus `public.mpeg-4` (MP4). Do not show it for PDFs, images, or text.
- `make install` installs the CLI and copies the workflow into
  `~/Library/Services/`.
