"""
nicheking-watch — main entry point.

Orchestrates the full decode pipeline:
  1. Parse args + validate input
  2. Resolve cache (skip download/transcribe if hit)
  3. Download video + native captions
  4. Extract scene-aware frames (editing-aware density)
  5. Transcribe (captions first, Whisper fallback)
  6. Fetch niche context from Niche King API
  7. (CLAUDE PHASE — handled by SKILL.md prompt) analyze
  8. Save decode JSON to local library
  9. POST decode to Niche King API for permanent storage

This script is NOT meant to do the analysis itself — that's Claude's
job, driven by SKILL.md. This file handles the deterministic plumbing
(download, frame, transcribe, fetch context, save) and exits, leaving
Claude with all the data it needs in well-known paths.

Phase 1 (this commit): scaffolding + arg parsing + library/cache logic.
Phase 2 (next session): wire up download.py, frames.py, transcribe.py,
nk_api.py, library.py implementations and connect them here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────
# CONFIG — defaults match the SKILL.md spec. Override via CLI flags.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_LIBRARY_ROOT = Path.home() / "nicheking-watch" / "library"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "nicheking" / ".env"
DEFAULT_API_BASE = "https://app.nicheking.video"

DEFAULT_MAX_FRAMES = 80
HARD_FRAME_CAP = 200  # protects against runaway token cost
DEFAULT_RESOLUTION = 768
DEFAULT_SCENE_THRESHOLD = 0.4
DEFAULT_MAX_GAP = 45  # seconds — coverage-floor frame interval


# ─────────────────────────────────────────────────────────────────────
# DECODE RUN — single source of truth for one decode invocation.
# All downstream phases read from / write to fields on this object.
# ─────────────────────────────────────────────────────────────────────

@dataclass
class DecodeRun:
    # Input
    source: str  # URL or local file path
    topic: Optional[str] = None
    start: Optional[str] = None  # mm:ss
    end: Optional[str] = None
    max_frames: int = DEFAULT_MAX_FRAMES
    resolution: int = DEFAULT_RESOLUTION
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD
    max_gap: int = DEFAULT_MAX_GAP
    whisper_provider: str = "groq"  # groq | openai
    use_whisper: bool = True
    use_niche: bool = True
    out_dir: Path = field(default_factory=lambda: DEFAULT_LIBRARY_ROOT)

    # Computed during phase 1 setup
    slug: str = ""
    library_path: Path = field(default_factory=Path)
    is_cache_hit: bool = False

    # Filled in by phase 2 (download / frame / transcribe)
    video_path: Optional[Path] = None
    title: Optional[str] = None
    channel: Optional[str] = None
    duration_seconds: Optional[float] = None
    frames_dir: Optional[Path] = None
    frames: list[Path] = field(default_factory=list)
    transcript_path: Optional[Path] = None

    # Filled in by phase 3 (fetch niche context)
    niche_context: Optional[dict] = None
    niche_context_loaded: bool = False

    # Filled in by phase 5 (save)
    decode_path: Optional[Path] = None  # local decode.json
    notes_path: Optional[Path] = None  # local notes.md
    saved_to_nk: bool = False


# ─────────────────────────────────────────────────────────────────────
# ARG PARSER — keep this aligned with commands/nicheking-watch.md
# ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nicheking-watch",
        description="Decode any YouTube video against your specific niche.",
    )
    p.add_argument("source", help="YouTube URL or local file path")
    p.add_argument("topic", nargs="?", default=None, help="Optional decode focus (e.g. 'hook study')")
    p.add_argument("--start", default=None, help="Start of focus range (mm:ss)")
    p.add_argument("--end", default=None, help="End of focus range (mm:ss)")
    p.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES, help=f"Frame budget (default {DEFAULT_MAX_FRAMES}, hard cap {HARD_FRAME_CAP})")
    p.add_argument("--resolution", type=int, default=DEFAULT_RESOLUTION, help=f"Frame resolution (default {DEFAULT_RESOLUTION})")
    p.add_argument("--scene-threshold", type=float, default=DEFAULT_SCENE_THRESHOLD, help=f"ffmpeg scene detection threshold (default {DEFAULT_SCENE_THRESHOLD}, lower = more cuts)")
    p.add_argument("--max-gap", type=int, default=DEFAULT_MAX_GAP, help=f"Coverage-floor frame interval seconds (default {DEFAULT_MAX_GAP})")
    p.add_argument("--whisper", choices=["groq", "openai"], default="groq", help="Whisper provider when captions are missing")
    p.add_argument("--no-whisper", action="store_true", help="Skip Whisper fallback if no captions exist")
    p.add_argument("--no-niche", action="store_true", help="Skip Niche King API context (generic decode mode)")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_LIBRARY_ROOT, help="Override library location")
    return p


def parse_args(argv: Optional[list[str]] = None) -> DecodeRun:
    args = build_parser().parse_args(argv)

    if args.max_frames > HARD_FRAME_CAP:
        print(f"⚠ --max-frames {args.max_frames} exceeds hard cap {HARD_FRAME_CAP}, clamping.", file=sys.stderr)
        args.max_frames = HARD_FRAME_CAP

    return DecodeRun(
        source=args.source,
        topic=args.topic,
        start=args.start,
        end=args.end,
        max_frames=args.max_frames,
        resolution=args.resolution,
        scene_threshold=args.scene_threshold,
        max_gap=args.max_gap,
        whisper_provider=args.whisper,
        use_whisper=not args.no_whisper,
        use_niche=not args.no_niche,
        out_dir=args.out_dir,
    )


# ─────────────────────────────────────────────────────────────────────
# PHASE 1 — Setup + cache resolution
# Slug format matches SKILL.md spec: YYYY-MM-DD-<title>-<short-hash>
# Short hash includes source URL + focus range so different focus
# windows on the same video produce separate decode files.
# ─────────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Lowercase, dash-separated, alnum + dashes only."""
    out = []
    last_dash = False
    for c in text.lower():
        if c.isalnum():
            out.append(c)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-")[:60]  # cap length


