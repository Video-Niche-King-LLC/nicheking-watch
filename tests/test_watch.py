"""
Phase 1 tests — argparse + slug generation + cache resolution.

These tests are deliberately scoped to the deterministic plumbing that
ships in Phase 1. Phase 2 will add tests for download / frames /
transcribe / API client modules.

Run with: python3 -m pytest tests/test_watch.py
"""

import json
from pathlib import Path

import pytest

from scripts.watch import (
    DEFAULT_LIBRARY_ROOT,
    DEFAULT_MAX_FRAMES,
    HARD_FRAME_CAP,
    compute_slug,
    parse_args,
    setup_library,
    slugify,
)


# ─────────────────────────────────────────────────────────────────────
# slugify
# ─────────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_collapses_whitespace_and_punctuation(self):
        assert slugify("Why I Quit My Job!! (Real Reason)") == "why-i-quit-my-job-real-reason"

    def test_strips_leading_trailing_dashes(self):
        assert slugify("---weird---") == "weird"

    def test_caps_at_60_chars(self):
        long = "a" * 200
        assert len(slugify(long)) == 60

    def test_lowercases(self):
        assert slugify("MrBeast") == "mrbeast"

    def test_numerics_preserved(self):
        assert slugify("Top 10 Mistakes In 2026") == "top-10-mistakes-in-2026"


# ─────────────────────────────────────────────────────────────────────
# compute_slug
# ─────────────────────────────────────────────────────────────────────

class TestComputeSlug:
    def test_format(self):
        slug = compute_slug("https://youtu.be/abc", None, None, "Test Video")
        # YYYY-MM-DD-test-video-XXXX
        parts = slug.split("-")
        assert len(parts[0]) == 4  # year
        assert len(parts[1]) == 2  # month
        assert len(parts[2]) == 2  # day
        assert "test" in slug
        # Last 4 chars are the hash
        assert len(slug.split("-")[-1]) == 4

    def test_same_source_same_focus_same_slug(self):
        slug_a = compute_slug("https://youtu.be/abc", "0:30", "5:00", "Test")
        slug_b = compute_slug("https://youtu.be/abc", "0:30", "5:00", "Test")
        assert slug_a == slug_b

    def test_different_focus_different_slug(self):
        # Different focus range = different decode = different slug
        slug_a = compute_slug("https://youtu.be/abc", None, None, "Test")
        slug_b = compute_slug("https://youtu.be/abc", "5:00", "10:00", "Test")
        # Same prefix but different hash suffix
        assert slug_a.split("-")[-1] != slug_b.split("-")[-1]

    def test_falls_back_to_url_segment_when_no_title(self):
        slug = compute_slug("https://youtu.be/abc-def", None, None, None)
        assert "abc-def" in slug


# ─────────────────────────────────────────────────────────────────────
# parse_args
# ─────────────────────────────────────────────────────────────────────

class TestParseArgs:
    def test_minimal(self):
        run = parse_args(["https://youtu.be/abc"])
        assert run.source == "https://youtu.be/abc"
        assert run.topic is None
        assert run.max_frames == DEFAULT_MAX_FRAMES
        assert run.use_whisper is True
        assert run.use_niche is True
        assert run.out_dir == DEFAULT_LIBRARY_ROOT

    def test_with_topic(self):
        run = parse_args(["https://youtu.be/abc", "hook study"])
        assert run.topic == "hook study"

    def test_no_whisper_flag(self):
        run = parse_args(["https://youtu.be/abc", "--no-whisper"])
        assert run.use_whisper is False

    def test_no_niche_flag(self):
        run = parse_args(["https://youtu.be/abc", "--no-niche"])
        assert run.use_niche is False

    def test_focus_range(self):
        run = parse_args(["https://youtu.be/abc", "--start", "1:30", "--end", "5:00"])
        assert run.start == "1:30"
        assert run.end == "5:00"

    def test_max_frames_clamped_to_hard_cap(self, capsys):
        run = parse_args(["https://youtu.be/abc", "--max-frames", "9999"])
        assert run.max_frames == HARD_FRAME_CAP
        captured = capsys.readouterr()
        assert "exceeds hard cap" in captured.err

    def test_whisper_provider_choices(self):
        run_groq = parse_args(["https://youtu.be/abc", "--whisper", "groq"])
        assert run_groq.whisper_provider == "groq"
        run_openai = parse_args(["https://youtu.be/abc", "--whisper", "openai"])
        assert run_openai.whisper_provider == "openai"

    def test_invalid_whisper_provider_rejected(self):
        with pytest.raises(SystemExit):
            parse_args(["https://youtu.be/abc", "--whisper", "deepgram"])


# ─────────────────────────────────────────────────────────────────────
# setup_library + cache resolution
# ─────────────────────────────────────────────────────────────────────

class TestSetupLibrary:
    def test_creates_library_dir(self, tmp_path):
        run = parse_args(["https://youtu.be/abc", "--out-dir", str(tmp_path)])
        setup_library(run)
        assert run.library_path.exists()
        assert run.library_path.is_dir()
        assert run.is_cache_hit is False

    def test_detects_cache_hit_when_meta_and_video_exist(self, tmp_path):
        run = parse_args(["https://youtu.be/abc", "--out-dir", str(tmp_path)])
        run.slug = compute_slug(run.source, run.start, run.end)
        run.library_path = tmp_path / run.slug
        run.library_path.mkdir(parents=True)

        # Simulate a previous decode having saved video + meta
        video_path = run.library_path / "video.mp4"
        video_path.write_bytes(b"fake mp4")
        meta = {
            "video_filename": "video.mp4",
            "title": "Cached Video",
            "channel": "Test Channel",
            "duration_seconds": 612,
        }
        (run.library_path / "meta.json").write_text(json.dumps(meta))

        # Re-run setup — should detect the cache hit
        setup_library(run)
        assert run.is_cache_hit is True
        assert run.title == "Cached Video"
        assert run.duration_seconds == 612

    def test_corrupt_meta_treated_as_cache_miss(self, tmp_path):
        run = parse_args(["https://youtu.be/abc", "--out-dir", str(tmp_path)])
        run.slug = compute_slug(run.source, run.start, run.end)
        run.library_path = tmp_path / run.slug
        run.library_path.mkdir(parents=True)

        # Garbage meta.json
        (run.library_path / "meta.json").write_text("not json {")

        setup_library(run)
        assert run.is_cache_hit is False

    def test_meta_pointing_at_missing_video_treated_as_cache_miss(self, tmp_path):
        run = parse_args(["https://youtu.be/abc", "--out-dir", str(tmp_path)])
        run.slug = compute_slug(run.source, run.start, run.end)
        run.library_path = tmp_path / run.slug
        run.library_path.mkdir(parents=True)

        # Meta says video.mp4 exists but it doesn't
        meta = {"video_filename": "missing.mp4"}
        (run.library_path / "meta.json").write_text(json.dumps(meta))

        setup_library(run)
        assert run.is_cache_hit is False
