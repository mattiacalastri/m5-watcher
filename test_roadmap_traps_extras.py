"""test_roadmap_traps_extras.py — fill remaining branches in roadmap_traps.

Existing tests_roadmap.py already covers some traps. This file extends to:
  - _run subprocess wrapper (success + failure paths)
  - _run_parallel
  - _check_trap1_si_blanket (no commits, threshold met, fresh trap-check)
  - _check_trap2_build_abandon (no projects dir)
  - _parse_session_updated (every format + invalid)
  - _extract_session_mrr (frontmatter, body, sentinel -1, none)
  - _check_trap3_memory_drift (every reason branch)
  - _check_trap4_consensus (sentinel parse + threshold)
  - _parse_event_dt + _extract_event_start
  - _check_trap5_event_stuffing (over threshold + every wrapper shape)
  - detect_active_traps + render_traps_banner with synthetic state
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import roadmap_traps as rt


def _reset_cache() -> None:
    state = rt.detect_active_traps._cache_state  # type: ignore[attr-defined]
    state["ts"] = 0.0
    state["data"] = None


# =============================================================================
# Subprocess helpers
# =============================================================================

class TestRunHelpers(unittest.TestCase):

    def test_run_success(self):
        out = rt._run(["echo", "hello"])
        self.assertIn("hello", out)

    def test_run_missing_command_returns_empty(self):
        out = rt._run(["/no/such/binary"])
        self.assertEqual(out, "")

    def test_run_parallel_empty(self):
        self.assertEqual(rt._run_parallel([]), [])

    def test_run_parallel_executes_jobs(self):
        jobs = [
            (["echo", "first"],  None),
            (["echo", "second"], None),
        ]
        outputs = rt._run_parallel(jobs)
        self.assertEqual(len(outputs), 2)
        self.assertTrue(any("first" in o for o in outputs))
        self.assertTrue(any("second" in o for o in outputs))


# =============================================================================
# TRAP 1 — Sì blanket
# =============================================================================

class TestTrap1(unittest.TestCase):

    def test_no_repos_returns_none(self):
        # All repos absent → no commits → returns None
        with patch.object(rt, "TRACKED_REPOS", []):
            self.assertIsNone(rt._check_trap1_si_blanket())

    def test_threshold_not_met_returns_none(self):
        # Mock _run_parallel to return outputs with only 1 commit per repo
        with tempfile.TemporaryDirectory() as td:
            fake_repo = Path(td)
            (fake_repo / ".git").mkdir()
            with patch.object(rt, "TRACKED_REPOS", [fake_repo]), \
                 patch.object(rt, "_run_parallel", return_value=["abc1234 commit msg\n"]), \
                 patch.object(rt, "TRAP1_COMMIT_THRESHOLD", 5):
                self.assertIsNone(rt._check_trap1_si_blanket())

    def test_threshold_met_fires_trap(self):
        with tempfile.TemporaryDirectory() as td:
            fake_repo = Path(td)
            (fake_repo / ".git").mkdir()
            many = "\n".join(f"abc{i} msg" for i in range(20)) + "\n"
            with patch.object(rt, "TRACKED_REPOS", [fake_repo]), \
                 patch.object(rt, "_run_parallel", return_value=[many]), \
                 patch.object(rt, "TRAP1_COMMIT_THRESHOLD", 5), \
                 patch.object(rt, "TRAP_CHECK_FILE", Path("/no/such/file")):
                trap = rt._check_trap1_si_blanket()
        self.assertIsNotNone(trap)
        self.assertEqual(trap["trap"], "Sì blanket")
        self.assertEqual(trap["severity"], "P1")

    def test_fresh_trap_check_suppresses_trap(self):
        with tempfile.TemporaryDirectory() as td:
            fake_repo = Path(td)
            (fake_repo / ".git").mkdir()
            check_file = Path(td) / "last_trap_check.txt"
            check_file.write_text("ok")  # fresh mtime = now
            many = "\n".join(f"abc{i} msg" for i in range(20)) + "\n"
            with patch.object(rt, "TRACKED_REPOS", [fake_repo]), \
                 patch.object(rt, "_run_parallel", return_value=[many]), \
                 patch.object(rt, "TRAP1_COMMIT_THRESHOLD", 5), \
                 patch.object(rt, "TRAP_CHECK_FILE", check_file):
                self.assertIsNone(rt._check_trap1_si_blanket())


# =============================================================================
# TRAP 2 — Build abandon (early-return paths only; full git mocking is heavy)
# =============================================================================

class TestTrap2(unittest.TestCase):

    def test_missing_projects_dir_returns_none(self):
        with patch.object(rt, "PROJECTS_DIR", Path("/no/such/dir")):
            self.assertIsNone(rt._check_trap2_build_abandon())

    def test_unreadable_projects_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "projects"
            d.mkdir()
            # Empty dir — no .git children → reads it once, returns None
            with patch.object(rt, "PROJECTS_DIR", d):
                self.assertIsNone(rt._check_trap2_build_abandon())


# =============================================================================
# TRAP 3 — Memory drift helpers + integration
# =============================================================================

class TestParseSessionUpdated(unittest.TestCase):

    def test_full_iso_with_seconds(self):
        text = "---\nupdated: 2026-05-10T12:34:56\n---\nbody"
        d = rt._parse_session_updated(text)
        self.assertEqual(d, datetime(2026, 5, 10, 12, 34, 56))

    def test_iso_without_seconds(self):
        text = "---\nupdated: 2026-05-10T12:34\n---\nbody"
        d = rt._parse_session_updated(text)
        self.assertEqual(d, datetime(2026, 5, 10, 12, 34))

    def test_space_separator(self):
        text = "---\nupdated: 2026-05-10 12:34:56\n---\nbody"
        d = rt._parse_session_updated(text)
        self.assertEqual(d, datetime(2026, 5, 10, 12, 34, 56))

    def test_missing_returns_none(self):
        self.assertIsNone(rt._parse_session_updated("no frontmatter here"))

    def test_invalid_format_returns_none(self):
        self.assertIsNone(rt._parse_session_updated("---\nupdated: not-a-date\n---\n"))


class TestExtractSessionMrr(unittest.TestCase):

    def test_frontmatter_int(self):
        self.assertEqual(rt._extract_session_mrr("---\nmrr: 5400\n---\n"), 5400)

    def test_frontmatter_with_eur(self):
        self.assertEqual(rt._extract_session_mrr("---\nmrr: 5.400€\n---\n"), 5400)

    def test_body_match(self):
        self.assertEqual(rt._extract_session_mrr("status: MRR €4200 ok"), 4200)

    def test_no_mention_returns_none(self):
        self.assertIsNone(rt._extract_session_mrr("totally unrelated"))

    def test_unparseable_returns_minus_one(self):
        # MRR mentioned but no digits
        # Match requires digits, so this triggers None instead. Confirm sentinel
        # path with mrr: <garbage>
        self.assertEqual(rt._extract_session_mrr("---\nmrr: garbage\n---\n"), -1)


class TestTrap3(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_trap3_")
        self.dir = Path(self._tmp.name)
        self.session = self.dir / "session_current.md"
        self.kpi = self.dir / "KPI.md"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_session_returns_none(self):
        with patch.object(rt, "SESSION_CURRENT", self.dir / "ghost.md"):
            self.assertIsNone(rt._check_trap3_memory_drift())

    def test_stale_session_triggers_trap(self):
        old = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        self.session.write_text(f"---\nupdated: {old}\nmrr: 5000\n---\nbody")
        self.kpi.write_text("---\nmrr: 5000\n---\n")
        with patch.object(rt, "SESSION_CURRENT", self.session), \
             patch.object(rt, "KPI_FILE", self.kpi):
            trap = rt._check_trap3_memory_drift()
        self.assertIsNotNone(trap)
        self.assertIn("session_current updated", trap["evidence"])

    def test_mrr_drift_triggers_trap(self):
        recent = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.session.write_text(f"---\nupdated: {recent}\nmrr: 5000\n---\nbody")
        self.kpi.write_text("---\nmrr: 4000\n---\n")
        with patch.object(rt, "SESSION_CURRENT", self.session), \
             patch.object(rt, "KPI_FILE", self.kpi):
            trap = rt._check_trap3_memory_drift()
        self.assertIsNotNone(trap)
        self.assertIn("MRR session=5000", trap["evidence"])

    def test_unparseable_mrr_triggers_dedicated_trap(self):
        recent = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.session.write_text(f"---\nupdated: {recent}\nmrr: ???\n---\n")
        self.kpi.write_text("---\nmrr: 5000\n---\n")
        with patch.object(rt, "SESSION_CURRENT", self.session), \
             patch.object(rt, "KPI_FILE", self.kpi):
            trap = rt._check_trap3_memory_drift()
        self.assertIsNotNone(trap)
        self.assertIn("MRR_UNPARSEABLE", trap["evidence"])

    def test_no_drift_returns_none(self):
        recent = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.session.write_text(f"---\nupdated: {recent}\nmrr: 5000\n---\n")
        self.kpi.write_text("---\nmrr: 5000\n---\n")
        with patch.object(rt, "SESSION_CURRENT", self.session), \
             patch.object(rt, "KPI_FILE", self.kpi):
            self.assertIsNone(rt._check_trap3_memory_drift())


# =============================================================================
# TRAP 4 — Consensus
# =============================================================================

class TestTrap4(unittest.TestCase):

    def test_missing_sentinel_returns_none(self):
        with patch.object(rt, "MEMORY_SENTINEL", Path("/no/such")):
            self.assertIsNone(rt._check_trap4_consensus())

    def test_no_output_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            stub = Path(td) / "stub.py"
            stub.write_text("#!/usr/bin/env python3\nprint('')\n")
            stub.chmod(0o755)
            with patch.object(rt, "MEMORY_SENTINEL", stub), \
                 patch.object(rt, "_run", return_value=""):
                self.assertIsNone(rt._check_trap4_consensus())

    def test_unparseable_output_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            stub = Path(td) / "stub.py"
            stub.write_text("# stub")
            with patch.object(rt, "MEMORY_SENTINEL", stub), \
                 patch.object(rt, "_run", return_value="totally unrelated output"):
                self.assertIsNone(rt._check_trap4_consensus())


# =============================================================================
# TRAP 5 — Event stuffing
# =============================================================================

class TestParseEventDt(unittest.TestCase):

    def test_iso_with_z(self):
        d = rt._parse_event_dt("2026-05-10T12:00:00Z")
        self.assertIsNotNone(d)

    def test_naive_iso(self):
        d = rt._parse_event_dt("2026-05-10T12:00:00")
        self.assertIsNotNone(d)

    def test_date_only(self):
        d = rt._parse_event_dt("2026-05-10")
        self.assertIsNotNone(d)

    def test_invalid(self):
        self.assertIsNone(rt._parse_event_dt(""))
        self.assertIsNone(rt._parse_event_dt(None))
        self.assertIsNone(rt._parse_event_dt("xyz"))


class TestExtractEventStart(unittest.TestCase):

    def test_dict_with_datetime(self):
        self.assertEqual(rt._extract_event_start({"start": {"dateTime": "2026-05-10T12:00:00"}}),
                         "2026-05-10T12:00:00")

    def test_dict_with_date(self):
        self.assertEqual(rt._extract_event_start({"start": {"date": "2026-05-10"}}),
                         "2026-05-10")

    def test_string_start(self):
        self.assertEqual(rt._extract_event_start({"start": "2026-05-10T12:00:00"}),
                         "2026-05-10T12:00:00")

    def test_top_level_dateTime(self):
        self.assertEqual(rt._extract_event_start({"dateTime": "2026-05-10T12:00:00"}),
                         "2026-05-10T12:00:00")

    def test_top_level_start_time(self):
        self.assertEqual(rt._extract_event_start({"start_time": "2026-05-10T12:00:00"}),
                         "2026-05-10T12:00:00")

    def test_no_start_key(self):
        self.assertIsNone(rt._extract_event_start({"other": "x"}))


class TestTrap5(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_trap5_")
        self.cache = Path(self._tmp.name) / "cal.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_events(self, n_future: int, wrapper: str = "list") -> None:
        future_dt = datetime.now(timezone.utc) + timedelta(days=3)
        events = [
            {"start": {"dateTime": (future_dt + timedelta(hours=i)).isoformat()}}
            for i in range(n_future)
        ]
        if wrapper == "list":
            data = events
        elif wrapper == "events":
            data = {"events": events}
        elif wrapper == "items":
            data = {"items": events}
        elif wrapper == "calendar":
            data = {"calendar": events}
        elif wrapper == "data":
            data = {"data": events}
        else:
            data = {"unknown_wrapper": events}
        self.cache.write_text(json.dumps(data))

    def test_missing_cache_returns_none(self):
        with patch.object(rt, "CALENDAR_CACHE", self.cache.parent / "ghost.json"):
            self.assertIsNone(rt._check_trap5_event_stuffing())

    def test_invalid_json_returns_none(self):
        self.cache.write_text("{not json")
        with patch.object(rt, "CALENDAR_CACHE", self.cache):
            self.assertIsNone(rt._check_trap5_event_stuffing())

    def test_below_threshold_returns_none(self):
        self._write_events(5, wrapper="list")
        with patch.object(rt, "CALENDAR_CACHE", self.cache), \
             patch.object(rt, "TRAP5_EVENT_THRESHOLD", 40):
            self.assertIsNone(rt._check_trap5_event_stuffing())

    def test_above_threshold_fires_trap(self):
        self._write_events(50, wrapper="list")
        with patch.object(rt, "CALENDAR_CACHE", self.cache), \
             patch.object(rt, "TRAP5_EVENT_THRESHOLD", 40):
            trap = rt._check_trap5_event_stuffing()
        self.assertIsNotNone(trap)
        self.assertEqual(trap["trap"], "Event-stuffing")
        self.assertEqual(trap["severity"], "P2")

    def test_events_wrapper_dict_keys(self):
        for wrapper in ("events", "items", "calendar", "data"):
            self.cache.unlink(missing_ok=True)
            self._write_events(50, wrapper=wrapper)
            with patch.object(rt, "CALENDAR_CACHE", self.cache), \
                 patch.object(rt, "TRAP5_EVENT_THRESHOLD", 40):
                trap = rt._check_trap5_event_stuffing()
            self.assertIsNotNone(trap, f"failed for wrapper={wrapper}")

    def test_unknown_wrapper_returns_none(self):
        self._write_events(50, wrapper="unknown")
        with patch.object(rt, "CALENDAR_CACHE", self.cache), \
             patch.object(rt, "TRAP5_EVENT_THRESHOLD", 40):
            self.assertIsNone(rt._check_trap5_event_stuffing())


# =============================================================================
# detect_active_traps + render_traps_banner
# =============================================================================

class TestDetectAndRender(unittest.TestCase):

    def setUp(self):
        _reset_cache()

    def tearDown(self):
        _reset_cache()

    def test_no_traps_returns_empty_banner(self):
        with patch.object(rt, "_TRAP_FUNCS", (lambda: None,)):
            self.assertEqual(rt.render_traps_banner(), "")

    def test_detector_exception_becomes_trap(self):
        def boom():
            raise RuntimeError("detector exploded")
        with patch.object(rt, "_TRAP_FUNCS", (boom,)):
            traps = rt.detect_active_traps(force=True)
        self.assertEqual(len(traps), 1)
        self.assertEqual(traps[0]["trap"], "DETECTOR_FAILED")

    def test_render_banner_with_traps(self):
        def fake() -> dict:
            return {
                "trap": "FakeTrap", "evidence": "test evidence",
                "severity": "P1", "mitigation": "do thing",
                "cicatrice_ref": "n/a",
            }
        _reset_cache()
        with patch.object(rt, "_TRAP_FUNCS", (fake,)):
            banner = rt.render_traps_banner()
        self.assertIn("TRAP ACTIVE", banner)
        self.assertIn("FakeTrap", banner)
        self.assertIn("test evidence", banner)


if __name__ == "__main__":
    unittest.main(verbosity=2)
