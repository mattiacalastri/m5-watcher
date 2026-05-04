"""🐙 M5 Max Watcher — test suite completo.

Copertura:
  - Utility functions (bar, sparkline, stacked_bar, health_score, rainbow_text)
  - Data sources (unified_memory, cpu_per_core, top_processes, tentacoli, battery, I/O)
  - Panel renderers (render_cpu, render_mem, render_heatmap, render_analytics, render_voice)
  - App internals (_count_claude_mcp, _claude_session_number)
  - vault_parser (vault_graph_data — Neural Density cockpit)
  - graph_widget (render_graph — tutti i filtri + error path)
  - kpi_widget (read_kpi_data + render_kpi)
  - data_sources.log_feed (tail, clean_msg, make_title, dedup)
  - Headless Textual (compose + tab switch 1-7 + pause toggle)

Run: venv/bin/python test_suite.py
"""
from __future__ import annotations

import asyncio
import collections
import py_compile
import sys
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path

# ── ensure project root on sys.path ──────────────────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph_widget as _gw
import kpi_widget   as _kw
import vault_parser as _vp


# ─────────────────────────────────────────────────────────────────────────────
# 1. SYNTAX
# ─────────────────────────────────────────────────────────────────────────────

class TestSyntax(unittest.TestCase):
    def _ok(self, fname: str) -> None:
        py_compile.compile(str(ROOT / fname), doraise=True)

    def test_app_syntax(self):         self._ok("app.py")
    def test_data_sources_syntax(self): self._ok("data_sources.py")
    def test_vault_parser_syntax(self): self._ok("vault_parser.py")
    def test_graph_widget_syntax(self): self._ok("graph_widget.py")
    def test_kpi_widget_syntax(self):   self._ok("kpi_widget.py")


# ─────────────────────────────────────────────────────────────────────────────
# 2. DEPS
# ─────────────────────────────────────────────────────────────────────────────

class TestDeps(unittest.TestCase):
    def _ver(self, mod: str, min_ver: tuple) -> None:
        import importlib
        m = importlib.import_module(mod)
        ver = tuple(int(x) for x in m.__version__.split(".")[:len(min_ver)])
        self.assertGreaterEqual(ver, min_ver, f"{mod} version too old")

    def test_textual_version(self):  self._ver("textual",  (0, 80, 0))
    def test_psutil_version(self):   self._ver("psutil",   (5,))
    def test_networkx_version(self): self._ver("networkx", (3,))


# ─────────────────────────────────────────────────────────────────────────────
# 3. UTILITY FUNCTIONS (app.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestUtilities(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.app = app

    def test_bar_zero(self):
        self.assertEqual(len(self.app.bar(0)), 20)

    def test_bar_full(self):
        b = self.app.bar(100)
        self.assertIn("█", b)

    def test_bar_clamp(self):
        self.assertEqual(self.app.bar(-5), self.app.bar(0))
        self.assertEqual(self.app.bar(200), self.app.bar(100))

    def test_sparkline_empty(self):
        s = self.app.sparkline(deque())
        self.assertEqual(len(s), 50)

    def test_sparkline_values(self):
        d = deque([0.0, 25.0, 50.0, 75.0, 100.0])
        s = self.app.sparkline(d, w=5)
        self.assertEqual(len(s), 5)

    def test_stacked_bar_sums(self):
        segs = [(512 * 1024**3, "#ff0000"), (512 * 1024**3, "#00ff00")]
        result = self.app.stacked_bar(segs, 1024 * 1024**3, w=20)
        self.assertIn("█", result)

    def test_health_score_healthy(self):
        score, _ = self.app.health_score(10.0, 30.0, 2.0)
        self.assertGreaterEqual(score, 60)

    def test_health_score_stressed(self):
        score, _ = self.app.health_score(95.0, 95.0, 40.0)
        self.assertLessEqual(score, 40)

    def test_rainbow_text_length(self):
        result = self.app.rainbow_text("Polpo", phase=0.0)
        # rainbow_text wraps chars in Rich hex markup — check all 5 chars survive
        for ch in "Polpo":
            self.assertIn(ch, result)

    def test_health_emoji_green(self):  self.assertEqual(self.app.health_emoji(90), "💚")
    def test_health_emoji_yellow(self): self.assertEqual(self.app.health_emoji(65), "💛")
    def test_health_emoji_red(self):    self.assertEqual(self.app.health_emoji(20), "❤️")


