"""test_bench.py — coverage for bench/run_bench CLI.

Covers:
  - _evaluate (PASS / WARN / FAIL branches, every threshold)
  - _markdown_report (formatting + status indicators)
  - main() with mocked _run_pilot (success + pilot failure paths)
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import run_bench as rb


def _make_snapshot(
    *,
    p95: float = 20.0,
    p50: float = 10.0,
    p99: float = 25.0,
    n: int = 100,
    cache_hits: int = 90,
    cache_misses: int = 10,
    rss_mb: float = 200.0,
    flash_count: int = 0,
    uptime_s: float = 8.0,
    ts: float | None = None,
) -> dict:
    """Build a Metrics.summary()-shaped dict for _evaluate."""
    total = cache_hits + cache_misses
    return {
        "frame_ms": {"p50": p50, "p95": p95, "p99": p99, "n": n},
        "cache":    {"hits": cache_hits, "misses": cache_misses,
                     "ratio": cache_hits / total if total else 0.0},
        "rss_mb":   {"last": rss_mb},
        "flash":    {"count": flash_count},
        "uptime_s": uptime_s,
        "ts":       ts if ts is not None else time.time(),
    }


# =============================================================================
# _evaluate
# =============================================================================

class TestEvaluate(unittest.TestCase):

    def test_pass_when_all_thresholds_met(self):
        out = rb._evaluate(_make_snapshot(p95=20.0, cache_hits=90, rss_mb=200.0))
        self.assertEqual(out["verdict"], "PASS")
        self.assertIn("All thresholds met", out["verdict_detail"])

    def test_warn_when_one_threshold_missed(self):
        # p95 above 33ms ceiling → 1 miss
        out = rb._evaluate(_make_snapshot(p95=50.0))
        self.assertEqual(out["verdict"], "WARN")
        self.assertIn("frame_p95", out["verdict_detail"])

    def test_fail_when_two_thresholds_missed(self):
        # Both p95 and rss out of range
        out = rb._evaluate(_make_snapshot(p95=50.0, rss_mb=600.0))
        self.assertEqual(out["verdict"], "FAIL")
        self.assertIn("2 thresholds", out["verdict_detail"])

    def test_cache_ratio_threshold(self):
        # ratio = 0.4 < 0.5 → miss
        out = rb._evaluate(_make_snapshot(cache_hits=4, cache_misses=6))
        self.assertEqual(out["verdict"], "WARN")
        self.assertIn("cache_ratio", out["verdict_detail"])

    def test_zero_cache_traffic_does_not_trigger_miss(self):
        # ratio = 0.0 but no traffic → not penalized
        out = rb._evaluate(_make_snapshot(cache_hits=0, cache_misses=0, p95=10.0, rss_mb=100.0))
        self.assertEqual(out["verdict"], "PASS")

    def test_results_dict_includes_all_keys(self):
        out = rb._evaluate(_make_snapshot())
        for key in ("frame_p50_ms", "frame_p95_ms", "frame_p99_ms",
                    "cache_ratio", "rss_mb", "flash_count",
                    "frame_samples", "uptime_s"):
            self.assertIn(key, out["results"])


# =============================================================================
# _markdown_report
# =============================================================================

class TestMarkdownReport(unittest.TestCase):

    def test_pass_emits_green_check(self):
        bench = rb._evaluate(_make_snapshot())
        md = rb._markdown_report(bench)
        self.assertIn("PASS", md)
        self.assertIn("✅", md)
        self.assertIn("Results vs Thresholds", md)

    def test_fail_emits_red_x(self):
        bench = rb._evaluate(_make_snapshot(p95=99.0, rss_mb=600.0))
        md = rb._markdown_report(bench)
        self.assertIn("FAIL", md)
        self.assertIn("❌", md)
        self.assertIn("MISS", md)

    def test_table_columns_present(self):
        bench = rb._evaluate(_make_snapshot())
        md = rb._markdown_report(bench)
        for col in ("Metric", "Value", "Threshold", "Status"):
            self.assertIn(col, md)


# =============================================================================
# main() with _run_pilot mocked
# =============================================================================

class TestMainEntryPoint(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_bench_")
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_main(self, argv: list[str], pilot_result: dict | Exception):
        """Invoke rb.main() with sys.argv patched + _run_pilot mocked."""

        async def _fake_pilot():
            if isinstance(pilot_result, Exception):
                raise pilot_result
            return pilot_result

        stdout = StringIO()
        stderr = StringIO()
        with patch.object(sys, "argv", ["run_bench", *argv]), \
             patch.object(rb, "_run_pilot", _fake_pilot), \
             patch("sys.stdout", stdout), \
             patch("sys.stderr", stderr):
            try:
                rb.main()
                exit_code = 0
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else 1
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_main_pass_returns_zero(self):
        snap = _make_snapshot()
        rc, out, err = self._run_main([], snap)
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertEqual(result["verdict"], "PASS")
        self.assertIn("verdict=PASS", err)

    def test_main_warn_returns_one(self):
        snap = _make_snapshot(p95=50.0)
        rc, _, err = self._run_main([], snap)
        self.assertEqual(rc, 1)
        self.assertIn("verdict=WARN", err)

    def test_main_fail_returns_two(self):
        snap = _make_snapshot(p95=99.0, rss_mb=999.0)
        rc, _, err = self._run_main([], snap)
        self.assertEqual(rc, 2)
        self.assertIn("verdict=FAIL", err)

    def test_main_pilot_exception_returns_two_with_fail_verdict(self):
        rc, out, _ = self._run_main([], RuntimeError("pilot died"))
        self.assertEqual(rc, 2)
        result = json.loads(out)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIn("Pilot failed", result["verdict_detail"])

    def test_main_writes_json_out_file(self):
        dest = self.dir / "bench.json"
        snap = _make_snapshot()
        rc, _, _ = self._run_main(["--json-out", str(dest)], snap)
        self.assertEqual(rc, 0)
        self.assertTrue(dest.exists())
        result = json.loads(dest.read_text())
        self.assertEqual(result["verdict"], "PASS")

    def test_main_markdown_flag_prints_report(self):
        snap = _make_snapshot()
        rc, out, _ = self._run_main(["--markdown"], snap)
        self.assertEqual(rc, 0)
        self.assertIn("# m5-watcher bench report", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
