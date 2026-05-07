# Changelog

All notable changes to nicheking-watch will be documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — Phases 1 + 2 + 3 + 4 shipped May 7, 2026

### Phase 1 — Architecture + SKILL.md
- pyproject.toml, README.md, LICENSE (MIT with attribution to devinilabs/claude-watch), .gitignore, CHANGELOG.md
- SKILL.md — the editing-pattern decode prompt template (the soul of the plugin)
- commands/nicheking-watch.md — slash command spec for Claude Code
- scripts/watch.py — argparse, slug generation, cache resolution, config loading
- 22 tests passing in tests/test_watch.py

### Phase 2 — Python plumbing (this batch)
- scripts/download.py — yt-dlp wrapper, oEmbed metadata, Shorts URL normalization, cache-friendly meta.json writer, ffprobe duration fallback, --start/--end via yt-dlp download-sections
- scripts/frames.py — three-pass ffmpeg extraction:
  - Pass A scene detection (gt(scene,T) + showinfo)
  - Pass B coverage floor (1 frame every max_gap seconds)
  - Pass C **hook density boost** — 5 frames evenly distributed in first 30s (NEW vs claude-watch). Critical for editing-pattern analysis since the hook delivery contains 80% of the signal.
  - Frames named with timestamps (frame_NNN_at_X.Xs.jpg) so Claude can reference by source-second
  - Priority dedupe within 0.5s window (hook > scene > coverage)
  - Budget clamping drops coverage frames first
- scripts/transcribe.py — three-tier transcription:
  - Tier 1: VTT captions parsed from yt-dlp sidecar (free)
  - Tier 2: Whisper via Groq (whisper-large-v3, fast + cheap)
  - Tier 3: Whisper via OpenAI (whisper-1)
  - 25MB upload cap enforced with helpful --start/--end suggestion
  - Audio extraction via ffmpeg (16kHz mono mp3)
  - Soft-fall to frames-only mode when nothing works
- scripts/library.py — write_decode_outputs (writes decode.json + notes.md), load_decode, list_decodes for future --list flag
- scripts/save.py — standalone POST script. Claude calls `python3 -m scripts.save <slug>` after writing decode.json to push it to the Niche King API.
- watch.py main() wired up — phases 1+2+3 orchestrated end-to-end. Phase 4 (Claude analyzes) and phase 5 (save script) are external to this script.
- 13 new library tests (test_library.py) + 22 existing watch tests = 35/35 passing

### Phase 3 — Niche King API endpoints
- GET /api/nicheking-watch?videoUrl=... — returns niche profile + research patterns + creator context + oEmbed video metadata + Subject-Matter Hierarchy instructions
- POST /api/nicheking-watch — saves decode JSON to tool_runs (tool_type='nicheking-watch')
- GET /api/decoded-videos — library lookup with optional sourceUrl filter

### Phase 4 — MCP integration + in-app surface
- New MCP tool: nicheking_get_decoded_videos
- New /decoded-videos viewer page (Next.js) with niche match score badges + expandable decode details + empty state
- New tile on /create page in Track category: "Decoded Videos"
- Help Chat Q&A entry per CLAUDE.md Critical Rule
- What's New entry

### Remaining for full release (Phase 5)
- Move to public github.com/Video-Niche-King-LLC/nicheking-watch repo
- Build release workflow (.github/workflows/build-release.yml) for tagged releases
- Build .skill bundle for claude.ai web installs
- Add to /integrations Native tier with setup walkthrough
- Add to /site/connections hub
- Add to dev nav dropdown
- Real-world testing on 5+ videos across different niches