# ─────────────────────────────────────────────────────────────────────────────
# 4. DATA SOURCES
# ─────────────────────────────────────────────────────────────────────────────

class TestDataSources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import data_sources as ds
        cls.ds = ds

    def test_unified_memory_keys(self):
        m = self.ds.unified_memory()
        for key in ("total", "used", "pct"):
            self.assertIn(key, m, f"missing key: {key}")

    def test_unified_memory_pct_range(self):
        pct = self.ds.unified_memory().get("pct", -1)
        self.assertGreaterEqual(pct, 0)
        self.assertLessEqual(pct, 100)

    def test_cpu_per_core_count(self):
        cores = asyncio.run(self.ds.cpu_per_core())
        self.assertEqual(len(cores), self.ds.E_CORES + self.ds.P_CORES)

    def test_top_processes_list(self):
        procs = self.ds.top_processes(5)
        self.assertIsInstance(procs, list)
        self.assertLessEqual(len(procs), 5)
        if procs:
            self.assertIn("pid", procs[0])
            self.assertIn("cpu", procs[0])

    def test_tentacoli_list(self):
        tents = self.ds.tentacoli()
        self.assertIsInstance(tents, list)

    def test_battery_keys(self):
        bat = self.ds.battery()
        self.assertIn("pct", bat)
        self.assertIn("charging", bat)

    def test_disk_io_rate(self):
        d = self.ds.disk_io_rate()
        self.assertIn("read", d)
        self.assertIn("write", d)

    def test_net_io_rate(self):
        n = self.ds.net_io_rate()
        self.assertIn("recv", n)
        self.assertIn("sent", n)


# ─────────────────────────────────────────────────────────────────────────────
# 5. PANEL RENDERERS (app.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        import data_sources as ds
        cls.app = app
        cls.ds = ds

    def _fresh_history(self, n: int = 5, val: float = 50.0) -> deque:
        return deque([val] * n, maxlen=44)

    def test_render_cpu_empty(self):
        result = self.app.render_cpu([], deque(), {}, {})
        self.assertIn("Probing", result)

    def test_render_cpu_live(self):
        cores = asyncio.run(self.ds.cpu_per_core())
        result = self.app.render_cpu(cores, self._fresh_history(), {}, {})
        self.assertGreater(len(result), 50)

    def test_render_mem(self):
        m = self.ds.unified_memory()
        result = self.app.render_mem(m, self._fresh_history(), 30.0, 2.5)
        self.assertGreater(len(result), 50)

    def test_render_heatmap(self):
        history = {i: self._fresh_history() for i in range(self.app.N_CORES)}
        result = self.app.render_heatmap(history)
        self.assertGreater(len(result), 50)

    def test_render_analytics(self):
        h = self._fresh_history()
        cores = {i: self._fresh_history() for i in range(self.app.N_CORES)}
        result = self.app.render_analytics(h, h, cores, 40.0, 50.0, 2.0)
        self.assertGreater(len(result), 50)

    def test_render_voice_no_jarvis(self):
        # voice_data() returns a safe dict even when Jarvis is offline
        vd = self.app.voice_data()
        result = self.app.render_voice(vd)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 6. APP INTERNALS
# ─────────────────────────────────────────────────────────────────────────────

class TestInternals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app
        cls.app = app

    def test_count_claude_mcp_returns_dict(self):
        result = self.app._count_claude_mcp()
        self.assertIsInstance(result, dict)
        self.assertIn("claude", result)
        self.assertIn("mcp", result)

    def test_claude_session_number_format(self):
        result = self.app._claude_session_number()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 7. VAULT PARSER — Tab 5 Neural Density
# ─────────────────────────────────────────────────────────────────────────────

class TestVaultParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        import networkx as nx
        import vault_parser as _vpm

        # Re-evaluate vault path at test time (respects M5_VAULT_PATH env var)
        env_path = os.environ.get("M5_VAULT_PATH")
        if env_path:
            cls.vault = Path(env_path)
        else:
            cls.vault = _vpm.VAULT_PATH

        cls.vault_available = cls.vault.exists() and any(cls.vault.rglob("*.md"))

        # Bypass module-level cache so the test uses the resolved path
        _vpm._cache = None
        _vpm._cache_ts = 0.0

        from vault_parser import vault_graph_data
        t0 = time.monotonic()
        cls.data = vault_graph_data(vault=cls.vault)
        cls.parse_time = time.monotonic() - t0
        cls.nx = nx

    def test_returns_dict(self):
        self.assertIsInstance(self.data, dict)

    def test_stats_keys(self):
        if not self.vault_available:
            self.skipTest("vault not found — skipping live graph tests")
        stats = self.data.get("stats", {})
        for key in ("total", "orphans", "mocs", "edges", "visible"):
            self.assertIn(key, stats)

    def test_intel_keys(self):
        if not self.vault_available:
            self.skipTest("vault not found")
        intel = self.data.get("intel", {})
        for key in ("density", "clustering", "giant_ratio", "avg_degree",
                    "n_clusters", "recent_7d", "top_indegree", "top_bridges",
                    "status_dist", "recent_today"):
            self.assertIn(key, intel, f"missing intel key: {key}")

    def test_node_count_realistic(self):
        if not self.vault_available:
            self.skipTest("vault not found")
        total = self.data["stats"]["total"]
        self.assertGreater(total, 2000, "vault seems too small")

    def test_edge_count_realistic(self):
        if not self.vault_available:
            self.skipTest("vault not found")
        edges = self.data["stats"]["edges"]
        self.assertGreater(edges, 5000, "too few edges")

    def test_graph_is_digraph(self):
        G = self.data.get("graph")
        self.assertIsInstance(G, self.nx.DiGraph)

    def test_top_indegree_format(self):
        if not self.vault_available:
            self.skipTest("vault not found")
        rows = self.data["intel"]["top_indegree"]
        self.assertGreater(len(rows), 0)
        name, in_d, out_d, ntype, bet = rows[0]
        self.assertIsInstance(name, str)
        self.assertIsInstance(in_d, int)
        self.assertIsInstance(bet, float)

    def test_betweenness_second_brain(self):
        if not self.vault_available:
            self.skipTest("vault not found")
        rows = self.data["intel"]["top_indegree"]
        names = [r[0] for r in rows]
        self.assertTrue(any("Second Brain" in n or "KPI" in n or "session_current" in n
                            for n in names), f"expected key nodes, got: {names[:5]}")

    def test_top_bridges_present(self):
        if not self.vault_available:
            self.skipTest("vault not found")
        bridges = self.data["intel"]["top_bridges"]
        self.assertGreater(len(bridges), 0)
        name, score = bridges[0]
        self.assertIsInstance(score, float)
        self.assertGreater(score, 0)

    def test_cache_second_call_faster(self):
        from vault_parser import vault_graph_data
        t0 = time.monotonic()
        vault_graph_data()
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.05, "cache miss on second call")

    def test_error_path_missing_vault(self):
        # vault_graph_data on missing path: rglob returns empty list → stats all-zero
        # (no OSError raised on macOS for nonexistent path, just 0 files found)
        orig_cache = _vp._cache
        orig_ts    = _vp._cache_ts
        _vp._cache = None
        _vp._cache_ts = 0.0
        try:
            result = _vp.vault_graph_data(vault=Path("/tmp/nonexistent_vault_test_polpo"))
            # Either 'error' key or empty stats — both are valid safe fallbacks
            if "error" in result:
                self.assertIsInstance(result["error"], str)
            else:
                self.assertEqual(result["stats"]["total"], 0)
        finally:
            _vp._cache    = orig_cache
            _vp._cache_ts = orig_ts

    def test_parse_time_acceptable(self):
        self.assertLess(self.parse_time, 30.0, "vault parse took too long")


# ─────────────────────────────────────────────────────────────────────────────
# 8. GRAPH WIDGET — render_graph
# ─────────────────────────────────────────────────────────────────────────────

