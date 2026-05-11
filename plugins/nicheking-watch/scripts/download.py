"""
Video download via yt-dlp + oEmbed metadata.

Phase 2 implementation, May 7, 2026. Resolves run.source (URL or local
file path), downloads to run.library_path, scrapes title/channel via
oEmbed (free, no API key), and writes meta.json for cache validation.

Implementation notes:

  - yt-dlp is invoked as a subprocess (not the Python module) so we
    can detect missing-binary errors cleanly and surface install
    instructions to the user.
  - Shorts URLs (youtube.com/shorts/X) are converted to standard watch
    URLs (youtube.com/watch?v=X) for compatibility — yt-dlp handles
    both, but standardizing simplifies the cache slug.
  - --start / --end honored via yt-dlp --download-sections so we don't
    download the whole video when only a window is needed.
  - meta.json is the single source of truth for cache hits. It must
    point to a video file that actually exists; corruption or missing
    file = cache miss.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com")
SHORTS_URL_RE = re.compile(r"^https?://(?:www\.|m\.)?youtube\.com/shorts/([\w-]{11})", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────
# Public entry — called from scripts/watch.py
# ─────────────────────────────────────────────────────────────────

def download_and_meta(run) -> None:
    """
    Resolves run.source. For URLs:
      - normalize Shorts → watch URL
      - yt-dlp pulls the video to run.library_path/video.mp4
      - oEmbed pulls title + channel
      - yt-dlp metadata pulls duration_seconds
    For local files:
      - copy or symlink into run.library_path
      - ffprobe pulls duration_seconds + title (filename fallback)

    Sets:
      - run.video_path
      - run.title, run.channel, run.duration_seconds
    Writes:
      - run.library_path / "meta.json"

    Raises RuntimeError with actionable message if anything goes wrong.
    """
    run.library_path.mkdir(parents=True, exist_ok=True)

    if _is_url(run.source):
        normalized = _normalize_url(run.source)
        run.source = normalized  # downstream code uses run.source
        _download_youtube(run, normalized)
    else:
        _ingest_local_file(run)

    if not run.video_path or not run.video_path.exists():
        raise RuntimeError(
            f"Download finished but no video file found at {run.video_path}. "
            "Check yt-dlp output above for errors."
        )

    _write_meta(run)
    print(f"✓ Video ready: {run.video_path.name} ({_format_duration(run.duration_seconds)})", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────
# URL handling
# ─────────────────────────────────────────────────────────────────

def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _normalize_url(url: str) -> str:
    """Convert Shorts URLs to standard watch URLs. Pass through other URLs."""
    m = SHORTS_URL_RE.match(url)
    if m:
        video_id = m.group(1)
        return f"https://www.youtube.com/watch?v={video_id}"
    return url


# ─────────────────────────────────────────────────────────────────
# YouTube download via yt-dlp
# ─────────────────────────────────────────────────────────────────

def _download_youtube(run, url: str) -> None:
    if not _which("yt-dlp"):
        raise RuntimeError(
            "yt-dlp is not installed. Install with: pip install yt-dlp\n"
            "  or via Homebrew: brew install yt-dlp"
        )

    output_template = str(run.library_path / "video.%(ext)s")
    cmd = [
        "yt-dlp",
        "-o", output_template,
        # Prefer mp4 for ffmpeg compatibility, downgrade gracefully
        "-f", "best[ext=mp4]/best",
        "--no-playlist",
        "--no-warnings",
        "--print-json",
        "--no-progress",
    ]
    # Honor --start / --end via download-sections
    if run.start or run.end:
        sec = f"*{run.start or '0:00'}-{run.end or 'inf'}"
        cmd.extend(["--download-sections", sec])
        # When sections are used yt-dlp re-encodes; force keyframes
        cmd.extend(["--force-keyframes-at-cuts"])
    # Caption sidecar — captures captions if available, used by transcribe.py
    cmd.extend([
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en.*,en",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
    ])
    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "yt-dlp timed out after 10 minutes. The video may be very large or unreachable. "
            "Try with --start / --end to focus on a smaller window."
        )

    if result.returncode != 0:
        # Print actual yt-dlp error so the user can see what failed
        err = (result.stderr or "").strip().splitlines()
        last_lines = "\n".join(err[-10:]) if err else "(no stderr)"
        raise RuntimeError(f"yt-dlp failed (exit {result.returncode}):\n{last_lines}")

    # Locate the downloaded file (yt-dlp might pick mkv if mp4 unavailable)
    video_files = list(run.library_path.glob("video.*"))
    video_files = [v for v in video_files if v.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
    if not video_files:
        raise RuntimeError(f"yt-dlp ran but no video file in {run.library_path}")
    run.video_path = video_files[0]

    # Parse the last JSON line from stdout (yt-dlp prints one per video)
    info: dict = {}
    for line in (result.stdout or "").splitlines()[::-1]:
        line = line.strip()
        if line.startswith("{"):
            try:
                info = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    run.title = info.get("title")
    run.channel = info.get("uploader") or info.get("channel")
    run.duration_seconds = info.get("duration")

    # Some yt-dlp builds don't fill duration in JSON when sections are used.
    # Fall back to ffprobe in that case.
    if not run.duration_seconds:
        run.duration_seconds = _ffprobe_duration(run.video_path)

    # Fallback to oEmbed if title still missing (rare).
    if not run.title:
        meta = _fetch_oembed(url)
        run.title = meta.get("title") or run.video_path.stem
        run.channel = run.channel or meta.get("author_name")


# ─────────────────────────────────────────────────────────────────
# Local file ingestion
# ─────────────────────────────────────────────────────────────────

def _ingest_local_file(run) -> None:
    src = Path(run.source).expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise RuntimeError(f"Local file not found: {src}")

    dest = run.library_path / f"video{src.suffix}"
    if not dest.exists():
        # Symlink first (fast + saves disk), fall back to copy if symlink fails
        try:
            dest.symlink_to(src)
        except OSError:
            shutil.copy2(src, dest)

    run.video_path = dest
    run.title = src.stem
    run.channel = "(local file)"
    run.duration_seconds = _ffprobe_duration(dest)


# ─────────────────────────────────────────────────────────────────
# oEmbed (public, free, no API key)
# ─────────────────────────────────────────────────────────────────

def _fetch_oembed(url: str) -> dict:
    """Fetch YouTube oEmbed metadata. Returns empty dict on failure."""
    try:
        import requests
    except ImportError:
        return {}
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
        res = requests.get(oembed_url, timeout=8)
        if res.ok:
            return res.json()
    except Exception:
        pass
    return {}


# ─────────────────────────────────────────────────────────────────
# ffprobe wrapper
# ─────────────────────────────────────────────────────────────────

def _ffprobe_duration(path: Path) -> Optional[float]:
    if not _which("ffprobe"):
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return float((result.stdout or "0").strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        pass
    return None


# ─────────────────────────────────────────────────────────────────
# Meta.json — cache validation source of truth
# ─────────────────────────────────────────────────────────────────

def _write_meta(run) -> None:
    meta = {
        "skill_version": "0.1.0",
        "source_url": run.source,
        "video_filename": run.video_path.name,
        "title": run.title,
        "channel": run.channel,
        "duration_seconds": run.duration_seconds,
        "focus_start": run.start,
        "focus_end": run.end,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "max_frames": run.max_frames,
        "scene_threshold": run.scene_threshold,
        "max_gap": run.max_gap,
    }
    (run.library_path / "meta.json").write_text(json.dumps(meta, indent=2))


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _format_duration(sec: Optional[float]) -> str:
    if not sec:
        return "?:??"
    m = int(sec) // 60
    s = int(sec) % 60
    return f"{m}:{s:02d}"
