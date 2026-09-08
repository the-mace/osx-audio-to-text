# Agent instructions

This is a small macOS utility. Keep the surface area small: one CLI, one
Finder Quick Action, Grok STT only.

- Runtime API is xAI Grok STT (`POST https://api.x.ai/v1/stt`). Do not add a
  second provider.
- API keys live in the environment or `~/.env` as `GROK_API_KEY` or
  `XAI_API_KEY`. Never commit secrets.
- Finder action name is **Convert to Text**. Input is Shortcuts
  `WFAVAssetContentItem` (audio + MP4). Do not show it for PDFs, images, or text.
- The reproducible artifact is `finder/Convert to Text.wflow`. `make install`
  signs it (`shortcuts sign --mode anyone`), imports/replaces the Shortcuts
  item, and registers it in `pbs`. Do not rely on a pre-existing local
  Shortcuts library entry.
