# OSX Audio to Text

Finder Quick Action that transcribes an audio or MP4 file with the
[xAI Grok Speech-to-Text API](https://docs.x.ai/developers/model-capabilities/audio/speech-to-text)
and writes a structured meeting summary (`summary.md`) plus the transcript
(`transcript.txt`). The `audio-to-text` CLI still writes a sidecar `.txt`
next to the file if you only want the transcript.

Right-click a file in Finder → **Quick Actions** → **Convert to Text**.
`Interview.mp4` becomes a folder `Interview/` with `summary.md` and
`transcript.txt`. The action runs `meeting-summary --notify` and posts a
macOS notification titled **Convert to Text** using the filename only
(never a full path).

```bash
meeting-summary ~/Downloads/standup.m4a
# writes ~/Downloads/standup/summary.md
#        ~/Downloads/standup/transcript.txt
```

The action is registered only for audio files and MP4/MKV. It does not appear
for PDFs, images, or other non-audio types. `.qta` is CLI-only.

## Privacy

The selected file is uploaded to xAI (`POST https://api.x.ai/v1/stt`) and
transcribed remotely. `meeting-summary` also sends the transcript to
`POST https://api.x.ai/v1/chat/completions`. Nothing is stored by this tool
except the local output files.

## Requirements

- macOS
- Python 3.11+
- ffmpeg (used to convert files to 16 kHz mono WAV before upload; **required**
  for `.qta`)
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

That installs the `audio-to-text` and `meeting-summary` commands and recreates the **Convert to Text**
Finder Quick Action (`meeting-summary --notify`) from `finder/Convert to Text.wflow` in this repo:

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
# (sidecar only; the Finder action does not use this)

meeting-summary ~/Desktop/standup.m4a
# writes ~/Desktop/standup/summary.md (key points, decisions, action items)
#        ~/Desktop/standup/transcript.txt
./meeting-summary.sh ~/Desktop/standup.m4a   # same, from a clone
meeting-summary --out ~/Desktop/out standup.m4a
meeting-summary --from-transcript notes.txt
meeting-summary --dry-run standup.m4a
meeting-summary --notify standup.m4a         # Finder uses this
audio-to-text --notify clip.m4a
```

`meeting-summary` uses the same Grok STT call as `audio-to-text`, then
`POST https://api.x.ai/v1/chat/completions` with
`grok-4.20-0309-non-reasoning` (override with `--model` or
`MEETING_SUMMARY_MODEL`). The transcript is a source file in the output
folder, not a sidecar next to the recording.

`summary.md` is GitHub-flavored markdown with a short title, date,
participants, key points, decisions, and an action-items table
(`Owner` / `Action` / `Due`). Names replace `Speaker N` when the
transcript says them. Unstated owners are `Unassigned`; unstated due
dates are `—`. Empty decisions or action items are `None recorded.`

`--notify` prints a filename-only status line instead of the output path
(so Shortcuts can show it) and posts a macOS notification titled
**Convert to Text**:

```
File Interview.mp4 has been successfully transcribed and summarized
File clip.m4a has been successfully transcribed
File notes.txt has been successfully summarized
Could not transcribe and summarize Interview.mp4: STT request failed (HTTP 500)
```

Failures use subtitle Failed. Paths in the reason are replaced with the
filename.

Grok STT is the transcriber (WER in the Whisper large-v3 band on public
indexes). The request pins `grok-voice-transcribe-2.0` (`GROK_STT_MODEL`
to override). Omitting `model` would fall back to xAI's 1.0 default.
Before upload, ffmpeg converts the file to 16 kHz mono WAV — the same
input whisper.cpp wants. Stereo Voice Memos are mixed, not split-channel;
`multichannel=true` on those duplicates every word, so it is off unless
you pass `--multichannel`. `--no-prepare` sends the original file.

```bash
audio-to-text --dry-run ~/Desktop/clip.mp4
audio-to-text --language en ~/Desktop/clip.m4a
audio-to-text --no-diarize ~/Desktop/clip.m4a          # merged text field only
audio-to-text --no-format ~/Desktop/clip.m4a           # omit ITN formatting
audio-to-text --no-merge-turns ~/Desktop/clip.m4a      # keep raw speaker flicker
audio-to-text --min-turn-seconds 1.2 clip.m4a          # flicker merge threshold
audio-to-text --min-turn-words 3 clip.m4a
audio-to-text --no-prefer-complete ~/Desktop/clip.m4a  # labels plus full text appendix
audio-to-text --json-out /tmp/clip.stt.json clip.m4a  # raw STT JSON
audio-to-text --multichannel call.wav                 # true L/R split recording
audio-to-text --no-prepare clip.m4a                   # skip 16 kHz mono convert
```

`meeting-summary` accepts the same STT flags (`--language`, `--no-diarize`,
`--no-format`, `--no-merge-turns`, `--min-turn-seconds`, `--min-turn-words`,
`--no-prefer-complete`, `--multichannel`, `--no-prepare`).

Default output groups consecutive words from Grok STT `diarize=true` and collapses
implausible micro-turns (under 1.2s and 3 words) into the adjacent speaker:

```
Speaker 0: …

Speaker 1: …
```

Labels are the integer `speaker` values from the API. If `words` is missing, has
no speaker fields, or reconstructs to much less than `text`, the sidecar uses the
unlabeled `text` field (`--prefer-complete`, the default). `audio-to-text`
does not run a second LLM pass; `meeting-summary` does (Grok chat).
`--no-format` is CLI-only; the Finder action still sends `format=true`.

Supported extensions: `.mp4`, `.m4a`, `.mp3`, `.wav`, `.aac`, `.flac`,
`.ogg`, `.opus`, `.mkv`, `.qta`. Max 500 MB per file (Grok STT limit).
`.qta` is remuxed to `.m4a` first (`ffmpeg -map 0:a:0 -c:a copy`), then
the usual 16 kHz mono prepare. ffmpeg is required for `.qta`. Output
names still use the original stem after remux/prepare. Use the CLI for
`.qta`; the Finder action does not list that type.

Logs: `/tmp/audio_to_text.log`.

## Uninstall

```bash
# Delete "Convert to Text" in the Shortcuts app
rm -rf "$HOME/Library/Services/Convert to Text.workflow"
pip uninstall osx-audio-to-text
rm -f /usr/local/bin/audio-to-text /usr/local/bin/meeting-summary \
  "$HOME/.local/bin/audio-to-text" "$HOME/.local/bin/meeting-summary"
```
