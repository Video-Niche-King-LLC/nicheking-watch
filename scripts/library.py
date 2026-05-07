"""
Local library + manifest management.

Phase 2 implementation, May 7, 2026. The library lives at
~/nicheking-watch/library/<slug>/ with files written by each phase:

    meta.json          # written by download.py — cache validation
    video.mp4          # written by download.py
    transcript.json    # written by transcribe.py
    frames/            # written by frames.py
      frame_000_at_0.5s.jpg
      ...
    decode.json        # written by Claude (the AI session) directly
    notes.md           # human-readable summary derived from decode.json

This module owns the decode-side writers (Claude calls these AFTER
producing the analysis) plus a small list_decodes helper for a future
--list flag.

Note on division of labor:
  - Claude writes decode.json directly using its Write tool.
  - This module's write_decode_outputs() is the deterministic helper
    that derives notes.md from decode.json. Claude calls it after
    writing decode.json, OR scripts/save.py calls it before POSTing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────
# Public API — called from scripts/watch.py and scripts/save.py
# ─────────────────────────────────────────────────────────────────

def write_decode_outputs(library_path: Path, decode: dict) -> tuple[Path, Path]:
    """
    Given a Path to a library dir and a decode dict, write decode.json
    (canonical JSON) and notes.md (human-readable) into the dir.
    Returns (decode_path, notes_path).
    """
    library_path.mkdir(parents=True, exist_ok=True)
    decode_path = library_path / "decode.json"
    notes_path = library_path / "notes.md"

    # Stamp the saved-at timestamp if Claude didn't
    if "decoded_at" not in decode:
        decode["decoded_at"] = datetime.now(timezone.utc).isoformat()

    decode_path.write_text(json.dumps(decode, indent=2))
    notes_path.write_text(_render_notes_md(decode))
    return decode_path, notes_path


def load_decode(library_path: Path) -> Optional[dict]:
    """Load decode.json from a library dir, or None if missing/corrupt."""
    p = library_path / "decode.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def list_decodes(library_root: Path) -> list[dict]:
    """
    Lists all decodes in the library, sorted by decoded_at desc.
    Each entry: { slug, decoded_at, source_url, source_title, score }.
    Used by future `/nicheking-watch --list` flag.
    """
    if not library_root.exists():
        return []
    out = []
    for slug_dir in library_root.iterdir():
        if not slug_dir.is_dir():
            continue
        decode = load_decode(slug_dir)
        if not decode:
            continue
        out.append({
            "slug": slug_dir.name,
            "decoded_at": decode.get("decoded_at"),
            "source_url": decode.get("source_url"),
            "source_title": decode.get("source_title"),
            "score": (decode.get("niche_match") or {}).get("score_0_to_100"),
        })
    out.sort(key=lambda d: d.get("decoded_at") or "", reverse=True)
    return out


# ─────────────────────────────────────────────────────────────────
# notes.md renderer — what creators read outside Claude
# ─────────────────────────────────────────────────────────────────

def _render_notes_md(decode: dict) -> str:
    title = decode.get("source_title") or "Decoded video"
    url = decode.get("source_url") or ""
    channel = decode.get("source_channel") or ""
    duration = decode.get("duration_seconds")
    score = (decode.get("niche_match") or {}).get("score_0_to_100")

    lines: list[str] = []
    lines.append(f"# {title}")
    if url:
        lines.append(f"> {url}")
    meta_bits = []
    if channel:
        meta_bits.append(channel)
    if duration:
        m = int(duration) // 60
        s = int(duration) % 60
        meta_bits.append(f"{m}:{s:02d}")
    if decode.get("decoded_at"):
        meta_bits.append(f"Decoded {decode['decoded_at'][:10]}")
    if meta_bits:
        lines.append(f"> {' · '.join(meta_bits)}")
    lines.append("")

    if score is not None:
        lines.append(f"## Niche match: **{score}/100**")
        lines.append("")

    if decode.get("overview"):
        lines.append("## Overview")
        lines.append(decode["overview"])
        lines.append("")

    # Hook
    hook = decode.get("hook") or {}
    if hook:
        lines.append("## Hook decode")
        if hook.get("arrival_seconds") is not None:
            lines.append(f"- **Arrives:** {hook['arrival_seconds']}s")
        if hook.get("pattern"):
            lines.append(f"- **Pattern:** {hook['pattern']}")
        if hook.get("transcript"):
            lines.append(f"- **Transcript:** _\"{hook['transcript']}\"_")
        if hook.get("draft_for_creator"):
            lines.append(f"- **Draft for your niche:** {hook['draft_for_creator']}")
        evidence = hook.get("evidence") or {}
        if evidence.get("matches_research_pattern"):
            lines.append(f"- **Matches research pattern:** {evidence['matches_research_pattern']}")
        lines.append("")

    # Pacing
    pacing = decode.get("pacing") or {}
    if pacing:
        lines.append("## Pacing")
        if pacing.get("first_cut_seconds") is not None:
            lines.append(f"- First cut: {pacing['first_cut_seconds']}s")
        if pacing.get("avg_cut_length_seconds") is not None:
            lines.append(f"- Avg cut length: {pacing['avg_cut_length_seconds']}s")
        if pacing.get("broll_cadence_seconds"):
            lines.append(f"- B-roll cadence: {pacing['broll_cadence_seconds']}s")
        if pacing.get("pattern_interrupts_at_seconds"):
            lines.append(f"- Pattern interrupts at: {', '.join(str(t) + 's' for t in pacing['pattern_interrupts_at_seconds'])}")
        if pacing.get("talking_head_to_broll_ratio"):
            lines.append(f"- Talking head : B-roll = {pacing['talking_head_to_broll_ratio']}")
        lines.append("")

    # Niche match details
    nm = decode.get("niche_match") or {}
    if nm.get("matched_patterns") or nm.get("missed_patterns") or nm.get("non_niche_moves"):
        lines.append("## Niche match breakdown")
        if nm.get("matched_patterns"):
            lines.append("**✓ Matched:**")
            for p in nm["matched_patterns"]:
                lines.append(f"  - {p}")
        if nm.get("missed_patterns"):
            lines.append("**⚠ Missed:**")
            for p in nm["missed_patterns"]:
                lines.append(f"  - {p}")
        if nm.get("non_niche_moves"):
            lines.append("**✗ Don't copy:**")
            for p in nm["non_niche_moves"]:
                lines.append(f"  - {p}")
        lines.append("")

    # Adapt for creator
    adapt = decode.get("adapt_for_creator") or {}
    if adapt:
        lines.append("## Adapt for your next video")
        if adapt.get("primary_recommendation"):
            lines.append(f"**{adapt['primary_recommendation']}**")
            lines.append("")
        if adapt.get("tactical_steps"):
            lines.append("### Steps")
            for i, step in enumerate(adapt["tactical_steps"], start=1):
                lines.append(f"{i}. {step}")
            lines.append("")
        if adapt.get("draft_titles"):
            lines.append("### Draft titles")
            for t in adapt["draft_titles"]:
                lines.append(f"- {t}")
            lines.append("")

    # Risks
    if decode.get("risk_notes"):
        lines.append("## Risk notes — what NOT to copy")
        for r in decode["risk_notes"]:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
