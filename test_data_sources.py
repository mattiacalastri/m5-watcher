"""test_data_sources.py — coverage for data_sources picker / drift / voice paths.

Targets the previously-uncovered areas of data_sources.py (~27% → expected 50%+):
  - severity classifier (P0/P1/info/noise)
  - burst pickers (Session Sync, Jarvis, Notes Sync, GHL, Setter, CRM)
  - _apply_burst_policies (collapse logic + min_count threshold)
  - source_drift_audit (live/stale/dead/missing)
  - log_feed + log_feed_meta (with hermetic _LOG_SOURCES)
  - voice helpers (_voice_mask_phone, _voice_parse_iso, _voice_sparkline,
    _voice_relative_age, _voice_load_json, _voice_read_live_call)
  - voice_agents_feed smoke (with all paths missing)
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import data_sources as ds


# =============================================================================
# Severity classifier
# =============================================================================

class TestClassifySeverity(unittest.TestCase):

    def test_outreach_err_always_p0(self):
        self.assertEqual(ds._classify_severity("Outreach Err", "anything", ""), "P0")

    def test_noise_pattern_overrides(self):
        # heartbeat is in the noise regex
        self.assertEqual(ds._classify_severity("Voice", "💓 alive", ""), "noise")

    def test_default_info_when_nothing_matches(self):
        self.assertEqual(ds._classify_severity("Voice", "agent dispatched", ""), "info")


# =============================================================================
# Burst pickers (pure functions on synthetic burst lists)
# =============================================================================

class TestBurstPickers(unittest.TestCase):

    def test_picker_mrr_outstanding_finds_mrr_line(self):
        burst = [
            {"title": "first", "desc": "boring"},
            {"title": "x", "desc": "MRR 12k · Outstanding 3k"},
        ]
        self.assertIn("MRR", ds._picker_mrr_outstanding(burst))

    def test_picker_mrr_outstanding_falls_back_to_first(self):
        burst = [{"title": "first", "desc": "no signal"}]
        self.assertEqual(ds._picker_mrr_outstanding(burst), "no signal")

    def test_picker_first_non_skip(self):
        burst = [
            {"title": "skip lead Mario", "desc": "skipped"},
            {"title": "Return inviato", "desc": "real signal"},
        ]
        self.assertEqual(ds._picker_first_non_skip(burst), "real signal")

    def test_picker_first_non_skip_all_skips(self):
        burst = [{"title": "skip A", "desc": "x"}, {"title": "skip B", "desc": "y"}]
        # Falls back to first
        self.assertEqual(ds._picker_first_non_skip(burst), "x")

    def test_picker_notes_sync_summary(self):
        burst = [
            {"title": "Lettura vault"},
            {"title": "Trovate 12 note nuove"},
            {"title": "Synced 0 notes, skipped 16 unchanged"},
        ]
        self.assertIn("Synced", ds._picker_notes_sync_summary(burst))

    def test_picker_ghl_leads_summary_picks_named_lead(self):
        burst = [
            {"title": "ALERT", "desc": ""},
            {"title": "🆕 Nuovo lead", "desc": "Mario Rossi · +39…"},
        ]
        out = ds._picker_ghl_leads_summary(burst)
        self.assertIn("Mario", out)

    def test_picker_ghl_leads_summary_falls_back_when_no_named_lead(self):
        burst = [{"title": "ALERT", "desc": "1 lead nuovo trovato"}]
        out = ds._picker_ghl_leads_summary(burst)
        self.assertIn("lead nuovo", out)

    def test_picker_setter_rollup_summary_aggregates(self):
        burst = [
            {"title": "telegram OK"},
            {"title": "snapshot"},   # dropped
            {"title": "lead 12 contacted"},
        ]
        out = ds._picker_setter_rollup_summary(burst)
        self.assertIn("telegram OK", out)
        self.assertIn("lead 12 contacted", out)
        self.assertNotIn("snapshot", out)

    def test_picker_crm_pipeline_totals_aggregates(self):
        burst = [
            {"title": "Demo ×3"},
            {"title": "Discovery ×5"},
            {"title": "🔥 Interessati ×2"},
        ]
        out = ds._picker_crm_pipeline_totals(burst)
        # "10 lead totali" — sum is reported even if exact stage names vary
        self.assertIn("10 lead totali", out)
        self.assertIn("interessati", out.lower())


# =============================================================================
# Burst policy application
# =============================================================================

class TestApplyBurstPolicies(unittest.TestCase):

    def test_below_min_count_passes_through(self):
        # Session Sync min_count=2, single entry → not collapsed
        e = {"source": "Session Sync", "title": "x", "desc": "y", "time_sort": 1000.0}
        self.assertEqual(ds._apply_burst_policies([e]), [e])

    def test_collapses_within_window(self):
        ts = 100.0
        burst = [
            {"source": "Session Sync", "title": "Astra Agency", "desc": "MRR 5k Outstanding 1k",
             "time_sort": ts, "severity": "info"},
            {"source": "Session Sync", "title": "Other run", "desc": "noise",
             "time_sort": ts - 10, "severity": "info"},
            {"source": "Session Sync", "title": "Another",  "desc": "filler",
             "time_sort": ts - 20, "severity": "info"},
        ]
        out = ds._apply_burst_policies(burst)
        self.assertEqual(len(out), 1)
        self.assertIn("3 segnali", out[0]["title"])
        self.assertIn("MRR", out[0]["desc"])

    def test_outside_window_creates_separate_groups(self):
        # Session Sync window = 90s
        burst = [
            {"source": "Session Sync", "title": "a", "desc": "MRR 1", "time_sort": 200.0, "severity": "info"},
            {"source": "Session Sync", "title": "b", "desc": "MRR 2", "time_sort": 100.0, "severity": "info"},
            {"source": "Session Sync", "title": "c", "desc": "MRR 3", "time_sort": 99.0,  "severity": "info"},
        ]
        out = ds._apply_burst_policies(burst)
        # First entry is outside the 90s window from the second pair
        # Result: 1 standalone (below min_count) + 1 collapsed pair
        titles = [e["title"] for e in out]
        self.assertTrue(any("2 segnali" in t for t in titles))

    def test_unknown_source_is_passed_through(self):
        e = {"source": "NotAPolicy", "title": "x", "desc": "y", "time_sort": 1.0}
        self.assertEqual(ds._apply_burst_policies([e]), [e])

    def test_collapse_inherits_worst_severity(self):
        ts = 50.0
        burst = [
            {"source": "Notes Sync", "title": "a", "desc": "x",
             "time_sort": ts, "severity": "info"},
            {"source": "Notes Sync", "title": "b", "desc": "y",
             "time_sort": ts - 5, "severity": "P0"},
        ]
        out = ds._apply_burst_policies(burst)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["severity"], "P0")


# =============================================================================
# Source drift audit
# =============================================================================

class TestSourceDriftAudit(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_drift_")
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make(self, name: str, age_hours: float, content: str = "x") -> Path:
        p = self.dir / name
        p.write_text(content)
        if age_hours > 0:
            mtime = dt.datetime.now().timestamp() - age_hours * 3600
            import os
            os.utime(p, (mtime, mtime))
        return p

    def test_status_classification(self):
        live    = self._make("live.log",    age_hours=1.0)
        stale   = self._make("stale.log",   age_hours=12.0)
        dead    = self._make("dead.log",    age_hours=48.0)
        missing = self.dir / "ghost.log"

        sources = [
            (str(live),    "🟢", "Live"),
            (str(stale),   "🟡", "Stale"),
            (str(dead),    "🔴", "Dead"),
            (str(missing), "⚫", "Missing"),
        ]
        with patch.object(ds, "_LOG_SOURCES", sources):
            out = ds.source_drift_audit()
        self.assertEqual(out["Live"]["status"],    "live")
        self.assertEqual(out["Stale"]["status"],   "stale")
        self.assertEqual(out["Dead"]["status"],    "dead")
        self.assertEqual(out["Missing"]["status"], "missing")

    def test_zero_byte_file_treated_as_missing(self):
        empty = self.dir / "empty.log"
        empty.write_text("")
        with patch.object(ds, "_LOG_SOURCES", [(str(empty), "·", "Empty")]):
            out = ds.source_drift_audit()
        self.assertEqual(out["Empty"]["status"], "missing")


# =============================================================================
# log_feed + log_feed_meta — hermetic
# =============================================================================

class TestLogFeedHermetic(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_logfeed_")
        self.dir = Path(self._tmp.name)
        ds._log_feed_cache = []
        ds._log_feed_ts = 0.0

    def tearDown(self):
        self._tmp.cleanup()
        ds._log_feed_cache = []
        ds._log_feed_ts = 0.0

    def test_log_feed_picks_up_lines(self):
        log = self.dir / "voice.log"
        log.write_text(
            "12:00:00 jarvis: Return inviato a Mario\n"
            "12:00:05 voice agent dispatched\n"
            "12:00:10 heartbeat\n"  # noise
        )
        with patch.object(ds, "_LOG_SOURCES", [(str(log), "🎤", "Voice")]):
            entries = ds.log_feed(max_per_source=10)
        self.assertGreater(len(entries), 0)
        for e in entries:
            self.assertIn("emoji", e)
            self.assertIn("title", e)
            self.assertIn("time_sort", e)
            self.assertIn("source", e)
            self.assertEqual(e["source"], "Voice")

    def test_log_feed_meta_aggregates_correctly(self):
        live = self.dir / "live.log"
        live.write_text("12:00:00 fresh entry — system: ok\n")
        with patch.object(ds, "_LOG_SOURCES", [(str(live), "·", "Live")]):
            ds._log_feed_cache = []
            ds._log_feed_ts = 0.0
            meta = ds.log_feed_meta()
        self.assertEqual(meta["sources_total"], 1)
        for key in ("sources_live", "sources_stale", "sources_dead",
                    "sources_missing", "p0_count", "p1_count",
                    "drift_labels", "total_entries"):
            self.assertIn(key, meta)


# =============================================================================
# Log message helpers
# =============================================================================

class TestLogMessageHelpers(unittest.TestCase):

    def test_extract_ts_iso_format(self):
        ts = ds._extract_ts("[2026-05-03T17:37:46.208901] hello")
        # First 8 chars of the time portion
        self.assertTrue(ts.startswith("17:37:46") or ts.startswith("2026-05"),
                        f"unexpected: {ts!r}")

    def test_extract_ts_simple_hms(self):
        ts = ds._extract_ts("[12:34:56] something happened")
        self.assertEqual(ts, "12:34:56")

    def test_extract_ts_no_timestamp(self):
        self.assertEqual(ds._extract_ts("no timestamp here"), "")

    def test_clean_msg_strips_brackets_and_levels(self):
        out = ds._clean_msg("[2026-05-03T17:37:46] info| → autosend keystroke fired")
        self.assertNotIn("info|", out)
        self.assertNotIn("[2026-", out)
        self.assertIn("autosend keystroke", out)

    def test_make_title_splits_on_separator(self):
        title = ds._make_title("Sync run — completed 3 ops")
        self.assertEqual(title, "Sync run")

    def test_make_title_truncates_to_38(self):
        long = "x" * 200
        self.assertEqual(len(ds._make_title(long)), 38)

    def test_make_title_crm_tabular(self):
        # CRM Alert source applies a special regex
        title = ds._make_title("  2° Tentativo   15   4%", source="CRM Alert")
        self.assertIn("×15", title)


# =============================================================================
# Voice helpers
# =============================================================================

class TestVoiceMaskPhone(unittest.TestCase):

    def test_empty_returns_dash(self):
        self.assertEqual(ds._voice_mask_phone(""), "—")

    def test_short_number_masked_fully(self):
        # < 4 digits → all stars
        self.assertEqual(ds._voice_mask_phone("12"), "+**")

    def test_italian_prefix_masks_middle(self):
        out = ds._voice_mask_phone("+393331234567")
        self.assertTrue(out.endswith("4567"))
        self.assertIn("+39", out)
        self.assertIn("***", out)

    def test_other_intl_prefix(self):
        out = ds._voice_mask_phone("+15551234567")
        self.assertTrue(out.endswith("4567"))
        self.assertIn("***", out)

    def test_no_plus_prefix(self):
        out = ds._voice_mask_phone("3331234567")
        self.assertTrue(out.endswith("4567"))
        self.assertNotIn("+", out)


class TestVoiceParseIso(unittest.TestCase):

    def test_z_suffix_treated_as_utc(self):
        d = ds._voice_parse_iso("2026-05-10T12:00:00Z")
        self.assertIsNotNone(d)
        self.assertEqual(d.tzinfo, dt.timezone.utc)

    def test_naive_iso_assumed_utc(self):
        d = ds._voice_parse_iso("2026-05-10T12:00:00")
        self.assertIsNotNone(d)
        self.assertEqual(d.tzinfo, dt.timezone.utc)

    def test_invalid_returns_none(self):
        self.assertIsNone(ds._voice_parse_iso(""))
        self.assertIsNone(ds._voice_parse_iso("not a date"))
        self.assertIsNone(ds._voice_parse_iso(None))


class TestVoiceSparkline(unittest.TestCase):

    def test_empty_returns_dim_bullets(self):
        out = ds._voice_sparkline([])
        self.assertEqual(out, "·" * 7)

    def test_constant_zero_returns_lowest(self):
        self.assertEqual(ds._voice_sparkline([0, 0, 0]), "▁▁▁")

    def test_constant_nonzero_returns_mid(self):
        self.assertEqual(ds._voice_sparkline([5, 5, 5]), "▄▄▄")

    def test_ascending_progression(self):
        out = ds._voice_sparkline([0, 5, 10])
        self.assertEqual(len(out), 3)
        # First is lowest, last is highest
        self.assertEqual(out[0], "▁")
        self.assertEqual(out[-1], "█")


class TestVoiceRelativeAge(unittest.TestCase):

    def test_negative_is_now(self):
        self.assertEqual(ds._voice_relative_age(-5), "now")

    def test_seconds(self):
        self.assertEqual(ds._voice_relative_age(30), "30s ago")

    def test_minutes(self):
        self.assertEqual(ds._voice_relative_age(180), "3m ago")

    def test_hours(self):
        self.assertEqual(ds._voice_relative_age(3700), "1h ago")

    def test_days(self):
        self.assertEqual(ds._voice_relative_age(90000), "1d ago")


class TestVoiceLoadJson(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_vj_")
        self.f = Path(self._tmp.name) / "x.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_returns_empty(self):
        self.assertEqual(ds._voice_load_json(self.f), {})

    def test_invalid_returns_empty(self):
        self.f.write_text("not json")
        self.assertEqual(ds._voice_load_json(self.f), {})

    def test_valid_round_trips(self):
        self.f.write_text(json.dumps({"a": 1}))
        self.assertEqual(ds._voice_load_json(self.f), {"a": 1})


class TestVoiceReadLiveCall(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_lc_")
        self.live = Path(self._tmp.name) / "live.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_returns_watcher_offline(self):
        with patch.object(ds, "_VOICE_LIVE_CALL", self.live):
            out = ds._voice_read_live_call()
        self.assertFalse(out["is_live"])
        self.assertEqual(out["status"], "watcher_offline")

    def test_malformed_payload_marked_idle(self):
        self.live.write_text(json.dumps([1, 2, 3]))  # not a dict
        with patch.object(ds, "_VOICE_LIVE_CALL", self.live):
            out = ds._voice_read_live_call()
        self.assertFalse(out["is_live"])
        self.assertEqual(out["status"], "idle")

    def test_valid_payload_with_age_stamp(self):
        ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)).isoformat()
        self.live.write_text(json.dumps({"is_live": True, "ts": ts, "status": "active"}))
        with patch.object(ds, "_VOICE_LIVE_CALL", self.live):
            out = ds._voice_read_live_call()
        self.assertIn("_age_s", out)
        self.assertGreaterEqual(out["_age_s"], 0)
        self.assertLess(out["_age_s"], 3600)
        self.assertFalse(out["_stale"])


# =============================================================================
# voice_agents_feed — smoke test with all paths missing
# =============================================================================

class TestVoiceAgentsFeedSmoke(unittest.TestCase):

    def test_returns_dict_with_no_files(self):
        # Point all voice paths at a definitely-empty dir
        with tempfile.TemporaryDirectory(prefix="m5w_va_") as tmp:
            empty = Path(tmp)
            with patch.multiple(
                ds,
                _VOICE_TELEMETRY_DIR=empty,
                _VOICE_CALLS_DIR=empty / "calls",
                _VOICE_OUTBOX_DIR=empty / "outbox",
                _VOICE_DLQ_DIR=empty / "dlq",
                _VOICE_OPTOUT_FILE=empty / "optout.jsonl",
                _VOICE_POLICY_FILE=empty / "policy.yaml",
                _VOICE_SETTERS_FILE=empty / "setters.yaml",
                _VOICE_LIVE_CALL=empty / "live.json",
                _VOICE_FEED_CACHE={},
                _VOICE_FEED_TS=0.0,
            ):
                out = ds.voice_agents_feed()
        self.assertIsInstance(out, dict)
        # Cache reset to avoid bleeding into other tests
        ds._VOICE_FEED_CACHE = {}
        ds._VOICE_FEED_TS = 0.0


# =============================================================================
# set_log_sources
# =============================================================================

class TestSetLogSources(unittest.TestCase):

    def test_appends_unique_entries(self):
        before = list(ds._LOG_SOURCES)
        new_entry = ("/tmp/__test_only__.log", "🧪", "Test Only")
        try:
            ds.set_log_sources([new_entry, new_entry])  # duplicate ignored
            self.assertEqual(ds._LOG_SOURCES.count(new_entry), 1)
        finally:
            ds._LOG_SOURCES[:] = before


if __name__ == "__main__":
    unittest.main(verbosity=2)
