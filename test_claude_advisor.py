"""test_claude_advisor.py — coverage for plugins/claude_advisor non-LLM helpers.

Skips network-bound LLM calls. Focuses on:
  - load_snapshot (missing + valid)
  - _verdict_color (every verdict)
  - _age_str (seconds/minutes/hours/invalid)
  - tail (empty + populated)
  - main() argparse routing (with run_loop and cycle_once mocked)
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _import_advisor():
    """Import via the canonical filename (handles namespacing)."""
    spec = importlib.util.spec_from_file_location(
        "claude_advisor_under_test",
        ROOT / "plugins" / "claude_advisor.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestLoadSnapshot(unittest.TestCase):

    def setUp(self):
        self.adv = _import_advisor()
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_adv_")
        self.snap = Path(self._tmp.name) / "snap.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_raises(self):
        with patch.object(self.adv, "SNAPSHOT_PATH", self.snap.parent / "ghost.json"):
            with self.assertRaises(FileNotFoundError):
                self.adv.load_snapshot()

    def test_valid_file_loads(self):
        payload = {"ts": "2026-05-10T12:00:00Z", "ram_free_gb": 8}
        self.snap.write_text(json.dumps(payload))
        with patch.object(self.adv, "SNAPSHOT_PATH", self.snap):
            self.assertEqual(self.adv.load_snapshot(), payload)


class TestVerdictColor(unittest.TestCase):

    def setUp(self):
        self.adv = _import_advisor()

    def test_known_verdicts(self):
        self.assertIn("green", self.adv._verdict_color("OPTIMAL"))
        self.assertIn("yellow", self.adv._verdict_color("WATCH"))
        self.assertIn("orange", self.adv._verdict_color("ACT"))
        self.assertIn("red", self.adv._verdict_color("CRITICAL"))

    def test_unknown_falls_back_to_white(self):
        self.assertIn("white", self.adv._verdict_color("MYSTERY"))


class TestAgeStr(unittest.TestCase):

    def setUp(self):
        self.adv = _import_advisor()

    def test_seconds(self):
        ts = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat()
        self.assertIn("s fa", self.adv._age_str(ts))

    def test_minutes(self):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.assertIn("m fa", self.adv._age_str(ts))

    def test_hours(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.assertIn("h fa", self.adv._age_str(ts))

    def test_invalid_returns_question_mark(self):
        self.assertEqual(self.adv._age_str("not-a-timestamp"), "?")
        self.assertEqual(self.adv._age_str(""), "?")


class TestTail(unittest.TestCase):

    def setUp(self):
        self.adv = _import_advisor()
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_tail_")
        self.out = Path(self._tmp.name) / "advisor.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_file_prints_placeholder(self):
        with patch.object(self.adv, "OUT_PATH", self.out), \
             patch("sys.stdout", new_callable=StringIO) as stdout:
            self.adv.tail()
        self.assertIn("empty", stdout.getvalue())

    def test_populated_file_prints_entries(self):
        entries = [
            {"ts": "2026-05-10T12:00:00+00:00", "verdict": "OPTIMAL",
             "headline": "all good", "action": "none"},
            {"ts": "2026-05-10T12:01:00+00:00", "verdict": "ACT",
             "headline": "swap > 90", "action": "killall ChatGPT"},
        ]
        self.out.write_text("\n".join(json.dumps(e) for e in entries))
        with patch.object(self.adv, "OUT_PATH", self.out), \
             patch("sys.stdout", new_callable=StringIO) as stdout:
            self.adv.tail(n=5)
        out = stdout.getvalue()
        self.assertIn("OPTIMAL", out)
        self.assertIn("all good", out)
        self.assertIn("→ action: killall", out)


class TestMainEntryPoint(unittest.TestCase):

    def setUp(self):
        self.adv = _import_advisor()

    def test_tail_routes_to_tail(self):
        with patch.object(self.adv, "tail") as fake, \
             patch.object(sys, "argv", ["claude_advisor", "--tail", "3"]):
            rc = self.adv.main()
        self.assertEqual(rc, 0)
        fake.assert_called_once_with(3)

    def test_loop_routes_to_run_loop(self):
        with patch.object(self.adv, "run_loop") as fake, \
             patch.object(sys, "argv", ["claude_advisor", "--loop", "--interval", "10"]):
            rc = self.adv.main()
        self.assertEqual(rc, 0)
        fake.assert_called_once()

    def test_default_runs_cycle_once(self):
        with patch.object(self.adv, "cycle_once") as fake, \
             patch.object(sys, "argv", ["claude_advisor"]):
            rc = self.adv.main()
        self.assertEqual(rc, 0)
        fake.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
