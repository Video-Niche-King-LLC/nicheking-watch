"""
Niche King API client — fetch context + save decode.

Two endpoints to talk to. Both Bearer-auth with the user's API key
loaded from ~/.config/nicheking/.env. Both use python-requests with
30s timeout. Soft-fail on network/4xx errors so the skill degrades
gracefully (continues in --no-niche mode rather than blocking).

Phase 2 implementation, May 7, 2026. The function signatures match
what scripts/watch.py imports — see DecodeRun in scripts/watch.py
for the field shape this module mutates.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

import requests

DEFAULT_API_BASE = os.environ.get("NICHEKING_API_BASE", "https://app.nicheking.video")
DEFAULT_TIMEOUT_SECONDS = 30
USER_AGENT = "nicheking-watch/0.1.0"


# ─────────────────────────────────────────────────────────────────
# GET — Fetch niche context for the skill
# ─────────────────────────────────────────────────────────────────

def fetch_niche_context(run, api_key: Optional[str]) -> None:
    """
    Calls GET /api/nicheking-watch?videoUrl=<source>.

    On success, sets run.niche_context to the response dict and
    run.niche_context_loaded to True. On any failure (network, 401,
    404, 500), prints a one-line warning and leaves
    run.niche_context_loaded as False — the caller flips
    run.use_niche off and continues in generic mode.

    Never raises. The skill must be able to degrade gracefully.
    """
    if not api_key:
        print("⚠ No API key — skipping niche context fetch.", file=sys.stderr)
        return

    url = f"{DEFAULT_API_BASE}/api/nicheking-watch"
    params = {"videoUrl": run.source}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
    }

    # 1 retry on transient errors (network or 5xx). 0 retries on 4xx
    # because user config is probably wrong and retrying won't help.
    for attempt in range(2):
        try:
            res = requests.get(url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT_SECONDS)
            if res.status_code == 200:
                run.niche_context = res.json()
                run.niche_context_loaded = True
                niche_focused = run.niche_context.get("niche", {}).get("focused", "(unknown)")
                print(f"✓ Niche context loaded — niche: {niche_focused}", file=sys.stderr)
                return
            if 400 <= res.status_code < 500:
                # Try to surface the API's error message; fall back to status text
                try:
                    err_msg = res.json().get("error", res.text)
                except (ValueError, json.JSONDecodeError):
                    err_msg = res.text
                print(f"⚠ Niche context fetch failed ({res.status_code}): {err_msg}", file=sys.stderr)
                print("  Continuing in --no-niche mode.", file=sys.stderr)
                return  # 4xx — don't retry
            # 5xx — retry once
            if attempt == 0:
                time.sleep(1)
                continue
            print(f"⚠ Niche context fetch failed ({res.status_code}). Continuing in --no-niche mode.", file=sys.stderr)
            return
        except requests.exceptions.Timeout:
            if attempt == 0:
                print("⚠ Niche context fetch timed out, retrying once...", file=sys.stderr)
                time.sleep(1)
                continue
            print("⚠ Niche context fetch timed out twice. Continuing in --no-niche mode.", file=sys.stderr)
            return
        except requests.exceptions.RequestException as e:
            if attempt == 0:
                print(f"⚠ Niche context fetch network error: {e}. Retrying once...", file=sys.stderr)
                time.sleep(1)
                continue
            print(f"⚠ Niche context fetch failed: {e}. Continuing in --no-niche mode.", file=sys.stderr)
            return


# ─────────────────────────────────────────────────────────────────
# POST — Save the decode to the user's Niche King library
# ─────────────────────────────────────────────────────────────────

def save_decode_to_nk(run, api_key: Optional[str]) -> bool:
    """
    Reads run.decode_path, parses the decode JSON, and POSTs it.

    Returns True if save succeeded. On failure, warns (doesn't raise)
    and returns False — the local decode.json is still on disk so the
    creator hasn't lost the analysis.
    """
    if not api_key:
        print("⚠ No API key — skipping save to Niche King library.", file=sys.stderr)
        return False
    if not run.decode_path or not run.decode_path.exists():
        print("⚠ No decode.json found — nothing to save.", file=sys.stderr)
        return False

    try:
        decode = json.loads(run.decode_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠ Could not read decode.json: {e}. Local copy preserved.", file=sys.stderr)
        return False

    url = f"{DEFAULT_API_BASE}/api/nicheking-watch"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }
    payload = {
        "source_url": run.source,
        "slug": run.slug,
        "decode": decode,
    }

    for attempt in range(2):
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT_SECONDS)
            if res.status_code in (200, 201):
                resp = res.json()
                run.saved_to_nk = True
                lib_url = resp.get("library_url", "https://app.nicheking.video/decoded-videos")
                print(f"✓ Decode saved to Niche King library — view at {lib_url}", file=sys.stderr)
                return True
            if 400 <= res.status_code < 500:
                try:
                    err_msg = res.json().get("error", res.text)
                except (ValueError, json.JSONDecodeError):
                    err_msg = res.text
                print(f"⚠ Save failed ({res.status_code}): {err_msg}", file=sys.stderr)
                print(f"  Local copy preserved at {run.decode_path}", file=sys.stderr)
                return False
            if attempt == 0:
                time.sleep(1)
                continue
            print(f"⚠ Save failed ({res.status_code}). Local copy preserved at {run.decode_path}", file=sys.stderr)
            return False
        except requests.exceptions.Timeout:
            if attempt == 0:
                print("⚠ Save timed out, retrying once...", file=sys.stderr)
                time.sleep(1)
                continue
            print(f"⚠ Save timed out twice. Local copy preserved at {run.decode_path}", file=sys.stderr)
            return False
        except requests.exceptions.RequestException as e:
            if attempt == 0:
                print(f"⚠ Save network error: {e}. Retrying once...", file=sys.stderr)
                time.sleep(1)
                continue
            print(f"⚠ Save failed: {e}. Local copy preserved at {run.decode_path}", file=sys.stderr)
            return False

    return False
