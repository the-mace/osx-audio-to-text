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

That installs the `audio-to-text` command and the **Convert to Text** Finder
Quick Action (a Shortcuts item, same mechanism as Rename Invoice). It only
appears when the selected file is audio or MP4 — not on PDFs, Word docs, or
images.

If the item is missing from the menu, click an `.m4a` / `.mp4` / `.mp3` and
try again. Then:

```bash
/System/Library/CoreServices/pbs -flush
killall Finder
```

## Usage

```bash
audio-to-text ~/Desktop/clip.mp4
# writes ~/Desktop/clip.txt

audio-to-text --dry-run ~/Desktop/clip.mp4
audio-to-text --language en ~/Desktop/clip.m4a
```

Supported extensions: `.mp4`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`,
`.ogg`, `.opus`, `.mkv`. Max 500 MB per file (Grok STT limit).

Logs: `/tmp/audio_to_text.log`.

## Uninstall

```bash
rm -rf "$HOME/Library/Services/Convert to Text.workflow"
pip uninstall osx-audio-to-text
rm -f /usr/local/bin/audio-to-text
```
