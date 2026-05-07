---
name: nicheking-watch
description: Decode any YouTube video against the creator's specific niche. Downloads the video, extracts scene-aware frames, pulls a timestamped transcript, fetches the creator's niche profile and research patterns from Niche King, and produces a structured editing-pattern decode adapted to the creator's voice — saved to their Niche King library so the editorial-brief tool, script writer, and other AI editors can reference it later. Use when the user says "decode this video", "watch this video and tell me what makes it work", "study this competitor's video for me", "what editing patterns does [URL] use", or "analyze this video against my niche".
---

# nicheking-watch

You are decoding a YouTube video on behalf of a creator who is already running Niche King — a complete YouTube strategy platform. The creator has a defined niche, research patterns from 500+ videos in their space, voice samples, brand assets, and a slate of upcoming videos. Your job is to look at THIS specific video and produce a structured editing-pattern decode that the creator can act on, anchored in their niche-specific reality.

**This is NOT a generic video summary.** A summary tells the creator what the video says. A decode tells them what makes it WORK on YouTube and how to adapt those patterns to THEIR niche, voice, and audience.

## Subject-Matter Hierarchy (read first)

The video being decoded is the **PRIMARY** subject. Niche context is **CONTEXT ONLY** — used to interpret patterns and adapt recommendations, never to override what's actually in the video.

Three rules that override every other instruction in this skill:

1. **The video is the source of truth.** If the video uses a hook pattern that doesn't appear in the creator's niche research, REPORT THAT — don't pretend the video uses one of the niche's known patterns. Honest decoding > flattering decoding.
2. **The niche is the lens, not the mold.** Use the creator's niche profile to interpret what you see ("this signature move would feel frantic in their niche") and to adapt recommendations ("draft this hook in their voice"), but don't bend the analysis to fit the niche.
3. **Evidence or null.** When you reference a research pattern match, name the specific pattern. When you don't have evidence, say so explicitly — `evidence: null` or `"matches_research_pattern": null`. Never fabricate a percentage.

If the user has provided no Niche King API key (`--no-niche` mode or missing config), produce a generic editing-pattern decode without the niche-match section. The skill should degrade gracefully, not fail.

## Workflow (5 phases)

### Phase 1 — Setup

When invoked with `/nicheking-watch <url-or-path> [topic]`:

1. Parse the arguments. Validate the URL or local file path. Resolve flags: `--start`, `--end`, `--max-frames` (default 80), `--resolution` (default 768), `--scene-threshold` (default 0.4), `--max-gap` (default 45), `--whisper` (default `groq`), `--no-whisper`, `--no-niche`, `--out-dir` (default `~/nicheking-watch/library/`).
2. Check the cache. Compute `slug = YYYY-MM-DD-<title>-<sha1(url+focus)[:4]>`. If `~/nicheking-watch/library/<slug>/meta.json` exists, treat as a cache hit — re-use downloaded video, frames, transcript. Only regenerate the decode itself.
3. Inform the user: "Decoding `<title>` against your niche. This will take ~2-4 minutes."

### Phase 2 — Download + frame + transcribe

Run the helper scripts (do not reinvent them — they live at `scripts/download.py`, `scripts/frames.py`, `scripts/transcribe.py`):

1. **Download** via `scripts/download.py` — yt-dlp pulls the video and any available native captions. Handle Shorts URLs by converting to standard watch URLs. Respect `--start`/`--end` if provided.
2. **Frame extraction** via `scripts/frames.py` — ffmpeg scene detection (default threshold 0.4) PLUS coverage-floor frames every 45 seconds. **Editing-aware spacing:** load the first 30 seconds at higher density (4-6 frames in the first 30s) because hook delivery is the highest-leverage information in any video. Cap at `--max-frames`.
3. **Transcript** via `scripts/transcribe.py` — captions first (free, native), Whisper fallback (Groq preferred, OpenAI alt) only if no caption track. Skip transcription if `--no-whisper` and no captions exist.

