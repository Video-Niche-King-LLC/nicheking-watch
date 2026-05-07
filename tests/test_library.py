"""
Tests for scripts/library.py — decode write/load + notes.md rendering
+ list_decodes scan.

These are pure file I/O tests with no external binary dependencies,
so they run anywhere pytest does.
"""

import json
from pathlib import Path

from scripts.library import list_decodes, load_decode, write_decode_outputs


# ─────────────────────────────────────────────────────────────────
# write_decode_outputs / load_decode round-trip
# ─────────────────────────────────────────────────────────────────

class TestWriteDecodeOutputs:
    def test_writes_decode_json_and_notes_md(self, tmp_path: Path):
        decode = {"overview": "Test overview", "hook": {"arrival_seconds": 3.2, "pattern": "story_open"}}
        decode_path, notes_path = write_decode_outputs(tmp_path, decode)
        assert decode_path == tmp_path / "decode.json"
        assert notes_path == tmp_path / "notes.md"
        assert decode_path.exists()
        assert notes_path.exists()

    def test_decode_json_round_trips(self, tmp_path: Path):
        original = {
            "overview": "Round-trip test",
            "hook": {"arrival_seconds": 5.0},
            "niche_match": {"score_0_to_100": 78},
        }
        write_decode_outputs(tmp_path, original)
        loaded = load_decode(tmp_path)
        assert loaded is not None
        assert loaded["overview"] == "Round-trip test"
        assert loaded["niche_match"]["score_0_to_100"] == 78

    def test_stamps_decoded_at_when_missing(self, tmp_path: Path):
        decode = {"overview": "no timestamp here", "hook": {}}
        write_decode_outputs(tmp_path, decode)
        loaded = load_decode(tmp_path)
        assert "decoded_at" in loaded
        # ISO format timestamp
        assert "T" in loaded["decoded_at"]

    def test_preserves_decoded_at_when_present(self, tmp_path: Path):
        decode = {"overview": "preset timestamp", "hook": {}, "decoded_at": "2025-01-01T00:00:00Z"}
        write_decode_outputs(tmp_path, decode)
        loaded = load_decode(tmp_path)
        assert loaded["decoded_at"] == "2025-01-01T00:00:00Z"


# ─────────────────────────────────────────────────────────────────
# load_decode edge cases
# ─────────────────────────────────────────────────────────────────

class TestLoadDecode:
    def test_returns_none_when_decode_missing(self, tmp_path: Path):
        assert load_decode(tmp_path) is None

    def test_returns_none_for_corrupt_json(self, tmp_path: Path):
        (tmp_path / "decode.json").write_text("not json {")
        assert load_decode(tmp_path) is None

    def test_returns_dict_for_valid_json(self, tmp_path: Path):
        (tmp_path / "decode.json").write_text('{"overview": "valid"}')
        result = load_decode(tmp_path)
        assert result == {"overview": "valid"}


# ─────────────────────────────────────────────────────────────────
# list_decodes — library scan
# ─────────────────────────────────────────────────────────────────

class TestListDecodes:
    def test_empty_library_returns_empty_list(self, tmp_path: Path):
        assert list_decodes(tmp_path) == []

    def test_skips_dirs_without_decode_json(self, tmp_path: Path):
        (tmp_path / "incomplete-slug").mkdir()
        (tmp_path / "incomplete-slug" / "meta.json").write_text("{}")
        assert list_decodes(tmp_path) == []

    def test_returns_decodes_sorted_newest_first(self, tmp_path: Path):
        # Older decode
        old_dir = tmp_path / "2026-05-01-old-slug-aaaa"
        old_dir.mkdir()
        (old_dir / "decode.json").write_text(json.dumps({
            "decoded_at": "2026-05-01T00:00:00Z",
            "source_url": "https://youtu.be/old",
            "source_title": "Old Video",
            "niche_match": {"score_0_to_100": 50},
        }))
        # Newer decode
        new_dir = tmp_path / "2026-05-07-new-slug-bbbb"
        new_dir.mkdir()
        (new_dir / "decode.json").write_text(json.dumps({
            "decoded_at": "2026-05-07T00:00:00Z",
            "source_url": "https://youtu.be/new",
            "source_title": "New Video",
            "niche_match": {"score_0_to_100": 80},
        }))

        decodes = list_decodes(tmp_path)
        assert len(decodes) == 2
        assert decodes[0]["source_title"] == "New Video"
        assert decodes[1]["source_title"] == "Old Video"
        assert decodes[0]["score"] == 80

    def test_handles_missing_score_gracefully(self, tmp_path: Path):
        d = tmp_path / "no-score-slug"
        d.mkdir()
        (d / "decode.json").write_text(json.dumps({
            "decoded_at": "2026-05-07T00:00:00Z",
            "source_url": "x", "source_title": "x",
        }))  # no niche_match field
        decodes = list_decodes(tmp_path)
        assert decodes[0]["score"] is None


# ─────────────────────────────────────────────────────────────────
# notes.md renderer — visual sanity check
# ─────────────────────────────────────────────────────────────────

class TestNotesMarkdown:
    def test_renders_full_decode_with_all_sections(self, tmp_path: Path):
        decode = {
            "source_url": "https://youtu.be/test",
            "source_title": "Test Video",
            "source_channel": "Test Channel",
            "duration_seconds": 612,
            "decoded_at": "2026-05-07T12:00:00Z",
            "overview": "This video is about X.",
            "hook": {
                "arrival_seconds": 3.2,
                "pattern": "curiosity_gap",
                "transcript": "Most people get this wrong.",
                "draft_for_creator": "Most coaches get this wrong about niches.",
                "evidence": {"matches_research_pattern": "curiosity gap in first 5 sec"},
            },
            "pacing": {
                "first_cut_seconds": 3.2,
                "avg_cut_length_seconds": 5.4,
                "broll_cadence_seconds": "4-6",
                "pattern_interrupts_at_seconds": [8, 35, 90],
                "talking_head_to_broll_ratio": "60:40",
            },
            "niche_match": {
                "score_0_to_100": 78,
                "matched_patterns": ["pattern A"],
                "missed_patterns": ["pattern B"],
                "non_niche_moves": ["move C"],
            },
            "adapt_for_creator": {
                "primary_recommendation": "Open with curiosity",
                "tactical_steps": ["Step 1", "Step 2"],
                "draft_titles": ["Title A", "Title B"],
            },
            "risk_notes": ["Don't copy the rapid-fire B-roll"],
        }
        write_decode_outputs(tmp_path, decode)
        notes = (tmp_path / "notes.md").read_text()
        # All section headers present
        assert "# Test Video" in notes
        assert "Niche match: **78/100**" in notes
        assert "## Overview" in notes
        assert "## Hook decode" in notes
        assert "## Pacing" in notes
        assert "## Niche match breakdown" in notes
        assert "## Adapt for your next video" in notes
        assert "## Risk notes" in notes
        # Specific content
        assert "curiosity_gap" in notes
        assert "Most coaches get this wrong" in notes
        assert "60:40" in notes
        assert "Title A" in notes

    def test_renders_minimal_decode_without_crashing(self, tmp_path: Path):
        decode = {"overview": "Just the overview."}
        write_decode_outputs(tmp_path, decode)
        notes = (tmp_path / "notes.md").read_text()
        assert "## Overview" in notes
        assert "Just the overview." in notes
        # No crashes from missing fields
        assert "## Hook" not in notes  # hook section skipped when no hook data