class TestGraphWidget(unittest.TestCase):
    _gdata: dict = {}

    @classmethod
    def setUpClass(cls):
        cls._gdata = _vp.vault_graph_data()

    def test_render_graph_all_mode(self):
        result = _gw.render_graph(self._gdata, filter_mode="all")
        self.assertGreater(len(result), 200)

    def test_render_graph_moc_mode(self):
        result = _gw.render_graph(self._gdata, filter_mode="moc")
        self.assertGreater(len(result), 100)

    def test_render_graph_orphan_mode(self):
        result = _gw.render_graph(self._gdata, filter_mode="orphan")
        self.assertGreater(len(result), 100)

    def test_neural_density_section(self):
        result = _gw.render_graph(self._gdata)
        self.assertIn("NEURAL DENSITY", result)

    def test_data_attractors_section(self):
        result = _gw.render_graph(self._gdata)
        self.assertIn("DATA ATTRACTORS", result)

    def test_stato_vault_section(self):
        result = _gw.render_graph(self._gdata)
        self.assertIn("STATO VAULT", result)

    def test_topologia_section(self):
        result = _gw.render_graph(self._gdata)
        self.assertIn("TOPOLOGIA", result)

    def test_filter_tabs_in_footer(self):
        for mode in _gw.FILTER_MODES:
            result = _gw.render_graph(self._gdata, filter_mode=mode)
            self.assertIn(mode, result)

    def test_error_path_render(self):
        result = _gw.render_graph({"error": "vault non trovato: test"})
        self.assertIn("vault non trovato", result)

    def test_empty_intel_render(self):
        result = _gw.render_graph({"stats": {}, "intel": {}})
        self.assertIn("in corso", result)

    def test_bar_primitive(self):
        b = _gw._bar(50, 100, w=20)
        self.assertIsInstance(b, str)
        self.assertIn("█", b)

    def test_gauge_returns_tuple(self):
        bar, color = _gw._gauge(0.5, 0.0, 1.0)
        self.assertIsInstance(bar, str)
        self.assertIsInstance(color, str)

    def test_nd_score_range(self):
        import re
        result = _gw.render_graph(self._gdata)
        m = re.search(r"NEURAL DENSITY.*?(\d{1,3})/100", result)
        if m:
            score = int(m.group(1))
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)


# ─────────────────────────────────────────────────────────────────────────────
# 9. HEADLESS TEXTUAL
# ─────────────────────────────────────────────────────────────────────────────