def compute_slug(source: str, focus_start: Optional[str], focus_end: Optional[str], title: Optional[str] = None) -> str:
    """
    Build the canonical slug for this decode.

    Format: YYYY-MM-DD-<title-slug>-<sha1-hash>
    Hash spans (source + focus_start + focus_end) so different focus
    windows on the same video stay separate. Date is local date.
    Title may be None during initial slug computation (we use it once
    we've fetched metadata via oEmbed in phase 2).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    h = hashlib.sha1(f"{source}|{focus_start or ''}|{focus_end or ''}".encode()).hexdigest()[:4]
    title_slug = slugify(title) if title else slugify(source.split("/")[-1] or "video")
    return f"{today}-{title_slug}-{h}"


def setup_library(run: DecodeRun) -> None:
    """
    Create the library dir for this decode and detect cache hits.
    Phase 1 sets the slug + library_path; phase 2 fills in the actual
    cached video/frames/transcript paths if the hit is real.
    """
    run.slug = compute_slug(run.source, run.start, run.end)
    run.library_path = run.out_dir / run.slug
    run.library_path.mkdir(parents=True, exist_ok=True)
    meta_path = run.library_path / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            # Validate the meta points at files that still exist
            video_path = run.library_path / meta.get("video_filename", "")
            if video_path.exists():
                run.is_cache_hit = True
                run.video_path = video_path
                run.title = meta.get("title")
                run.channel = meta.get("channel")
                run.duration_seconds = meta.get("duration_seconds")
                # Frames + transcript paths reconstructed downstream
        except (json.JSONDecodeError, OSError):
            # Corrupt meta — treat as cache miss, will overwrite
            run.is_cache_hit = False


# ─────────────────────────────────────────────────────────────────────
# CONFIG LOADING — read API keys from ~/.config/nicheking/.env
# ─────────────────────────────────────────────────────────────────────

def load_config() -> dict[str, str]:
    """
    Parse ~/.config/nicheking/.env (mode 0600). Returns dict of env vars
    found. Does NOT raise if file is missing — callers handle the
    "no API key" path gracefully.
    """
    cfg: dict[str, str] = {}
    if not DEFAULT_CONFIG_PATH.exists():
        return cfg
    for line in DEFAULT_CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


# ─────────────────────────────────────────────────────────────────────
# MAIN — phase 1 is the only phase wired up in this commit. Phases 2-5
# are stubbed with clear TODO markers pointing at the script files
# they'll live in.
# ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    """
    Orchestrate the deterministic phases of a decode run:

      Phase 1 — parse args, load config, setup library + cache slug
      Phase 2 — download + extract frames + transcribe
      Phase 3 — fetch niche context from Niche King API

    After this script exits, Claude (the AI session that invoked
    /nicheking-watch) reads the artifacts in run.library_path and
    writes decode.json + notes.md per SKILL.md spec. Claude then
    invokes scripts/save.py to POST the decode to the Niche King API.
    """
    run = parse_args(argv)
    cfg = load_config()

    if run.use_niche and not cfg.get("NICHEKING_API_KEY"):
        print(
            "⚠ No NICHEKING_API_KEY found in ~/.config/nicheking/.env — "
            "running in --no-niche mode (generic decode without niche scoring).",
            file=sys.stderr,
        )
        run.use_niche = False

    # Phase 1 — setup
    setup_library(run)
    if run.is_cache_hit:
        print(f"✓ Cache hit: {run.slug} — re-using download + transcript.", file=sys.stderr)
    else:
        print(f"→ Decoding {run.source} → {run.library_path}", file=sys.stderr)

    # Phase 2 — download / frames / transcribe
    # Imports are local so a partial install (e.g. running --help on a
    # machine without yt-dlp/ffmpeg) doesn't blow up at import time.
    if not run.is_cache_hit:
        from .download import download_and_meta
        try:
            download_and_meta(run)
        except RuntimeError as e:
            print(f"✗ Download failed: {e}", file=sys.stderr)
            return 1
    else:
        # Repopulate run fields from cached meta.json so downstream
        # phases (frames, transcribe, fetch_niche_context) work the
        # same way on cache hits as on fresh runs.
        _hydrate_from_cache(run)

    # Frames always re-run — cheap relative to download, and lets us
    # honor changed --max-frames / --scene-threshold flags on rerun
    from .frames import extract_frames
    try:
        extract_frames(run)
    except RuntimeError as e:
        print(f"✗ Frame extraction failed: {e}", file=sys.stderr)
        return 1

    # Transcript — captions first, Whisper fallback
    from .transcribe import get_transcript
    try:
        get_transcript(run, cfg)
    except Exception as e:
        # Soft-fail — frames-only mode is degraded but still useful
        print(f"⚠ Transcription failed: {e}. Continuing frames-only.", file=sys.stderr)
        run.transcript_path = None

    # Phase 3 — fetch niche context from Niche King API
    if run.use_niche:
        from .nk_api import fetch_niche_context
        fetch_niche_context(run, cfg.get("NICHEKING_API_KEY"))

    # Phase 4 — Claude analyzes (driven by SKILL.md prompt, not this script).
    # The deterministic plumbing above leaves all artifacts in well-known
    # paths inside run.library_path. The AI session reads frames + transcript
    # + niche context and writes decode.json + notes.md.
    #
    # Phase 5 — Claude calls `python3 -m scripts.save <slug>` to POST
    # the decode to the Niche King API once decode.json is written.

    # Final status — Claude parses this to know what's available
    print()
    print("=" * 62, file=sys.stderr)
    print("✓ Plumbing complete. Ready for Claude analysis.", file=sys.stderr)
    print(f"  Slug:        {run.slug}", file=sys.stderr)
    print(f"  Library:     {run.library_path}", file=sys.stderr)
    print(f"  Video:       {run.video_path.name if run.video_path else '(missing)'}", file=sys.stderr)
    print(f"  Frames:      {len(run.frames)} in {run.frames_dir}/", file=sys.stderr)
    print(f"  Transcript:  {run.transcript_path.name if run.transcript_path else '(none)'}", file=sys.stderr)
    print(f"  Niche ctx:   {'loaded' if run.niche_context_loaded else 'not loaded'}", file=sys.stderr)
    print("=" * 62, file=sys.stderr)
    print(file=sys.stderr)
    print(
        f"Next: read frames + transcript + niche context, write\n"
        f"      {run.library_path}/decode.json per SKILL.md spec,\n"
        f"      then run: python3 -m scripts.save {run.slug}",
        file=sys.stderr,
    )

    # Print the library path on stdout so Claude can capture it cleanly
    print(str(run.library_path))
    return 0


def _hydrate_from_cache(run: DecodeRun) -> None:
    """
    Repopulate run fields from cached meta.json on a cache hit.
    Sets video_path, title, channel, duration_seconds. Frames + transcript
    are derived/regenerated by their own phases.
    """
    meta_path = run.library_path / "meta.json"
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return
    if meta.get("video_filename"):
        candidate = run.library_path / meta["video_filename"]
        if candidate.exists():
            run.video_path = candidate
    run.title = run.title or meta.get("title")
    run.channel = run.channel or meta.get("channel")
    run.duration_seconds = run.duration_seconds or meta.get("duration_seconds")


if __name__ == "__main__":
    sys.exit(main())
