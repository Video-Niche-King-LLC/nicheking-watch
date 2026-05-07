# nicheking-watch

> **Decode any YouTube video against your specific niche.** Point Claude at a competitor's video, your own back catalog, or an inspiration video — get back a structured editing-pattern breakdown scored against the top performers in YOUR niche, plus draft hooks and titles adapted to YOUR voice.

```
/nicheking-watch https://youtu.be/<competitor-video>
```

That's the entire workflow. Claude downloads the video, extracts scene-aware frames, pulls the timestamped transcript, fetches your niche profile + research patterns from Niche King, and writes a decode that goes straight into your library.

## What you get back

Not a generic "here's what's in this video" summary. A YouTube-creator-specific editing decode:

- **Hook decode** — when the hook arrives, what pattern type, the actual transcript, and a draft hook adapted to your niche and voice
- **Pacing decode** — first cut timestamp, average cut length, B-roll cadence, pattern-interrupt timestamps, talking-head-to-B-roll ratio
- **Structure** — chapter breaks with estimated retention per section
- **CTA placements** — exact timestamps for early / mid / end CTAs with the actual copy used
- **Visual patterns** — on-screen text style, color grade, framing default, signature moves visible in frames
- **Niche match score (0-100)** — which of THIS video's patterns match the top performers in YOUR niche's research, which are missing, and which moves DON'T translate
- **Adapt for creator** — single most important takeaway + tactical steps + 3-5 draft titles for your next video drawing from this video's hook angle
- **Risk notes** — what NOT to copy because it won't fit your niche/voice/audience

The decode saves to your Niche King library so the editorial-brief tool, script writer, and any other AI editor connected via MCP can reference it later.

## Why this exists

[Niche King](https://www.nicheking.video) is the complete YouTube strategy platform — niche → topics → research → strategy → AI integration → create → improve. We analyze 500+ videos per niche through transcripts and thumbnail vision. But transcripts can't tell you *cut cadence, B-roll usage, signature visual moves, on-screen text patterns*. Frames can.

`nicheking-watch` is the missing visual layer. It runs on YOUR machine using YOUR Claude (or Cursor / Windsurf / Codex) subscription, extracts the visual data Niche King's server-side research can't, and feeds it back into Niche King's niche pattern matching.

## Install

| Surface | Command |
|---|---|
| **Claude Code** | `/plugin marketplace add Video-Niche-King-LLC/nicheking-watch` then `/plugin install nicheking-watch@nicheking-watch` |
| **claude.ai (web)** | Download `nicheking-watch.skill` from the latest release → Settings → Capabilities → Skills → + |
| **Codex** | `git clone https://github.com/Video-Niche-King-LLC/nicheking-watch ~/.codex/skills/nicheking-watch` |

## Setup

You need a Niche King API key (free for any active subscriber, generated at https://app.nicheking.video/integrations).

```bash
mkdir -p ~/.config/nicheking
cat > ~/.config/nicheking/.env <<EOF
NICHEKING_API_KEY=sk-nk-your-key-here
EOF
chmod 600 ~/.config/nicheking/.env
```

For Whisper fallback (only used when a video has no caption track) you can optionally add a Groq or OpenAI key:

```bash
cat >> ~/.config/nicheking/.env <<EOF
GROQ_API_KEY=gsk_...     # preferred — cheaper, faster
OPENAI_API_KEY=sk-...    # alternative
EOF
```

## Usage

```bash
# Basic — decode a competitor's video
/nicheking-watch https://youtu.be/<video>

# Decode a local file
/nicheking-watch ~/Videos/competitor.mp4

# Focus on a specific range (useful for long videos)
/nicheking-watch https://youtu.be/<video> --start 5:00 --end 25:00

# Bump frame budget for tightly-edited videos
/nicheking-watch https://youtu.be/<video> --max-frames 120

# Skip Whisper (frames-only mode if no captions)
/nicheking-watch https://youtu.be/<video> --no-whisper

# Without an API key — generic decode without niche scoring
/nicheking-watch https://youtu.be/<video> --no-niche
```

Flags: `--start`, `--end`, `--max-frames`, `--resolution`, `--scene-threshold`, `--max-gap`, `--whisper groq|openai`, `--no-whisper`, `--no-niche`, `--out-dir`.

## What this costs you

- **Niche King:** $0 — the API endpoints (fetch context + save decode) are free for any active subscriber.
- **Video download + native captions:** $0 — yt-dlp + ffmpeg, both free.
- **Whisper fallback:** Only kicks in when a video has no caption track. ~$0.01-0.05 per video on Groq.
- **Claude / Codex inference:** Charged to your existing AI subscription, not Niche King. The skill runs locally on your machine.

A typical 10-minute video decode costs about $0.50-$2 in Claude inference (depends on frame count and your tier) and $0 if it has captions, plus a fraction of a cent in Whisper if it doesn't.

## Re-running the same video

The library is keyed on `slug = YYYY-MM-DD-<title>-<short-hash>` where the short hash includes source URL + focus range. Re-running the same URL with the same focus range hits the cache — no re-download, no re-transcribe, only frames + decode regenerate.

To force a fresh run, delete the `meta.json` in `~/nicheking-watch/library/<slug>/`.

## Limits

- **Best accuracy:** under 30 minutes for a single decode pass. Past that, use `--start`/`--end` to focus on the section you care about.
- **Hard frame cap:** 80 by default. Bump with `--max-frames`. Token cost grows linearly.
- **Whisper upload limit:** 25 MB (~50 minutes mono 16 kHz). Longer videos need captions.
- **Public URLs and local files only.** No private platforms.

## How it integrates with the rest of Niche King

Once a decode is saved to your library, it becomes evidence the rest of the platform can reference:

- **`nicheking_decode_video_edit`** (existing MCP tool) — when you ask Claude to write an editorial brief for your next video, it pulls saved decodes that match your topic and uses them as concrete examples
- **Script writer** — when you generate a long-form script, the AI can reference patterns from videos you've already decoded ("apply the cold-open pattern from the MrBeast video you decoded last week")
- **History page** — every decode shows up under tool type "nicheking-watch" so you can revisit any video's analysis without re-running

## Develop

```bash
git clone https://github.com/Video-Niche-King-LLC/nicheking-watch
cd nicheking-watch
python3 -m pytest                              # full suite
bash scripts/build-skill.sh                    # → dist/nicheking-watch.skill (claude.ai bundle)
```

Releasing: tag `vX.Y.Z`, push the tag — CI builds and attaches `nicheking-watch.skill` to the GitHub release.

## Acknowledgments

This project's video download / frame extraction / transcription architecture is forked from [`devinilabs/claude-watch`](https://github.com/devinilabs/claude-watch) (MIT licensed). They built an excellent tool for tutorial note-taking; we adapted the architecture for YouTube-creator editing-pattern decoding. The output template, niche-aware context loading, and Niche King API integration are original.

Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), [ffmpeg](https://ffmpeg.org/), and Claude's multimodal Read tool. Whisper transcription via [Groq](https://groq.com) or [OpenAI](https://platform.openai.com).

## License

MIT — see [LICENSE](./LICENSE).