class TestHeadlessTextual(unittest.IsolatedAsyncioTestCase):
    async def _make_pilot(self):
        from app import M5Watcher
        from textual.pilot import Pilot
        app_instance = M5Watcher()
        return app_instance

    async def test_compose_no_error(self):
        from app import M5Watcher
        async with M5Watcher().run_test(headless=True) as pilot:
            self.assertIsNotNone(pilot.app)

    async def test_tab_switch_1_to_7(self):
        from app import M5Watcher
        from textual.widgets import TabbedContent
        async with M5Watcher().run_test(headless=True) as pilot:
            for key in ("1", "2", "3", "4", "5", "6", "7"):
                await pilot.press(key)
                await pilot.pause(0.05)
            tc = pilot.app.query_one(TabbedContent)
            self.assertEqual(tc.active, "tab-logs")

    async def test_pause_toggle(self):
        from app import M5Watcher
        async with M5Watcher().run_test(headless=True) as pilot:
            self.assertFalse(pilot.app._paused)
            await pilot.press("p")
            await pilot.pause(0.05)
            self.assertTrue(pilot.app._paused)
            await pilot.press("p")
            await pilot.pause(0.05)
            self.assertFalse(pilot.app._paused)

    async def test_graph_filter_cycle(self):
        from app import M5Watcher
        import graph_widget
        async with M5Watcher().run_test(headless=True) as pilot:
            await pilot.press("5")
            await pilot.pause(0.05)
            initial = pilot.app._graph_filter
            await pilot.press("f")
            await pilot.pause(0.05)
            after = pilot.app._graph_filter
            modes = list(graph_widget.FILTER_MODES)
            self.assertEqual(after, modes[(modes.index(initial) + 1) % len(modes)])

    async def test_all_tab_panes_have_header(self):
        """sess.1539: ogni TabPane deve esporre un header banner uniforme."""
        from app import M5Watcher
        async with M5Watcher().run_test(headless=True) as pilot:
            expected = [
                "heat-header", "analytics-header", "procs-header", "tent-header",
                "graph-header", "kpi-header", "logs-header", "sent-header",
                "debug-header",
            ]
            for hid in expected:
                w = pilot.app.query_one(f"#{hid}")
                self.assertIsNotNone(w, f"missing header #{hid}")

    async def test_titlebar_kpi_line5_renders_with_units(self):
        """sess.1539: line5 KPI deve esporre Nome+Dato+Unità dopo seed _kpi_data."""
        from app import M5Watcher, TitleBar
        async with M5Watcher().run_test(headless=True) as pilot:
            pilot.app._kpi_data = {
                'mrr': 4124.0, 'mrr_previous': 3624.0,
                'outstanding': 5009.0, 'pipeline_weighted': 48668.0,
                'setter_active': 274.0, 'setter_cold_avg': 43.3,
            }
            pilot.app._update_subtitle(cpu=10.0, load=2.0)
            await pilot.pause(0.05)
            tb = pilot.app.query_one("#title-bar", TitleBar)
            payload = tb.rich_info.get('kpi') or {}
            self.assertEqual(payload.get('mrr'), 4124.0)
            self.assertEqual(payload.get('mrr_delta'), 500.0)
            self.assertEqual(payload.get('cold_avg'), 43.3)
            # _last_paint = (ascii_banner, line1, line2, line3, line4, line5)
            tb._repaint()
            self.assertIsNotNone(tb._last_paint)
            line5 = tb._last_paint[5] if len(tb._last_paint) >= 6 else ''
            self.assertIn('€', line5, f"line5 missing currency unit: {line5[:200]}")
            self.assertIn('gg', line5, f"line5 missing gg unit: {line5[:200]}")

    async def test_titlebar_uniform_height_across_tabs(self):
        """sess.1539 round 3: TitleBar height uniforme su TUTTI i 9 tab
        (era 5/9 — review feedback: tent=4 mancante, completata coverage)."""
        from app import M5Watcher, TitleBar
        async with M5Watcher().run_test(headless=True) as pilot:
            tb = pilot.app.query_one("#title-bar", TitleBar)
            heights = []
            # 1=heat 2=stats 3=procs 4=tent 5=graph 6=kpi 7=logs 8=sent 9=debug
            for key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
                await pilot.press(key)
                await pilot.pause(0.05)
                heights.append(tb.styles.height)
            self.assertEqual(len(set(str(h) for h in heights)), 1,
                             f"TitleBar height varies across tabs: {heights}")

    async def test_titlebar_line5_responsive_three_tiers(self):
        """sess.1539 round 3: rendering a tutti e 3 i breakpoint cols
        (full ≥100, compact ≥80, tiny <80). Review feedback: prima solo full
        testato — assertIn('gg', ...) sarebbe FAILED al tier tiny perché lì
        cold_avg è omesso. Verifica € presente in TUTTI i tier."""
        from app import M5Watcher, TitleBar
        async with M5Watcher().run_test(headless=True) as pilot:
            tb = pilot.app.query_one("#title-bar", TitleBar)
            pilot.app._kpi_data = {
                'mrr': 4124.0, 'mrr_previous': 3624.0,
                'outstanding': 5009.0, 'pipeline_weighted': 48668.0,
                'setter_active': 274.0, 'setter_cold_avg': 43.3,
            }
            pilot.app._update_subtitle(cpu=10.0, load=2.0)
            for cols in (120, 90, 70):
                with self.subTest(cols=cols):
                    rich_info = dict(tb.rich_info)
                    rich_info['cols'] = cols
                    tb.rich_info = rich_info
                    tb._last_paint = None
                    tb._repaint()
                    line5 = tb._last_paint[5] if tb._last_paint else ''
                    self.assertIn('€', line5,
                                  f"cols={cols}: missing € — {line5[:100]}")

    async def test_titlebar_line5_negative_delta_uses_hot_pink(self):
        """sess.1539 round 3: mrr_delta < 0 → HOT_PINK color path.
        Review feedback: solo positivo era testato."""
        from app import M5Watcher, TitleBar, HOT_PINK
        async with M5Watcher().run_test(headless=True) as pilot:
            tb = pilot.app.query_one("#title-bar", TitleBar)
            pilot.app._kpi_data = {
                'mrr': 3000.0, 'mrr_previous': 4000.0,  # delta -1000
                'outstanding': 5000.0, 'pipeline_weighted': 10000.0,
                'setter_active': 100.0, 'setter_cold_avg': 50.0,
            }
            pilot.app._update_subtitle(cpu=10.0, load=2.0)
            tb._last_paint = None
            tb._repaint()
            line5 = tb._last_paint[5] if tb._last_paint else ''
            self.assertIn(HOT_PINK, line5,
                          f"negative delta should use HOT_PINK: {line5[:200]}")
            self.assertIn('-', line5, f"negative sign missing: {line5[:200]}")

    async def test_titlebar_line5_sparkline_true_branch_renders(self):
        """sess.1539 round 3: rendering line5 quando sparkline non vuota.
        Review feedback: i 3 ternari [COLOR]bars[/] hanno solo FALSE branch
        testato (history vuota in test_titlebar_kpi_line5)."""
        import kpi_widget as _kw
        from app import M5Watcher, TitleBar
        # Seed _HISTORY_* con punti differenti → sparkline ≥2 char
        for h in (_kw._HISTORY_MRR, _kw._HISTORY_OUTSTAND, _kw._HISTORY_PIPELINE):
            h.clear()
        for v in (3000, 3300, 3600, 3900, 4124):
            _kw._HISTORY_MRR.append(v)
            _kw._HISTORY_OUTSTAND.append(v + 800)
            _kw._HISTORY_PIPELINE.append(v * 10)
        async with M5Watcher().run_test(headless=True) as pilot:
            tb = pilot.app.query_one("#title-bar", TitleBar)
            pilot.app._kpi_data = {
                'mrr': 4124.0, 'mrr_previous': 3624.0,
                'outstanding': 5009.0, 'pipeline_weighted': 48668.0,
                'setter_active': 274.0, 'setter_cold_avg': 43.3,
            }
            pilot.app._update_subtitle(cpu=10.0, load=2.0)
            tb._last_paint = None
            tb._repaint()
            line5 = tb._last_paint[5] if tb._last_paint else ''
            # Almeno una barra unicode sparkline visibile
            spark_chars = set('▁▂▃▄▅▆▇█')
            self.assertTrue(any(c in line5 for c in spark_chars),
                            f"no sparkline char in line5: {line5[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# 10. KPI WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class TestKpiWidget(unittest.TestCase):
    def test_read_kpi_data_missing_path(self):
        result = _kw.read_kpi_data(path=Path("/tmp/nonexistent_kpi_polpo_test.md"))
        self.assertIsInstance(result, dict)

    def test_render_kpi_empty(self):
        result = _kw.render_kpi({})
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_render_kpi_with_mrr(self):
        result = _kw.render_kpi({'mrr': 5000, 'outstanding': 1200, 'pipeline_weighted': 3500})
        self.assertIn("5", result)

    def test_render_kpi_returns_str(self):
        self.assertIsInstance(_kw.render_kpi({}), str)

    def test_kpi_for_titlebar_empty_returns_empty(self):
        """sess.1539 round 2: kpi_for_titlebar({}) → {} (placeholder loading)."""
        self.assertEqual(_kw.kpi_for_titlebar({}), {})

    def test_kpi_for_titlebar_payload_shape(self):
        """sess.1539 round 2: payload completo con tutte le chiavi attese."""
        data = {'mrr': 4124, 'mrr_previous': 3624, 'outstanding': 5009,
                'pipeline_weighted': 48668, 'setter_active': 274,
                'setter_cold_avg': 43.3}
        payload = _kw.kpi_for_titlebar(data)
        for key in ('mrr', 'mrr_delta', 'outstanding', 'pipeline',
                    'leads', 'cold_avg', 'spark_mrr', 'spark_out',
                    'spark_pipe'):
            self.assertIn(key, payload, f"missing key {key}")
        self.assertEqual(payload['mrr'], 4124.0)
        self.assertEqual(payload['mrr_delta'], 500.0)
        self.assertEqual(payload['cold_avg'], 43.3)

    def test_kpi_for_titlebar_sparkline_grows_with_history(self):
        """sess.1539 round 2: sparkline non vuota dopo ≥2 punti history.

        sess.1539 round 3: clear ALL 3 history deque per isolazione completa
        (review feedback: prima clear solo _HISTORY_MRR → flaky se altri test
        avevano popolato _HISTORY_OUTSTAND/_HISTORY_PIPELINE)."""
        for h in (_kw._HISTORY_MRR, _kw._HISTORY_OUTSTAND, _kw._HISTORY_PIPELINE):
            h.clear()
        for v in (3000, 3200, 3500, 3800, 4124):
            _kw._HISTORY_MRR.append(v)
        payload = _kw.kpi_for_titlebar({'mrr': 4124})
        self.assertGreaterEqual(len(payload['spark_mrr']), 2,
                                f"sparkline troppo corto: {payload['spark_mrr']!r}")
        # History pulita → sparkline OUT/PIPE vuote per garanzia
        self.assertEqual(payload['spark_out'],  '')
        self.assertEqual(payload['spark_pipe'], '')

    def test_kpi_for_titlebar_nan_safe(self):
        """sess.1539 round 2: NaN/inf nel YAML non crashano il payload."""
        payload = _kw.kpi_for_titlebar({
            'mrr': 'corrupt', 'mrr_previous': float('inf'),
            'outstanding': float('nan'), 'pipeline_weighted': None,
            'setter_active': '12.5', 'setter_cold_avg': 'abc',
        })
        self.assertEqual(payload['mrr'], 0.0)
        self.assertEqual(payload['outstanding'], 0.0)
        self.assertEqual(payload['leads'], 12.5)
        self.assertEqual(payload['cold_avg'], 0.0)

    def test_kpi_for_titlebar_non_dict_input_safe(self):
        """sess.1539 round 3: input non-dict (list/int/str/None) → {}.

        Hardening contro YAML parser malformato che torna list/scalar.
        Senza guard isinstance(dict) il main loop crasha ogni tick.
        """
        for bad_input in ([1, 2, 3], "string", 42, None, 0, "", []):
            with self.subTest(bad=repr(bad_input)):
                self.assertEqual(_kw.kpi_for_titlebar(bad_input), {})


# ─────────────────────────────────────────────────────────────────────────────
# 11. LOG FEED
# ─────────────────────────────────────────────────────────────────────────────

import data_sources as _ds


class TestLogFeed(unittest.TestCase):
    def test_log_feed_returns_list(self):
        result = _ds.log_feed()
        self.assertIsInstance(result, list)

    def test_log_feed_entry_keys(self):
        result = _ds.log_feed()
        for entry in result[:5]:
            for key in ('ts', 'emoji', 'title', 'source', 'desc'):
                self.assertIn(key, entry)

    def test_log_feed_empty_sources(self):
        """No crash when all log paths are missing."""
        orig = _ds._LOG_SOURCES
        _ds._LOG_SOURCES = [("/tmp/nonexistent_polpo_test_abc123.log", "🔥", "Test")]
        try:
            result = _ds.log_feed()
            self.assertEqual(result, [])
        finally:
            _ds._LOG_SOURCES = orig

    def test_clean_msg_strips_timestamp_prefix(self):
        line = "[2026-05-03T20:45:24.208] info | lead ricevuto dal form"
        msg = _ds._clean_msg(line)
        self.assertNotIn("2026-05-03", msg)
        self.assertGreater(len(msg), 3)

    def test_make_title_short_line(self):
        title = _ds._make_title("lead ricevuto")
        self.assertEqual(title, "lead ricevuto")

    def test_make_title_splits_on_separator(self):
        title = _ds._make_title("GHL Alert: nuovo lead da Facebook")
        self.assertEqual(title, "GHL Alert")

    def test_tail_reads_last_lines(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
            for i in range(100):
                f.write(f"line {i}\n")
            fname = f.name
        try:
            lines = _ds._tail(Path(fname), 10)
            self.assertEqual(len(lines), 10)
            self.assertIn("line 99", lines[-1])
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_ts_float_valid(self):
        # Round 8 (sess.1534): _ts_float now returns absolute Unix timestamp
        # using fallback as day anchor. With anchor=0 (1970-01-01 01:00 CET DST-aware),
        # ts="01:30:00" lies within the same day → result = 0 + (offset to 01:30 from anchor).
        # We assert symbolic property: sub-day result is between fallback±86400.
        result = _ds._ts_float("01:30:00", 1746396000.0)  # fallback ~ 4 May 2026 22:00
        self.assertGreaterEqual(result, 1746396000.0 - 86400)
        self.assertLessEqual(result, 1746396000.0 + 86400)

    def test_ts_float_invalid(self):
        self.assertEqual(_ds._ts_float("", 42.0), 42.0)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()

    groups = [
        TestSyntax, TestDeps,
        TestUtilities, TestDataSources, TestRenderers, TestInternals,
        TestVaultParser, TestGraphWidget,
        TestKpiWidget, TestLogFeed,        # sess.1508 round 3: erano orfani
        TestHeadlessTextual,
    ]
    for cls in groups:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
