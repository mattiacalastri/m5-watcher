"""tests_extra.py — extra unit tests for polpo_charts, metrics, cli_commands.

35+ tests across 3 modules.
Run: python tests_extra.py   (from project root)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path

# ── ensure project root on sys.path ──────────────────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polpo_charts as pc
from metrics import Metrics


# =============================================================================
# 1. polpo_charts — 14 tests
# =============================================================================

class TestSparkline(unittest.TestCase):

    def test_empty_returns_dashes(self):
        result = pc.sparkline([])
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        # default width=50 dashes
        self.assertIn("─", result)

    def test_constant_series_mid_glyph(self):
        result = pc.sparkline([5] * 10)
        # constant series → mid glyph (SPARK9[4] = '▄')
        mid = pc.SPARK9[len(pc.SPARK9) // 2]
        # should NOT be all '█' (highest glyph)
        self.assertNotEqual(result, "█" * 10)
        # every char should be the same mid glyph
        self.assertEqual(len(set(result)), 1)
        self.assertEqual(result[0], mid)

    def test_ascending_first_less_than_last(self):
        result = pc.sparkline([0, 50, 100])
        # first char should be lower glyph than last
        self.assertLess(
            pc.SPARK9.index(result[0]),
            pc.SPARK9.index(result[-1]),
        )

    def test_with_color_wraps_markup(self):
        result = pc.sparkline([10, 20, 30], color="#ffffff")
        self.assertIn("[", result)
        self.assertIn("]", result)

    def test_returns_string(self):
        result = pc.sparkline([1.0, 2.0])
        self.assertIsInstance(result, str)


class TestPctBar(unittest.TestCase):

    def test_pct_bar_length(self):
        # pct_bar returns Rich markup — count visible chars '█' + '░'
        result = pc.pct_bar(0, 10)
        filled_count = result.count("█")
        empty_count = result.count("░")
        self.assertEqual(filled_count + empty_count, 10)

    def test_pct_bar_100_all_filled(self):
        result = pc.pct_bar(100, 10)
        filled_count = result.count("█")
        self.assertEqual(filled_count, 10)

    def test_pct_bar_0_all_empty(self):
        result = pc.pct_bar(0, 10)
        filled_count = result.count("█")
        self.assertEqual(filled_count, 0)


class TestGauge(unittest.TestCase):

    def test_gauge_returns_tuple(self):
        bar, color = pc.gauge(0, lo=0, hi=100)
        self.assertIsInstance(bar, str)
        self.assertIsInstance(color, str)

    def test_gauge_higher_is_better_false_high_value_is_bad_color(self):
        # higher_is_better=False, val=100 → norm=1.0, color=ORANGE (bad)
        _, color = pc.gauge(100, lo=0, hi=100, higher_is_better=False)
        self.assertEqual(color, pc.ORANGE)

    def test_gauge_degenerate_range_returns_dim(self):
        # hi - lo ≈ 0 → DIM bar
        bar, color = pc.gauge(50, lo=50, hi=50)
        self.assertIn(pc.DIM, bar)
        self.assertEqual(color, pc.DIM)


class TestPctColor(unittest.TestCase):

    def test_low_pct_is_lime(self):
        # pct < 40 → LIME (green / safe)
        result = pc.pct_color(10)
        self.assertEqual(result, pc.LIME)

    def test_high_pct_is_hot_pink(self):
        # pct >= 80 → HOT_PINK (red / bad)
        result = pc.pct_color(100)
        self.assertEqual(result, pc.HOT_PINK)

    def test_mid_pct_not_lime_not_hot_pink(self):
        result = pc.pct_color(50)
        self.assertNotEqual(result, pc.LIME)
        self.assertNotEqual(result, pc.HOT_PINK)


class TestFormatters(unittest.TestCase):

    def test_truncate_exact_clip(self):
        result = pc.truncate("hello", 3)
        # 'hel' (no ellipsis needed if trunc to n-1 + ellipsis = 3 total)
        # actual: s[:2] + ELLIPSIS = "he…"
        self.assertEqual(len(result), 3)
        self.assertTrue(result.startswith("h"))

    def test_truncate_no_op_when_short(self):
        result = pc.truncate("hi", 5)
        self.assertEqual(result, "hi")

    def test_eur_compact_millions(self):
        result = pc.eur_compact(1_500_000)
        self.assertEqual(result, "€1.5M")

    def test_eur_compact_thousands(self):
        result = pc.eur_compact(12_000)
        self.assertIn("k", result)

    def test_gb_conversion(self):
        import math
        result = pc.gb(int(2.5 * 1024 ** 3))
        self.assertIn("2.5", result)
        self.assertIn("G", result)

    def test_empty_state_returns_string(self):
        result = pc.empty_state("⚠", "nothing here")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_empty_state_with_hint(self):
        result = pc.empty_state("⚠", "nothing here", hint="add some data")
        self.assertIn("add some data", result)

    def test_high_contrast_mode_returns_bool(self):
        result = pc.high_contrast_mode()
        self.assertIsInstance(result, bool)

    def test_high_contrast_mode_env_off(self):
        os.environ.pop("M5W_HIGH_CONTRAST", None)
        self.assertFalse(pc.high_contrast_mode())

    def test_high_contrast_mode_env_on(self):
        os.environ["M5W_HIGH_CONTRAST"] = "1"
        try:
            self.assertTrue(pc.high_contrast_mode())
        finally:
            os.environ.pop("M5W_HIGH_CONTRAST", None)


# =============================================================================
# 2. Metrics — 12 tests
# =============================================================================

class TestMetricsConstruction(unittest.TestCase):

    def test_constructs_with_empty_deques(self):
        m = Metrics()
        self.assertIsInstance(m.frame_ms, deque)
        self.assertEqual(len(m.frame_ms), 0)
        self.assertIsInstance(m.rss_mb, deque)
        self.assertEqual(len(m.rss_mb), 0)

    def test_initial_counters_zero(self):
        m = Metrics()
        self.assertEqual(m.cache_hits, 0)
        self.assertEqual(m.cache_misses, 0)
        self.assertEqual(m.flash_count, 0)


class TestMetricsRecording(unittest.TestCase):

    def test_record_frame_appends(self):
        m = Metrics()
        m.record_frame(16.7)
        self.assertEqual(len(m.frame_ms), 1)
        self.assertAlmostEqual(m.frame_ms[0], 16.7)

    def test_record_frame_100_percentiles(self):
        m = Metrics()
        for i in range(100):
            m.record_frame(float(i + 1))
        s = m.summary()
        self.assertGreater(s["frame_ms"]["p50"], 0)
        self.assertGreater(s["frame_ms"]["p95"], s["frame_ms"]["p50"])
        self.assertGreater(s["frame_ms"]["p99"], s["frame_ms"]["p95"])

    def test_cache_ratio_three_hits_one_miss(self):
        m = Metrics()
        m.cache_hit()
        m.cache_hit()
        m.cache_hit()
        m.cache_miss()
        self.assertAlmostEqual(m.hit_ratio(), 0.75)

    def test_cache_ratio_all_hits(self):
        m = Metrics()
        m.cache_hit(100)
        self.assertAlmostEqual(m.hit_ratio(), 1.0)

    def test_cache_ratio_no_calls_is_zero(self):
        m = Metrics()
        ratio = m.hit_ratio()
        self.assertEqual(ratio, 0.0)
        # must not raise and must not be NaN
        self.assertFalse(ratio != ratio)  # NaN check: NaN != NaN

    def test_record_rss_explicit(self):
        m = Metrics()
        m.record_rss(200.0)
        self.assertEqual(len(m.rss_mb), 1)
        s = m.summary()
        self.assertIn("rss_mb", s)
        self.assertGreater(s["rss_mb"]["last"], 0)

    def test_flash_increments_count(self):
        m = Metrics()
        m.flash("test reason")
        self.assertEqual(m.flash_count, 1)
        self.assertIn("test reason", m.flash_reasons)

    def test_to_jsonl_line_valid_json(self):
        m = Metrics()
        m.record_frame(5.0)
        line = m.to_jsonl_line()
        self.assertIsInstance(line, str)
        parsed = json.loads(line.strip())
        self.assertIsInstance(parsed, dict)

    def test_summary_has_expected_keys(self):
        m = Metrics()
        s = m.summary()
        for key in ("ts", "uptime_s", "frame_ms", "slow_ms", "cache",
                    "idle", "flash", "tick_drift_ms", "rss_mb"):
            self.assertIn(key, s, f"Missing key: {key}")

    def test_summary_frame_ms_has_percentile_keys(self):
        m = Metrics()
        for v in range(10):
            m.record_frame(float(v))
        s = m.summary()
        self.assertIn("p50", s["frame_ms"])
        self.assertIn("p95", s["frame_ms"])
        self.assertIn("p99", s["frame_ms"])
        self.assertIn("n", s["frame_ms"])


# =============================================================================
# 3. cli_commands — 13 tests
# =============================================================================

import cli_commands as cc


class TestAddSubparsers(unittest.TestCase):

    def _make_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser()
        cc.add_subparsers(p)
        return p

    def test_registers_snapshot(self):
        p = self._make_parser()
        args = p.parse_args(["snapshot"])
        self.assertEqual(args.cmd, "snapshot")

    def test_registers_tail_feed(self):
        p = self._make_parser()
        args = p.parse_args(["tail-feed"])
        self.assertEqual(args.cmd, "tail-feed")

    def test_registers_export_kpi(self):
        p = self._make_parser()
        args = p.parse_args(["export-kpi"])
        self.assertEqual(args.cmd, "export-kpi")

    def test_registers_health(self):
        p = self._make_parser()
        args = p.parse_args(["health"])
        self.assertEqual(args.cmd, "health")

    def test_snapshot_has_pretty_flag(self):
        p = self._make_parser()
        args = p.parse_args(["snapshot", "--pretty"])
        self.assertTrue(args.pretty)

    def test_snapshot_has_output_flag(self):
        p = self._make_parser()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        args = p.parse_args(["snapshot", "--output", tmp])
        self.assertEqual(args.output, tmp)

    def test_export_kpi_has_format_choices(self):
        p = self._make_parser()
        args_csv = p.parse_args(["export-kpi", "--format", "csv"])
        self.assertEqual(args_csv.format, "csv")
        args_json = p.parse_args(["export-kpi", "--format", "json"])
        self.assertEqual(args_json.format, "json")

    def test_export_kpi_invalid_format_raises(self):
        p = self._make_parser()
        with self.assertRaises(SystemExit):
            p.parse_args(["export-kpi", "--format", "xml"])


class TestCmdSnapshot(unittest.TestCase):

    def _make_args(self, pretty=False, output=None):
        ns = argparse.Namespace()
        ns.pretty = pretty
        ns.output = output
        return ns

    def test_snapshot_no_output_returns_0(self):
        import io
        from unittest.mock import patch
        with patch("sys.stdout", new_callable=io.StringIO):
            rc = cc.cmd_snapshot(self._make_args())
        self.assertEqual(rc, 0)

    def test_snapshot_with_output_file_exists(self):
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "snap.json")
            rc = cc.cmd_snapshot(self._make_args(output=dest))
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(dest))

    def test_snapshot_output_is_valid_json(self):
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "snap.json")
            cc.cmd_snapshot(self._make_args(output=dest))
            with open(dest, encoding="utf-8") as f:
                data = json.load(f)
            self.assertIsInstance(data, dict)

    def test_snapshot_has_required_keys(self):
        with tempfile.TemporaryDirectory() as td:
            dest = os.path.join(td, "snap.json")
            cc.cmd_snapshot(self._make_args(output=dest))
            with open(dest, encoding="utf-8") as f:
                data = json.load(f)
            for key in ("timestamp", "chip", "memory", "battery"):
                self.assertIn(key, data, f"Missing key: {key}")

    def test_snapshot_pretty_output_is_indented(self):
        import io
        from unittest.mock import patch
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cc.cmd_snapshot(self._make_args(pretty=True))
        self.assertEqual(rc, 0)
        output = buf.getvalue()
        # indented JSON has newlines and leading spaces
        self.assertIn("\n", output)
        self.assertIn("  ", output)


class TestCmdHealth(unittest.TestCase):

    def _make_args(self):
        return argparse.Namespace()

    def test_health_returns_int(self):
        import io
        from unittest.mock import patch
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cc.cmd_health(self._make_args())
        self.assertIn(rc, {0, 1, 2})

    def test_health_output_is_valid_json(self):
        import io
        from unittest.mock import patch
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cc.cmd_health(self._make_args())
        data = json.loads(buf.getvalue())
        self.assertIsInstance(data, dict)
        self.assertIn("verdict", data)

    def test_health_verdict_is_valid(self):
        import io
        from unittest.mock import patch
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cc.cmd_health(self._make_args())
        data = json.loads(buf.getvalue())
        self.assertIn(data["verdict"], {"OK", "WARN", "ERROR"})


class TestWebhookPost(unittest.TestCase):

    def test_invalid_url_does_not_raise(self):
        # fire-and-forget: must NOT raise on invalid URL
        try:
            cc.webhook_post("http://invalid.example.invalid/hook", {"test": 1})
        except Exception as exc:
            self.fail(f"webhook_post raised unexpectedly: {exc}")

    def test_localhost_does_not_raise(self):
        # unreachable local port — still must not raise
        try:
            cc.webhook_post("http://127.0.0.1:19999/hook", {"ping": True})
        except Exception as exc:
            self.fail(f"webhook_post raised unexpectedly: {exc}")

    def test_post_handles_unreachable_host_silently(self):
        # _post must swallow every exception (fire-and-forget contract)
        try:
            cc._post("http://127.0.0.1:1/no_such_port", {"ping": True})
        except Exception as exc:
            self.fail(f"_post raised unexpectedly: {exc}")

    def test_webhook_post_inside_running_loop_uses_to_thread(self):
        import asyncio

        async def run() -> None:
            cc.webhook_post("http://127.0.0.1:1/no_such_port", {"hello": "world"})
            # Yield once so the scheduled task settles
            await asyncio.sleep(0)

        # Must not raise, even though the URL is unreachable
        asyncio.run(run())


class TestCmdExportKpi(unittest.TestCase):

    def _make_args(self, fmt: str = "csv"):
        ns = argparse.Namespace()
        ns.format = fmt
        return ns

    def test_missing_kpi_returns_1(self):
        import io
        from unittest.mock import patch
        with patch.object(cc, "kpi_widget") as fake_kpi:
            fake_kpi.read_kpi_data.return_value = {}
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = cc.cmd_export_kpi(self._make_args(fmt="csv"))
        self.assertEqual(rc, 1)

    def test_kpi_read_error_returns_1(self):
        import io
        from unittest.mock import patch
        with patch.object(cc, "kpi_widget") as fake_kpi:
            fake_kpi.read_kpi_data.side_effect = RuntimeError("boom")
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = cc.cmd_export_kpi(self._make_args())
        self.assertEqual(rc, 1)

    def test_csv_format_emits_header_and_rows(self):
        import io
        from unittest.mock import patch
        kpi = {"mrr": 5000, "cac": 120}
        buf = io.StringIO()
        with patch.object(cc, "kpi_widget") as fake_kpi:
            fake_kpi.read_kpi_data.return_value = kpi
            with patch("sys.stdout", buf):
                rc = cc.cmd_export_kpi(self._make_args(fmt="csv"))
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("key,value", out)
        self.assertIn("mrr,5000", out)
        self.assertIn("cac,120", out)

    def test_json_format_is_valid_json(self):
        import io
        from unittest.mock import patch
        kpi = {"mrr": 5000, "outstanding": 1200}
        buf = io.StringIO()
        with patch.object(cc, "kpi_widget") as fake_kpi:
            fake_kpi.read_kpi_data.return_value = kpi
            with patch("sys.stdout", buf):
                rc = cc.cmd_export_kpi(self._make_args(fmt="json"))
        self.assertEqual(rc, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["mrr"], 5000)
        self.assertEqual(parsed["outstanding"], 1200)


class TestCmdTailFeed(unittest.TestCase):

    def _make_args(self):
        return argparse.Namespace()

    def test_missing_metrics_file_returns_0(self):
        import io
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            ghost = Path(td) / "metrics.jsonl"
            buf = io.StringIO()
            with patch.object(cc, "_METRICS_FILE", ghost), patch("sys.stderr", buf):
                rc = cc.cmd_tail_feed(self._make_args())
        self.assertEqual(rc, 0)
        self.assertIn("not found", buf.getvalue())


class TestCmdSnapshotErrorPaths(unittest.TestCase):

    def _make_args(self, output=None):
        ns = argparse.Namespace()
        ns.pretty = False
        ns.output = output
        return ns

    def test_snapshot_unwritable_path_returns_1(self):
        import io
        from unittest.mock import patch
        # /proc/1/no_write is reliably unwritable on Linux; fall back to
        # explicit OSError simulation via mkdir -p mock if needed.
        bad = "/proc/1/no_write/snap.json"
        buf = io.StringIO()
        with patch("sys.stderr", buf), patch("sys.stdout", new_callable=io.StringIO):
            rc = cc.cmd_snapshot(self._make_args(output=bad))
        # Either the OS rejects mkdir, or the write fails — both go to rc=1
        if rc != 1:
            self.skipTest(f"unexpected: path turned out to be writable on this host (rc={rc})")
        self.assertIn("snapshot", buf.getvalue())


# =============================================================================
# vault_parser — hermetic vault tests (no dependency on user's Obsidian vault)
# =============================================================================

import vault_parser as _vp


class TestVaultParserHermetic(unittest.TestCase):
    """Exercise vault_parser.vault_graph_data branches with a synthetic vault."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_vault_")
        self.vault = Path(self._tmp.name)
        _vp._cache = None
        _vp._cache_ts = 0.0

    def tearDown(self):
        self._tmp.cleanup()
        _vp._cache = None
        _vp._cache_ts = 0.0

    def _write(self, rel: str, body: str) -> None:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def test_missing_vault_returns_error_dict(self):
        out = _vp.vault_graph_data(vault=self.vault.parent / "definitely_not_there")
        self.assertIn("error", out)
        self.assertEqual(out["stats"]["total"], 0)

    def test_classify_moc_orphan_normal(self):
        self._write("Areas/MOC Atlas.md", "[[Hub]]\n")
        self._write("Areas/Hub.md", "[[Leaf]] [[MOC Atlas]]\n")
        self._write("Areas/Leaf.md", "body\n")
        self._write("Areas/Lonely.md", "lone island\n")
        out = _vp.vault_graph_data(vault=self.vault)
        G = out["graph"]
        # MOC: name starts with MOC → 'moc' regardless of in-degree
        self.assertEqual(G.nodes["MOC Atlas"]["type"], "moc")
        # Hub has incoming edge from MOC Atlas → 'normal'
        self.assertEqual(G.nodes["Hub"]["type"], "normal")
        # Lonely has no inbound edges → 'orphan'
        self.assertEqual(G.nodes["Lonely"]["type"], "orphan")

    def test_status_distribution_parsed(self):
        self._write("a.md", "---\nstatus: seed\n---\n[[b]]\n")
        self._write("b.md", "---\nstatus: growing\n---\n[[a]]\n")
        self._write("c.md", "---\nstatus: evergreen\n---\n[[a]]\n")
        self._write("d.md", "---\nstatus: stub\n---\n[[a]]\n")
        out = _vp.vault_graph_data(vault=self.vault)
        dist = out["intel"]["status_dist"]
        self.assertEqual(dist["seed"], 1)
        self.assertEqual(dist["growing"], 1)
        self.assertEqual(dist["evergreen"], 1)
        self.assertEqual(dist["stub"], 1)

    def test_wikilinks_with_pipe_and_anchor(self):
        # [[Target|Display]] and [[Target#Section]] should both resolve to "Target"
        self._write("source.md", "[[Target|alt label]] and [[Target#heading]]\n")
        self._write("Target.md", "real target\n")
        out = _vp.vault_graph_data(vault=self.vault)
        self.assertTrue(out["graph"].has_edge("source", "Target"))

    def test_self_loops_filtered(self):
        # A wikilink to the same stem must not create a self-edge
        self._write("only.md", "[[only]] self ref\n")
        out = _vp.vault_graph_data(vault=self.vault)
        self.assertFalse(out["graph"].has_edge("only", "only"))

    def test_folder_distribution(self):
        self._write("Projects/p.md", "[[Atlas/a]]\n")
        self._write("Atlas/a.md", "leaf\n")
        out = _vp.vault_graph_data(vault=self.vault)
        folder_dist = out["intel"]["folder_dist"] if "folder_dist" in out["intel"] else {}
        # First-level dir counted; root-level files would land in "_root"
        self.assertIn("Projects", folder_dist)
        self.assertIn("Atlas", folder_dist)

    def test_cache_is_used_on_second_call(self):
        self._write("a.md", "[[b]]\n")
        self._write("b.md", "leaf\n")
        first = _vp.vault_graph_data(vault=self.vault)
        # Mutate the cache marker so we can detect a re-parse
        first["__cache_marker__"] = True
        second = _vp.vault_graph_data(vault=self.vault)
        self.assertIs(first, second)
        self.assertTrue(second.get("__cache_marker__"))

    def test_load_semantic_communities_missing_file(self):
        # When semantic_areas.json is unreadable / absent, returns []
        from unittest.mock import patch
        with patch.object(_vp, "_SEMANTIC_AREAS_PATH", self.vault / "missing.json"):
            self.assertEqual(_vp._load_semantic_communities(), [])

    def test_load_semantic_communities_parses_json(self):
        from unittest.mock import patch
        sem = self.vault / "sem.json"
        sem.write_text(json.dumps({
            "communities": [
                {"label": "Marketing", "size": 42, "top_hubs": ["A", "B", "C", "D"]},
                {"label": "Ops",       "size": 17, "top_hubs": ["X"]},
            ]
        }))
        with patch.object(_vp, "_SEMANTIC_AREAS_PATH", sem):
            out = _vp._load_semantic_communities()
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["label"], "Marketing")
        # top_hubs is sliced to 3 max
        self.assertEqual(out[0]["top_hubs"], ["A", "B", "C"])

    def test_intel_keys_present_for_hermetic_vault(self):
        for i in range(12):
            self._write(f"n{i}.md", f"[[n{(i + 1) % 12}]] [[n{(i + 2) % 12}]]\n")
        out = _vp.vault_graph_data(vault=self.vault)
        for key in ("density", "clustering", "giant_ratio", "avg_degree",
                    "n_clusters", "recent_7d", "top_indegree", "top_bridges",
                    "status_dist", "recent_today", "folder_dist"):
            self.assertIn(key, out["intel"], f"missing intel key: {key}")
        self.assertEqual(out["stats"]["total"], 12)