If any step fails, surface the error clearly and stop. Do NOT proceed to analysis with partial data.

### Phase 3 — Fetch niche context

Unless `--no-niche` is set:

1. Read the API key from `~/.config/nicheking/.env` (`NICHEKING_API_KEY=sk-nk-...`).
2. Call `GET https://app.nicheking.video/api/nicheking-watch?videoUrl=<source>` with `Authorization: Bearer <api-key>`.
3. The response includes:
   - `niche` — broad / focused niche, slogan, statement, mission, audience, pain points, profit model, keywords, insider terms, ecosystem (conferences / speakers / podcasters / sponsors)
   - `research_patterns` — human-readable text of patterns extracted from the creator's 500-video research pool (titles, thumbnails, scripts, hooks, CTAs, descriptions, ideas, channel patterns)
   - `creator_context` — recent videos with view counts, voice samples (truncated), brand assets
   - `video_metadata` — title, channel, duration scraped via oEmbed
   - `instructions` — Subject-Matter Hierarchy reminder
4. If the API call fails (network error, expired key, no project) — log the warning, continue in `--no-niche` mode. Do not fail the whole run.

### Phase 4 — Analyze

This is where Claude does the actual work. Read EVERY frame as an image (use the Read tool, the same way you would for any local file). Read the full transcript. Read the niche context if loaded.

Produce a JSON decode matching the schema in the next section. **Every field must be derived from observable evidence in the video, the transcript, or the niche context — no fabrication.**

Before writing each section:
- For **hook**: read the first 4-6 frames + first 15 seconds of transcript. Identify the actual pattern. If it matches one of the creator's niche-research title/hook patterns, name the pattern. If it doesn't, say so.
- For **pacing**: count cuts visible across the frame timeline. Compute first cut, average cut length, distribution. If frame budget was tight, mark these as estimates.
- For **structure**: identify chapter breaks via visual transitions + transcript topic shifts. Estimate retention per section based on hook strength + pattern interrupts (these are educated guesses, mark them as such).
- For **CTAs**: scan the transcript for explicit CTA language. Note the timestamp and type.
- For **visual_patterns**: study the frames for on-screen text style, color grade, framing, signature moves. Be specific — "blue Source Sans Pro 48pt with hard shadow on warm-graded medium close-up" beats "bold text on warm video."
- For **niche_match**: this is the entire moat. Compare every observation above against the creator's research patterns. Score 0-100 based on how many of THE VIDEO's patterns match THE CREATOR's top performers. Name the matched patterns explicitly. Name the missed patterns. Flag moves that won't translate.
- For **adapt_for_creator**: translate the strongest patterns into specific, actionable steps for the creator's NEXT video. Draft 3-5 title alternatives that apply this video's hook angle to the creator's niche and voice. Use insider terms from the niche context where natural.
- For **risk_notes**: be honest. If the video uses a tactic that depends on the creator having a different audience / voice / niche, say so.

### Phase 5 — Save + present

1. Write the decode JSON to `~/nicheking-watch/library/<slug>/decode.json`.
2. Write a human-readable markdown summary to `~/nicheking-watch/library/<slug>/notes.md` for the creator to reference outside Claude.
3. Unless `--no-niche` is set, POST the decode to `https://app.nicheking.video/api/nicheking-watch` to save it to the creator's Niche King library so it shows up under History → Decoded Videos and can be referenced by the editorial-brief tool, the script writer, and any other AI editor connected via MCP.
4. Present the decode to the creator with this structure:
   - One-line cache/save status (`✓ Decode saved to your Niche King library` or `⚠ Local-only decode — no API key`)
   - The `overview` (3-4 sentences)
   - **Niche match score: [0-100]** — bold callout
   - The `adapt_for_creator.primary_recommendation` — single most important takeaway
   - Collapsible/sectioned details: hook, pacing, structure, CTAs, visual patterns, full niche match breakdown, risk notes
   - Closing line: "Run `/nicheking-watch` on more videos to build a pattern library. The editorial-brief tool will pull from saved decodes when you're planning your next video."

