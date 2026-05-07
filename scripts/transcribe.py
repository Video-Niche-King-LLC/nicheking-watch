"""
Transcript fetching — captions first, Whisper fallback.

Phase 2 implementation, May 7, 2026. Three-tier strategy:

  Tier 1 — yt-dlp native captions (FREE)
    Already downloaded by scripts/download.py via --write-subs.
    Parsed as VTT into timestamped segments.
    Manual captions preferred over auto-generated when both exist.

  Tier 2 — Whisper via Groq (~$0.01-0.05/video, fast)
    POST audio to api.groq.com/openai/v1/audio/transcriptions
    Model: whisper-large-v3
    Audio: ffmpeg -ar 16000 -ac 1 -c:a libmp3lame -q:a 9 (~16kbps mp3)
    Hard 25 MB upload cap.

  Tier 3 — Whisper via OpenAI (alternative, similar cost)
    POST to api.openai.com/v1/audio/transcriptions, model whisper-1.
    Same audio prep, same 25 MB cap.

  --no-whisper mode: if no captions exist AND --no-whisper is set,
  return None and let downstream phases run frames-only.

Output written to run.library_path / "transcript.json" with shape:
    {
      "source": "captions" | "whisper-groq" | "whisper-openai" | null,
      "language": "en",
      "segments": [{"start": 0.0, "end": 3.2, "text": "..."}]
    }
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


WHISPER_UPLOAD_CAP_BYTES = 25 * 1024 * 1024  # 25 MB
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"


# ─────────────────────────────────────────────────────────────────
# Public entry — called from scripts/watch.py
# ─────────────────────────────────────────────────────────────────

def get_transcript(run, cfg: dict) -> None:
    """
    Reads run.video_path, run.library_path, run.use_whisper,
    run.whisper_provider, plus cfg['GROQ_API_KEY'] / ['OPENAI_API_KEY'].

    Sets:
      - run.transcript_path = run.library_path / "transcript.json"
        (file may contain {"source": null, ...} if no transcript was
         obtained — caller treats that as frames-only mode)
    """
    out_path = run.library_path / "transcript.json"
    run.transcript_path = out_path

    # Tier 1 — yt-dlp captions sidecar
    captions_segments = _try_load_captions(run.library_path)
    if captions_segments:
        _write_transcript(out_path, source="captions", segments=captions_segments)
        print(f"✓ Transcript loaded from native captions ({len(captions_segments)} segments)", file=sys.stderr)
        return

    # Tier 2/3 — Whisper if enabled
    if not run.use_whisper:
        print("⚠ No captions found and --no-whisper set. Running frames-only.", file=sys.stderr)
        _write_transcript(out_path, source=None, segments=[])
        return

    # Extract audio for Whisper
    audio_path = run.library_path / "audio.mp3"
    if not _extract_audio(run.video_path, audio_path):
        print("⚠ Audio extraction failed. Running frames-only.", file=sys.stderr)
        _write_transcript(out_path, source=None, segments=[])
        return

    if audio_path.stat().st_size > WHISPER_UPLOAD_CAP_BYTES:
        print(
            f"⚠ Audio file is {audio_path.stat().st_size / 1024 / 1024:.1f}MB — "
            f"exceeds Whisper's 25MB cap. Use --start/--end to focus on a smaller window. "
            "Running frames-only.",
            file=sys.stderr,
        )
        _write_transcript(out_path, source=None, segments=[])
        return

    # Try preferred provider, fall back to alt if it fails
    providers = [run.whisper_provider]
    if run.whisper_provider != "openai":
        providers.append("openai")
    if run.whisper_provider != "groq":
        providers.append("groq")

    for provider in providers:
        api_key = cfg.get(f"{provider.upper()}_API_KEY")
        if not api_key:
            continue
        result = _whisper_transcribe(audio_path, provider, api_key)
        if result is not None:
            _write_transcript(out_path, source=f"whisper-{provider}", segments=result)
            print(f"✓ Transcript via Whisper ({provider}) — {len(result)} segments", file=sys.stderr)
            return

    print("⚠ No Whisper API key configured (set GROQ_API_KEY or OPENAI_API_KEY in ~/.config/nicheking/.env). Running frames-only.", file=sys.stderr)
    _write_transcript(out_path, source=None, segments=[])


# ─────────────────────────────────────────────────────────────────
# Tier 1 — VTT captions parsing
# ─────────────────────────────────────────────────────────────────

def _try_load_captions(library_path: Path) -> Optional[list[dict]]:
    """
    yt-dlp writes captions as <basename>.<lang>.vtt next to the video.
    Prefer manual ('en.vtt') over auto-generated ('en.<...>.vtt').
    Returns segments list or None if no parseable captions exist.
    """
    vtt_files = sorted(library_path.glob("*.vtt"))
    if not vtt_files:
        return None

    # Prefer manual captions (file pattern often video.en.vtt) over auto
    # (often video.en.<source>.vtt). yt-dlp doesn't guarantee a clean
    # naming convention so fall back to alphabetical priority.
    vtt_files.sort(key=lambda p: (1 if "auto" in p.name else 0, p.name))

    for vtt in vtt_files:
        try:
            segments = _parse_vtt(vtt.read_text(encoding="utf-8"))
            if segments:
                return segments
        except (OSError, UnicodeDecodeError):
            continue
    return None


def _parse_vtt(content: str) -> list[dict]:
    """
    Minimal VTT parser. Captures cues with HH:MM:SS.mmm timestamps and
    text. Strips HTML tags + inline timing tags YouTube embeds for
    auto-captions. Coalesces multi-line text within a single cue.
    """
    segments: list[dict] = []
    current_start: Optional[float] = None
    current_end: Optional[float] = None
    current_text: list[str] = []

    cue_re = re.compile(r"^\s*(\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3})")
    tag_re = re.compile(r"<[^>]+>")

    def flush():
        nonlocal current_start, current_end, current_text
        if current_start is not None and current_text:
            text = " ".join(current_text).strip()
            text = tag_re.sub("", text)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                segments.append({"start": current_start, "end": current_end, "text": text})
        current_start = current_end = None
        current_text = []

    seen_segments: set[tuple[float, str]] = set()  # de-dupe identical lines (auto-captions repeat)
    for line in content.splitlines():
        line = line.rstrip()
        if not line:
            flush()
            continue
        m = cue_re.match(line)
        if m:
            flush()
            current_start = _hms_to_seconds(m.group(1))
            current_end = _hms_to_seconds(m.group(2))
        elif current_start is not None and not line.startswith("WEBVTT"):
            current_text.append(line)
    flush()

    # Deduplicate consecutive segments with same start+text (YouTube auto-cap quirk)
    deduped: list[dict] = []
    for seg in segments:
        key = (round(seg["start"], 1), seg["text"])
        if key in seen_segments:
            continue
        seen_segments.add(key)
        deduped.append(seg)
    return deduped


def _hms_to_seconds(s: str) -> float:
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


# ─────────────────────────────────────────────────────────────────
# Tier 2/3 — Whisper transcription
# ─────────────────────────────────────────────────────────────────

def _extract_audio(video_path: Path, audio_path: Path) -> bool:
    """Extract mono 16kHz mp3 audio — small enough to fit Whisper's cap."""
    if not _which("ffmpeg"):
        return False
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn",
        "-ar", "16000", "-ac", "1",
        "-c:a", "libmp3lame", "-q:a", "9",  # tiny but speech-intelligible
        "-y", str(audio_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return result.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 0
    except subprocess.SubprocessError:
        return False


def _whisper_transcribe(audio_path: Path, provider: str, api_key: str) -> Optional[list[dict]]:
    """
    Returns list of segments [{start, end, text}] or None on failure.
    Uses verbose_json response format so we get per-segment timestamps.
    """
    try:
        import requests
    except ImportError:
        return None

    if provider == "groq":
        url, model = GROQ_TRANSCRIBE_URL, "whisper-large-v3"
    elif provider == "openai":
        url, model = OPENAI_TRANSCRIBE_URL, "whisper-1"
    else:
        return None

    try:
        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/mpeg")}
            data = {"model": model, "response_format": "verbose_json"}
            headers = {"Authorization": f"Bearer {api_key}"}
            res = requests.post(url, files=files, data=data, headers=headers, timeout=180)
        if not res.ok:
            print(f"⚠ Whisper {provider} returned {res.status_code}: {res.text[:200]}", file=sys.stderr)
            return None
        body = res.json()
        # verbose_json shape: { "text": "...", "segments": [{ "start", "end", "text" }, ...] }
        segments = body.get("segments") or []
        return [
            {"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": (s.get("text") or "").strip()}
            for s in segments if s.get("text")
        ]
    except Exception as e:
        print(f"⚠ Whisper {provider} request failed: {e}", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _write_transcript(out_path: Path, source: Optional[str], segments: list[dict]) -> None:
    payload = {
        "source": source,
        "language": "en",  # TODO: detect non-English; out of scope for v1
        "segments": segments,
    }
    out_path.write_text(json.dumps(payload, indent=2))


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)
