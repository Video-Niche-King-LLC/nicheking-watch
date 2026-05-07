---
name: nicheking-watch
description: Decode any YouTube video against your specific niche
---

# /nicheking-watch

Decode any YouTube video — your own, a competitor's, or an inspiration video — against the creator's specific niche, research patterns, voice samples, and brand. Saves the decode to the creator's Niche King library for the editorial-brief tool, script writer, and other AI editors to reference later.

## Usage

```
/nicheking-watch <url-or-path> [topic]
```

## Examples

```
/nicheking-watch https://youtu.be/dQw4w9WgXcQ
/nicheking-watch ~/Videos/competitor.mp4 hook study
/nicheking-watch https://youtu.be/<long-video> --start 5:00 --end 25:00
/nicheking-watch <url> --max-frames 120
/nicheking-watch <url> --no-niche
```

## Flags

- `--start <mm:ss>` — start of focus range
- `--end <mm:ss>` — end of focus range
- `--max-frames <int>` — frame budget (default 80, max 200)
- `--resolution <int>` — frame resolution (default 768)
- `--scene-threshold <float>` — ffmpeg scene detection threshold (default 0.4, lower = more cuts)
- `--max-gap <int>` — coverage-floor frame interval in seconds (default 45)
- `--whisper <groq|openai>` — Whisper provider (default `groq`)
- `--no-whisper` — skip Whisper fallback if no captions
- `--no-niche` — skip Niche King API context (generic decode mode)
- `--out-dir <path>` — override library location (default `~/nicheking-watch/library/`)

## What happens when you run this

1. Skill validates the URL or file path
2. Checks the local cache — if you've decoded this video before with the same focus range, the download/transcribe is reused
3. Downloads the video (yt-dlp) and pulls native captions if available
4. Extracts scene-aware frames (ffmpeg), with editing-aware density: more frames in the first 30s where hook delivery happens
5. Transcribes if no captions exist (Whisper via Groq or OpenAI — only kicks in when needed)
6. Fetches your niche profile + research patterns + voice samples + brand from Niche King via API key
7. Claude reads every frame as an image, reads the full transcript, and writes a structured editing-pattern decode adapted to your niche
8. Saves the decode to your Niche King library (visible in History → Decoded Videos)
9. Presents the decode with the niche match score and primary recommendation as the headline

## Required setup

Add your Niche King API key to `~/.config/nicheking/.env`:

```bash
mkdir -p ~/.config/nicheking
cat > ~/.config/nicheking/.env <<EOF
NICHEKING_API_KEY=sk-nk-your-key-here
EOF
chmod 600 ~/.config/nicheking/.env
```

Generate a key at https://app.nicheking.video/integrations.

Optional Whisper keys (only used when a video has no caption track):

```bash
cat >> ~/.config/nicheking/.env <<EOF
GROQ_API_KEY=gsk_...
OPENAI_API_KEY=sk-...
EOF
```