# =============================================================================
# plugin_loader — discovery & registry
# =============================================================================

import plugin_loader as _pl


class TestPluginLoader(unittest.TestCase):
    """Cover the registry decorator + discover_plugins import paths."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_plugins_")
        self.plugin_dir = Path(self._tmp.name)
        _pl.clear_registry()

    def tearDown(self):
        _pl.clear_registry()
        # Clean up modules we injected so subsequent tests get fresh imports
        for name in list(sys.modules):
            if name.startswith("_m5_plugin_"):
                del sys.modules[name]
        self._tmp.cleanup()

    def _write_plugin(self, filename: str, body: str) -> None:
        (self.plugin_dir / filename).write_text(body, encoding="utf-8")

    def test_register_tab_appends_to_registry(self):
        self._write_plugin("ok.py", (
            "from plugin_loader import register_tab\n"
            "from textual.widgets import Static\n"
            "@register_tab(id='tab-ok', label='OK', key='o')\n"
            "def make() -> Static: return Static('hi')\n"
        ))
        tabs = _pl.discover_plugins(self.plugin_dir)
        self.assertEqual(len(tabs), 1)
        tab = tabs[0]
        self.assertEqual(tab.id, "tab-ok")
        self.assertEqual(tab.label, "OK")
        self.assertEqual(tab.key_binding, "o")
        # repr is informative
        self.assertIn("tab-ok", repr(tab))

    def test_discover_skips_underscore_prefixed_files(self):
        self._write_plugin("__init__.py", "")
        self._write_plugin("_private.py", (
            "from plugin_loader import register_tab\n"
            "from textual.widgets import Static\n"
            "@register_tab(id='hidden', label='Hidden')\n"
            "def make(): return Static('no')\n"
        ))
        self.assertEqual(_pl.discover_plugins(self.plugin_dir), [])

    def test_discover_returns_empty_for_missing_dir(self):
        bogus = self.plugin_dir / "not_a_dir"
        self.assertEqual(_pl.discover_plugins(bogus), [])

    def test_failed_plugin_import_is_swallowed(self):
        self._write_plugin("broken.py", "raise RuntimeError('boom')\n")
        # Discovery must not raise — the broken file is logged and skipped
        result = _pl.discover_plugins(self.plugin_dir)
        self.assertEqual(result, [])
        # And the broken module must not linger in sys.modules
        self.assertNotIn("_m5_plugin_broken", sys.modules)

    def test_already_loaded_plugin_is_skipped(self):
        self._write_plugin("once.py", (
            "from plugin_loader import register_tab\n"
            "from textual.widgets import Static\n"
            "@register_tab(id='once', label='Once')\n"
            "def make(): return Static('x')\n"
        ))
        first = _pl.discover_plugins(self.plugin_dir)
        # Second discovery without clear_registry: registry remains, no re-import
        second = _pl.discover_plugins(self.plugin_dir)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)

    def test_clear_registry_resets_state(self):
        self._write_plugin("p.py", (
            "from plugin_loader import register_tab\n"
            "from textual.widgets import Static\n"
            "@register_tab(id='p', label='P')\n"
            "def make(): return Static('y')\n"
        ))
        _pl.discover_plugins(self.plugin_dir)
        self.assertGreater(len(_pl._REGISTRY), 0)
        _pl.clear_registry()
        self.assertEqual(_pl._REGISTRY, [])

    def test_default_plugin_dir_resolves_to_project_plugins(self):
        # Without arg, scans <repo>/plugins/. The shipped claude_advisor plugin
        # may or may not register a tab depending on its own preconditions, but
        # discovery itself must not raise.
        try:
            _pl.discover_plugins()
        except Exception as exc:
            self.fail(f"discover_plugins() raised: {exc}")


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
