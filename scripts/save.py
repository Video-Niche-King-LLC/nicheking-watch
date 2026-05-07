"""
Standalone "save the decode" script.

After Claude has analyzed the video and written decode.json into the
library dir, this script POSTs it to the Niche King API so it shows
up in the creator's library at https://app.nicheking.video/decoded-videos.

Usage from inside a Claude session:
    python3 -m scripts.save <library-path-or-slug>

Library path can be either:
  - The full path: ~/nicheking-watch/library/2026-05-07-mrbeast-a3f9
  - Just the slug (relative to default library root): 2026-05-07-mrbeast-a3f9

Why this is a separate script from scripts/watch.py:
  watch.py runs phases 1-3 (download/frame/transcribe/fetch context)
  and exits, leaving Claude with all the inputs in well-known paths.
  Claude then reads frames + transcript + niche context, writes
  decode.json + notes.md, and only THEN calls save.py to push the
  decode to the API.

  Splitting them keeps each script's responsibility crisp:
    watch.py = "make me ready for analysis"
    save.py = "the analysis is done, save it"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .library import load_decode, write_decode_outputs
from .nk_api import save_decode_to_nk
from .watch import DEFAULT_API_BASE, DEFAULT_LIBRARY_ROOT, load_config


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python3 -m scripts.save <library-path-or-slug>", file=sys.stderr)
        return 1

    target = args[0]
    library_path = _resolve_library_path(target)
    if not library_path or not library_path.is_dir():
        print(f"⚠ Library directory not found: {target}", file=sys.stderr)
        return 1

    decode = load_decode(library_path)
    if not decode:
        print(
            f"⚠ No decode.json found at {library_path}.\n"
            "  Did Claude finish writing the decode? Make sure decode.json exists "
            "in this directory before calling save.py.",
            file=sys.stderr,
        )
        return 1

    # Render notes.md (idempotent — fine if Claude already wrote one)
    write_decode_outputs(library_path, decode)

    # Build a minimal pseudo-run object for nk_api.save_decode_to_nk —
    # the function only needs source, slug, decode_path, saved_to_nk.
    class _Run:
        pass
    run = _Run()
    run.source = decode.get("source_url", "")
    run.slug = library_path.name
    run.decode_path = library_path / "decode.json"
    run.saved_to_nk = False

    cfg = load_config()
    api_key = cfg.get("NICHEKING_API_KEY")
    if not api_key:
        print(
            "⚠ NICHEKING_API_KEY missing from ~/.config/nicheking/.env.\n"
            "  Decode is on disk at " + str(run.decode_path) + " but not synced to the cloud library.\n"
            "  Add an API key (https://app.nicheking.video/integrations) and rerun this command to sync.",
            file=sys.stderr,
        )
        return 0  # not a failure — local copy is preserved

    success = save_decode_to_nk(run, api_key)
    return 0 if success else 1


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _resolve_library_path(target: str) -> Path | None:
    """
    Accept either an absolute/relative path OR a bare slug. If a slug
    is passed, resolve it under the default library root.
    """
    p = Path(target).expanduser()
    if p.is_absolute() or p.exists():
        return p.resolve()
    # Treat as slug under the default library root
    candidate = (DEFAULT_LIBRARY_ROOT / target).expanduser()
    return candidate if candidate.exists() else None


if __name__ == "__main__":
    sys.exit(main())
