"""
Frame extraction via ffmpeg, with editing-aware density.

Phase 2 implementation, May 7, 2026. Three-pass extraction:

  Pass A — scene detection
    ffmpeg select='gt(scene,THRESHOLD)'. Default 0.4 catches most
    editing cuts without firing on micro-motion.

  Pass B — coverage floor
    1 frame every MAX_GAP seconds (default 45). Catches "talking
    head with no cuts for 60 seconds" cases pass A skips.

  Pass C — hook density boost (NEW vs claude-watch)
    The first 30 seconds is the highest-leverage information in any
    YouTube video. We sample 4-6 additional frames in this window
    regardless of scene activity. claude-watch's lecture-note approach
    falls short here for editing analysis.

Frames are written as JPEGs with timestamps in the filename so Claude
can reference them in the decode output by source-second:
    frame_000_at_0.5s.jpg
    frame_001_at_3.2s.jpg
    ...

Output is sorted chronologically and clamped to run.max_frames. When
over budget, we drop frames from the lowest-density section first
(prevents losing the hook frames or dense edit sections).

ffmpeg invoked as a subprocess for clean missing-binary error
messaging; if you want to swap to ffmpeg-python at some point that's
fine, but the subprocess approach has zero pip deps.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


HOOK_WINDOW_SECONDS = 30.0
HOOK_FRAMES_TARGET = 5  # 4-6 frames in first 30s
DEDUPE_WINDOW_SECONDS = 0.5  # frames within this window are deduped


# ─────────────────────────────────────────────────────────────────
# Public entry — called from scripts/watch.py
# ─────────────────────────────────────────────────────────────────

def extract_frames(run) -> None:
    """
    Reads run.video_path, run.scene_threshold, run.max_gap, run.max_frames,
    run.resolution, optional run.start/run.end.

    Sets:
      - run.frames_dir = run.library_path / "frames"
      - run.frames = sorted list of Path objects, chronological
    """
    if not _which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg is not installed. Install with:\n"
            "  brew install ffmpeg          # macOS\n"
            "  apt install ffmpeg           # Debian/Ubuntu\n"
            "  See https://ffmpeg.org/download.html"
        )

    run.frames_dir = run.library_path / "frames"
    run.frames_dir.mkdir(exist_ok=True)
    # Clear any previous extraction (cache regeneration scenario)
    for old in run.frames_dir.glob("frame_*.jpg"):
        old.unlink()

    duration = run.duration_seconds or 0.0
    if duration <= 0:
        # Last-resort: try ffprobe one more time
        duration = _probe_duration(run.video_path) or 0.0

    # Run all three passes, collect (timestamp, source_kind) tuples
    timestamps: list[tuple[float, str]] = []

    # Pass A — scene detection
    scene_ts = _scene_detection_timestamps(run.video_path, run.scene_threshold)
    timestamps.extend((t, "scene") for t in scene_ts)

    # Pass B — coverage floor
    cov_ts = _coverage_timestamps(duration, run.max_gap)
    timestamps.extend((t, "coverage") for t in cov_ts)

    # Pass C — hook density boost
    hook_ts = _hook_timestamps(duration, HOOK_WINDOW_SECONDS, HOOK_FRAMES_TARGET)
    timestamps.extend((t, "hook") for t in hook_ts)

    # Dedupe within DEDUPE_WINDOW_SECONDS, prefer "hook" > "scene" > "coverage"
    timestamps = _dedupe_with_priority(timestamps, DEDUPE_WINDOW_SECONDS)

    # Sort chronologically
    timestamps.sort(key=lambda x: x[0])

    # Clamp to max_frames — prefer dropping coverage frames over scene/hook
    if len(timestamps) > run.max_frames:
        timestamps = _clamp_to_budget(timestamps, run.max_frames)

    # Extract each timestamp via ffmpeg seek
    extracted: list[Path] = []
    for idx, (ts, _kind) in enumerate(timestamps):
        out_path = run.frames_dir / f"frame_{idx:03d}_at_{ts:.1f}s.jpg"
        if _extract_single_frame(run.video_path, ts, out_path, run.resolution):
            extracted.append(out_path)

    if not extracted:
        raise RuntimeError(
            "Frame extraction produced no images. Check that the video file is readable: "
            f"{run.video_path}"
        )

    run.frames = sorted(extracted, key=lambda p: _ts_from_filename(p))
    print(f"✓ Extracted {len(run.frames)} frames (scene + coverage + hook density)", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────
# Pass A — scene detection
# ─────────────────────────────────────────────────────────────────

def _scene_detection_timestamps(video_path: Path, threshold: float) -> list[float]:
    """
    Run ffmpeg with showinfo + select=gt(scene,T) to print scene-change
    timestamps to stderr. Parse out pts_time values.
    """
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("⚠ Scene detection timed out — using coverage frames only.", file=sys.stderr)
        return []

    timestamps: list[float] = []
    pts_re = re.compile(r"pts_time:([\d.]+)")
    for line in (result.stderr or "").splitlines():
        m = pts_re.search(line)
        if m:
            try:
                timestamps.append(float(m.group(1)))
            except ValueError:
                continue
    return timestamps


# ─────────────────────────────────────────────────────────────────
# Pass B — coverage floor
# ─────────────────────────────────────────────────────────────────

def _coverage_timestamps(duration: float, max_gap: int) -> list[float]:
    if duration <= 0 or max_gap <= 0:
        return []
    timestamps = []
    t = 0.5  # offset from absolute zero so we don't grab the black-frame intro
    while t < duration:
        timestamps.append(round(t, 2))
        t += max_gap
    return timestamps


# ─────────────────────────────────────────────────────────────────
# Pass C — hook density boost (the new bit vs claude-watch)
# ─────────────────────────────────────────────────────────────────

def _hook_timestamps(duration: float, window: float, target_frames: int) -> list[float]:
    """
    Pack target_frames evenly into the first `window` seconds. Skip if
    the video is shorter than the window (handle short videos gracefully).
    """
    effective_window = min(window, duration) if duration > 0 else window
    if effective_window <= 0:
        return []
    # target_frames evenly spaced inside the window
    interval = effective_window / target_frames
    return [round(interval * (i + 0.5), 2) for i in range(target_frames)]


# ─────────────────────────────────────────────────────────────────
# Dedupe + budget clamping
# ─────────────────────────────────────────────────────────────────

# Higher value = higher priority when deduping
_PRIORITY = {"hook": 3, "scene": 2, "coverage": 1}


def _dedupe_with_priority(timestamps: list[tuple[float, str]], window: float) -> list[tuple[float, str]]:
    """
    Within `window` seconds, keep the highest-priority timestamp.
    Algorithm: sort by timestamp, walk through, keep current if no
    higher-priority timestamp within window of it.
    """
    if not timestamps:
        return []
    sorted_ts = sorted(timestamps, key=lambda x: x[0])
    kept: list[tuple[float, str]] = []
    for ts, kind in sorted_ts:
        # Look back at last kept — if within window, dedupe
        if kept and (ts - kept[-1][0]) < window:
            # Keep whichever has higher priority
            if _PRIORITY[kind] > _PRIORITY[kept[-1][1]]:
                kept[-1] = (ts, kind)
            # else: skip current
            continue
        kept.append((ts, kind))
    return kept


def _clamp_to_budget(timestamps: list[tuple[float, str]], max_frames: int) -> list[tuple[float, str]]:
    """
    When over budget, drop coverage frames first (lowest priority),
    then scene frames, never hook frames.
    """
    if len(timestamps) <= max_frames:
        return timestamps
    # Sort by priority asc — lowest priority eligible to drop first
    by_priority = sorted(timestamps, key=lambda x: (_PRIORITY[x[1]], x[0]))
    keep_set = set(id(t) for t in by_priority[-max_frames:])
    return [t for t in timestamps if id(t) in keep_set]


# ─────────────────────────────────────────────────────────────────
# Single frame extraction via ffmpeg seek
# ─────────────────────────────────────────────────────────────────

def _extract_single_frame(video_path: Path, timestamp: float, out_path: Path, resolution: int) -> bool:
    """
    Seek to timestamp, write one JPEG. Resolution scales the longer
    edge to `resolution` while preserving aspect ratio. Returns True
    on success.
    """
    cmd = [
        "ffmpeg", "-ss", f"{timestamp:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", f"scale='if(gt(iw,ih),{resolution},-2)':'if(gt(iw,ih),-2,{resolution})'",
        "-q:v", "3",  # high JPEG quality
        "-y",  # overwrite
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    except subprocess.SubprocessError:
        return False


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _probe_duration(path: Path) -> Optional[float]:
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
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


_TS_RE = re.compile(r"frame_\d+_at_([\d.]+)s\.jpg$")


def _ts_from_filename(p: Path) -> float:
    m = _TS_RE.search(p.name)
    return float(m.group(1)) if m else 0.0
