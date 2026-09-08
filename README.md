# OSX Audio to Text

Finder Quick Action that transcribes an audio or MP4 file to a sidecar `.txt`
using the [xAI Grok Speech-to-Text API](https://docs.x.ai/developers/model-capabilities/audio/speech-to-text).

Right-click a file in Finder → **Quick Actions** → **Convert to Text**.
`Interview.mp4` becomes `Interview.txt` in the same folder.

The action is registered only for audio files and MP4/MKV. It does not appear
for PDFs, images, or other non-audio types.

## Privacy

The selected file is uploaded to xAI (`POST https://api.x.ai/v1/stt`) and
transcribed remotely. Nothing is stored by this tool except the local `.txt`.

## Requirements

- macOS
- Python 3.11+
- An xAI API key in `~/.env` or the environment:

  ```bash
  # same key osx-file-renamer uses
  GROK_API_KEY=...
  # or
  XAI_API_KEY=...
  ```

## Install

```bash
git clone git@github.com:the-mace/osx-audio-to-text.git
cd osx-audio-to-text
make install
```

That installs the `audio-to-text` command and recreates the **Convert to Text**
Finder Quick Action from `finder/Convert to Text.wflow` in this repo:

1. `shortcuts sign --mode anyone` (so the file is valid on any Mac)
2. Import or **Replace** the Shortcuts item
3. Register it as a Finder Quick Action (`pbs` / `NSServicesStatus`)

You do not build the shortcut by hand. A fresh clone on another Mac hits the
same path. The action only appears for audio/MP4, not PDFs, Word docs, or images.

If the item is missing from the menu after install, click an `.m4a` / `.mp4` /
`.mp3` (not a document), then:

```bash
/System/Library/CoreServices/pbs -flush
killall Finder
```

## Usage

```bash
audio-to-text ~/Desktop/Interview.m4a
# writes ~/Desktop/Interview.txt with Speaker N: labels when STT returns them

audio-to-text --dry-run ~/Desktop/clip.mp4
audio-to-text --language en ~/Desktop/clip.m4a
audio-to-text --no-diarize ~/Desktop/clip.m4a          # merged text field only
audio-to-text --json-out /tmp/clip.stt.json clip.m4a  # raw STT JSON
```

Default output groups consecutive words from Grok STT `diarize=true`:

```
Speaker 0: …

Speaker 1: …
```

Labels are the integer `speaker` values from the API. If `words` is missing or has
no speaker fields, the sidecar is the unlabeled `text` field. No second LLM pass.

Supported extensions: `.mp4`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`,
`.ogg`, `.opus`, `.mkv`. Max 500 MB per file (Grok STT limit).

Logs: `/tmp/audio_to_text.log`.

## Uninstall

```bash
# Delete "Convert to Text" in the Shortcuts app
rm -rf "$HOME/Library/Services/Convert to Text.workflow"
pip uninstall osx-audio-to-text
rm -f /usr/local/bin/audio-to-text "$HOME/.local/bin/audio-to-text"
```