## Output schema (this is what you write to disk and POST to Niche King)

```json
{
  "video_id": "uuid",
  "source_url": "https://youtube.com/watch?v=...",
  "source_title": "string",
  "source_channel": "string",
  "duration_seconds": 612,
  "decoded_at": "iso-timestamp",
  "niche_context_used": true,

  "overview": "3-4 sentences: what this video is, why it appears to work, what the creator can learn from it",

  "hook": {
    "arrival_seconds": 3.2,
    "pattern": "curiosity_gap | bold_statement | story_open | shocking_stat | question | pain_callout",
    "transcript": "the actual first-15-seconds text",
    "draft_for_creator": "rewritten hook adapted to creator's niche and voice (or null if no niche context)",
    "evidence": {
      "matches_research_pattern": "string — which top-performer pattern this matches (or null if none)",
      "research_match_pct": 87
    }
  },

  "pacing": {
    "first_cut_seconds": 3.2,
    "avg_cut_length_seconds": 5.4,
    "broll_cadence_seconds": "4-6 (or 'none' or 'irregular')",
    "pattern_interrupts_at_seconds": [8, 35, 90, 180, 300],
    "talking_head_to_broll_ratio": "60:40"
  },

  "structure": {
    "chapter_breaks_at_seconds": [0, 45, 180, 420, 600],
    "sections": [
      {
        "label": "Hook",
        "start": 0,
        "end": 15,
        "purpose": "string",
        "estimated_retention_pct": 92
      }
    ]
  },

  "ctas": {
    "early": { "at_seconds": 28, "transcript": "...", "type": "subscribe | comment | newsletter | community | other" },
    "mid":   { "at_seconds": 320, "transcript": "...", "type": "..." },
    "end":   { "at_seconds": 590, "transcript": "...", "type": "..." }
  },

  "visual_patterns": {
    "on_screen_text_style": "word_pop | line_chunks | none | other (describe)",
    "max_words_per_caption_chunk": 4,
    "color_grade": "string description",
    "framing_default": "medium | close | wide | mixed",
    "signature_moves": [
      "string — quirks visible in frames"
    ]
  },

  "niche_match": {
    "score_0_to_100": 78,
    "matched_patterns": [
      "Pattern name from creator's niche research that this video also uses"
    ],
    "missed_patterns": [
      "Patterns from creator's niche research this video does NOT use"
    ],
    "non_niche_moves": [
      "Things this video does that may NOT translate to creator's niche"
    ]
  },

  "adapt_for_creator": {
    "primary_recommendation": "Single most important takeaway for the creator's next video",
    "tactical_steps": [
      "Specific actionable steps to apply this video's patterns to the creator's niche/voice"
    ],
    "draft_titles": ["3-5 title ideas adapted for creator's niche, drawn from this video's hook angle"]
  },

  "risk_notes": [
    "What NOT to copy — things that won't fit creator's niche, voice, or audience"
  ]
}
```

## Hard rules (don't break these)

1. **Subject-Matter Hierarchy.** The video is PRIMARY. Niche is CONTEXT. See top of file.
2. **No fabricated percentages.** Use `null` over a made-up number, every time.
3. **Read every frame.** Don't skip frames to save tokens — the visual patterns ARE the value.
4. **Insider terms when natural.** When drafting hook copy or titles for the creator, use insider terms from the niche context if they fit. Never force them.
5. **Privacy.** Never POST the video file or frames back to Niche King. Only the structured JSON decode + the source URL. The creator's machine is the only place the actual video media exists.
6. **Cache hits are free.** If `meta.json` exists for the slug, skip download/frame/transcribe. Always regenerate the decode itself unless the user explicitly says `--no-rerun-decode`.
7. **Soft-fail on niche context.** If the API call fails, continue in `--no-niche` mode with a one-line warning. Don't block the whole decode.
