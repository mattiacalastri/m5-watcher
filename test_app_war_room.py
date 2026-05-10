"""test_app_war_room.py — coverage for app_war_room (was 0%).

Targets the pure helpers that don't require a running Textual pilot:
  - health_emoji / _trend_signal / trend_emoji / trend_arrow
  - bar / sparkline / heat / stacked_bar / p_pct / gb / trunc / health_score / _c
  - render_cpu / render_mem / render_heatmap / render_analytics
  - _trim_deque / _fmt_age / _format_uptime
  - forecast_eta / _fmt_eta (war-room exclusive)
  - read_session_current_summary (hermetic file)
  - render_pulse (synthetic data)
  - rainbow_text / _rainbow_hex
  - _safe_render_* error badge path
  - render_focus / render_voice empty-state
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app_war_room as wr


# =============================================================================
# Visual primitives mirroring TestUtilities in test_suite (now for war_room)
# =============================================================================

class TestVisualPrimitives(unittest.TestCase):

    def test_bar_full_at_100(self):
        self.assertEqual(wr.bar(100.0, 10).count("█"), 10)

    def test_bar_clamps_negative(self):
        self.assertNotIn("█", wr.bar(-50.0, 10))

    def test_bar_clamps_over_100(self):
        self.assertEqual(wr.bar(200.0, 10).count("█"), 10)

    def test_sparkline_with_values(self):
        d = deque([10, 30, 60, 90])
        self.assertGreater(len(wr.sparkline(d, w=10)), 0)

    def test_sparkline_empty_returns_dashes(self):
        out = wr.sparkline(deque(), w=10)
        self.assertEqual(len(out), 10)

    def test_stacked_bar_segments(self):
        out = wr.stacked_bar([(50, "#ff0000"), (50, "#00ff00")], total=100, w=20)
        self.assertIsInstance(out, str)

    def test_heat_returns_glyph_color_tuple(self):
        glyph, color = wr.heat(50.0)
        self.assertIsInstance(glyph, str)
        self.assertIsInstance(color, str)

    def test_p_pct_basic(self):
        self.assertAlmostEqual(wr.p_pct([10, 20, 30, 40, 50], 0.5), 30.0)

    def test_p_pct_empty_returns_zero(self):
        self.assertEqual(wr.p_pct([], 0.5), 0.0)

    def test_gb_formats(self):
        out = wr.gb(2 * 1024**3)
        self.assertIn("2", out)

    def test_trunc_short_unchanged(self):
        self.assertEqual(wr.trunc("hi", 10), "hi")

    def test_trunc_long_truncated(self):
        out = wr.trunc("x" * 100, 10)
        self.assertLessEqual(len(out), 10)


class TestColorPicker(unittest.TestCase):

    def test_high_value_red(self):
        self.assertEqual(wr._c(90.0), wr.RED)

    def test_mid_value_yellow(self):
        self.assertEqual(wr._c(70.0), wr.YELLOW)

    def test_low_mid_teal(self):
        self.assertEqual(wr._c(50.0), wr.TEAL)

    def test_low_value_green(self):
        self.assertEqual(wr._c(10.0), wr.GREEN)


class TestHealthScore(unittest.TestCase):

    def test_clean_health_high(self):
        score, color = wr.health_score(cpu=10, ram=20, load=0.5)
        self.assertGreater(score, 60)

    def test_red_zone_low(self):
        score, _ = wr.health_score(cpu=99, ram=99, load=20.0)
        self.assertLess(score, 50)


class TestHealthEmoji(unittest.TestCase):

    def test_thresholds(self):
        self.assertEqual(wr.health_emoji(90), "💚")
        self.assertEqual(wr.health_emoji(70), "💛")
        self.assertEqual(wr.health_emoji(50), "🟧")
        self.assertEqual(wr.health_emoji(20), "❤️")


class TestTrendSignals(unittest.TestCase):

    def test_short_history_returns_none(self):
        self.assertIsNone(wr._trend_signal(deque([1, 2])))

    def test_strong_uptrend(self):
        sig = wr._trend_signal(deque([0, 10, 20, 30, 40, 50]))
        self.assertIsNotNone(sig)
        self.assertEqual(sig[1], 2)

    def test_strong_downtrend(self):
        sig = wr._trend_signal(deque([60, 50, 40, 30, 20, 10]))
        self.assertEqual(sig[1], -2)

    def test_flat_returns_zero(self):
        sig = wr._trend_signal(deque([10, 10, 10, 10, 10, 10]))
        self.assertEqual(sig[1], 0)

    def test_trend_emoji_short(self):
        self.assertIn("─", wr.trend_emoji(deque([1])))

    def test_trend_emoji_with_data(self):
        out = wr.trend_emoji(deque([0, 10, 20, 30, 40, 50]))
        self.assertIn("▲", out)

    def test_trend_arrow_returns_string(self):
        self.assertIsInstance(wr.trend_arrow(deque([1, 2, 3])), str)


# =============================================================================
# Render functions (smoke + sane output)
# =============================================================================

class TestRenderHelpers(unittest.TestCase):

    def test_render_cpu_with_data(self):
        # Provide enough cores for both clusters
        percents = [10.0] * (wr.ds.E_CORES + wr.ds.P_CORES)
        history = deque([20, 30, 40, 50])
        out = wr.render_cpu(percents, history, {"read": 1.0, "write": 0.5},
                            {"recv": 2.0, "sent": 1.0})
        # War-room render shows S/P cluster emojis + I/O footer
        self.assertIn("🍃", out)
        self.assertIn("🚀", out)
        self.assertIn("💾", out)

    def test_render_cpu_empty_returns_probe_message(self):
        out = wr.render_cpu([], deque(), {}, {})
        self.assertIn("Probing", out)

    def test_render_mem_empty_returns_probe(self):
        out = wr.render_mem({}, deque(), 10.0, 1.0)
        self.assertIn("Reading", out)

    def test_render_mem_with_data(self):
        m = {
            "wired": 10**9, "active": 2 * 10**9, "inactive": 5 * 10**8,
            "compressed": 10**8, "free": 4 * 10**9, "used": 5 * 10**9,
            "total": 16 * 10**9, "pct": 50,
            "swap": 0, "swap_pct": 0,
            "pressure": ("ok", "ok"),
        }
        out = wr.render_mem(m, deque([45, 50, 55]), 30.0, 1.5)
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 50)

    def test_render_heatmap_smoke(self):
        history = {i: deque([0, 25, 50, 75]) for i in range(5)}
        out = wr.render_heatmap(history, cols=10)
        self.assertIsInstance(out, str)

    def test_render_analytics_smoke(self):
        cpu_h = deque([10, 20, 30] * 30)
        mem_h = deque([20, 25, 30] * 30)
        cores = {i: deque([10, 20, 30, 40] * 10) for i in range(5)}
        out = wr.render_analytics(cpu_h, mem_h, cores, 25.0, 35.0, 1.5)
        self.assertIsInstance(out, str)


class TestRenderFocusVoice(unittest.TestCase):

    def test_render_focus_empty(self):
        out = wr.render_focus({})
        self.assertIsInstance(out, str)

    def test_render_voice_jarvis_offline(self):
        # voice_data() returns the full schema even when Jarvis is offline.
        vd = wr.voice_data()
        out = wr.render_voice(vd)
        self.assertIsInstance(out, str)

    def test_render_feed_empty(self):
        out = wr.render_feed(deque())
        self.assertIn("attesa", out)

    def test_render_feed_with_lines(self):
        feed = deque(["[bold]event 1[/]", "event 2"])
        out = wr.render_feed(feed)
        self.assertIn("event 1", out)


# =============================================================================
# War-room-specific helpers
# =============================================================================

class TestForecastEta(unittest.TestCase):

    def test_short_history_returns_none(self):
        self.assertIsNone(wr.forecast_eta(deque([10, 20])))

    def test_above_threshold_returns_alert(self):
        out = wr.forecast_eta(deque([90] * 10), threshold=85.0)
        self.assertEqual(out["severity"], "alert")
        self.assertEqual(out["eta_seconds"], 0)

    def test_flat_or_descending_returns_none(self):
        # slope <= 0.05 → no forecast
        self.assertIsNone(wr.forecast_eta(deque([50] * 10), threshold=85.0))
        self.assertIsNone(wr.forecast_eta(deque(range(70, 60, -1)), threshold=85.0))

    def test_rising_returns_eta(self):
        # rising 1 unit per sample, 30 samples, current ~70, threshold 85 → 15 samples
        out = wr.forecast_eta(deque(range(40, 70)), threshold=85.0, sample_dt_s=2.0)
        self.assertIsNotNone(out)
        self.assertGreater(out["eta_seconds"], 0)
        self.assertIn(out["severity"], {"ok", "watch", "warn", "alert"})


class TestFmtEta(unittest.TestCase):

    def test_seconds(self):
        self.assertEqual(wr._fmt_eta(45), "45s")

    def test_minutes(self):
        self.assertEqual(wr._fmt_eta(180), "3m")

    def test_hours(self):
        self.assertEqual(wr._fmt_eta(3725), "1h02m")


class TestFmtAge(unittest.TestCase):

    def test_none_dash(self):
        self.assertEqual(wr._fmt_age(None), "—")

    def test_subsecond(self):
        self.assertEqual(wr._fmt_age(2), "<5s")

    def test_seconds(self):
        self.assertEqual(wr._fmt_age(30), "30s")

    def test_minutes(self):
        self.assertEqual(wr._fmt_age(180), "3m")

    def test_hours(self):
        self.assertEqual(wr._fmt_age(7200), "2h")

    def test_days(self):
        self.assertEqual(wr._fmt_age(86400 * 3), "3d")


class TestFormatUptime(unittest.TestCase):

    def test_format_uptime_returns_string(self):
        self.assertIsInstance(wr._format_uptime(3725), str)


class TestSessionCurrentSummary(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_sc_")
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_paths_returns_empty_dict(self):
        # Patch the candidate paths to point at non-existent files
        ghost = self.dir / "ghost.md"
        with patch.object(wr.Path, "home", return_value=self.dir):
            self.assertEqual(wr.read_session_current_summary(), {})

    def test_parses_full_session_file(self):
        gpc = self.dir / "graphify-polpo-core"
        gpc.mkdir()
        sc = gpc / "session_current.md"
        sc.write_text(
            "---\nsession: 1700\nstatus: open\nupdated: 2026-05-10T12:00:00\n---\n"
            "# Session Doc\n\n"
            "## Task in corso\n\n"
            "- Implementa walker tab\n"
            "- Refactor heat widget\n\n"
            "## Blocchi\n\n"
            "- WhatsApp template Meta\n"
        )
        with patch.object(wr.Path, "home", return_value=self.dir):
            out = wr.read_session_current_summary()
        self.assertEqual(out["session"], "1700")
        self.assertEqual(out["status"], "open")
        self.assertIn("Implementa walker tab", out["tasks"])
        self.assertIn("WhatsApp template Meta", out["blocks"])


class TestRenderPulse(unittest.TestCase):

    def test_render_pulse_smoke(self):
        kpi = {"mrr": 5400, "outstanding": 2100, "pipeline_weighted": 12000,
               "setter_active": 8, "sessione_corrente": "1700"}
        cpu_history = deque([10, 20, 30, 40, 50, 60, 70])
        mem_history = deque([20, 25, 30, 35, 40, 45, 50])
        mem_now = {"pct": 50}
        soul = {
            "session": "1700", "status": "open", "updated": "2026-05-10",
            "tasks": ["task one", "task two"],
            "blocks": ["blocco x"],
        }
        out = wr.render_pulse(kpi, mem_history, cpu_history, mem_now, soul)
        self.assertIn("PREDICTIVE", out)
        self.assertIn("BUSINESS", out)
        self.assertIn("POLPO SOUL", out)
        self.assertIn("1700", out)
        self.assertIn("task one", out)
        self.assertIn("blocco x", out)


class TestRainbowText(unittest.TestCase):

    def test_rainbow_text_returns_markup(self):
        out = wr.rainbow_text("ABC", phase=0.0)
        self.assertIn("A", out)
        self.assertIn("B", out)
        self.assertIn("C", out)

    def test_rainbow_hex_returns_color(self):
        c = wr._rainbow_hex(0, phase=0.5)
        self.assertTrue(c.startswith("#"))
        self.assertEqual(len(c), 7)


# =============================================================================
# Safe render error path
# =============================================================================

class TestSafeRenderErrorPath(unittest.TestCase):

    def test_render_error_badge_includes_section_and_type(self):
        badge = wr._render_error_badge("polestar", RuntimeError("boom"))
        self.assertIn("polestar", badge)
        self.assertIn("RuntimeError", badge)

    def test_log_render_error_does_not_raise(self):
        # Even with a write error the helper must swallow silently
        try:
            wr._log_render_error("test_section", ValueError("x"))
        except Exception as exc:
            self.fail(f"_log_render_error raised: {exc}")

    def test_lazy_roadmap_module_missing_returns_none(self):
        # Clear cache and try to load a non-existent module
        wr._ROADMAP_MODULES_CACHE.clear()
        out = wr._lazy_roadmap_module("definitely_not_a_module_xyz")
        self.assertIsNone(out)
        # Cached on second call
        self.assertIsNone(wr._lazy_roadmap_module("definitely_not_a_module_xyz"))


class TestSafeRenderWrappers(unittest.TestCase):

    SECTIONS = [
        ("polestar",     wr._safe_render_polestar,     "render_polestar_strip"),
        ("vectors",      wr._safe_render_vectors,      "render_vectors_strip"),
        ("traps",        wr._safe_render_traps,        "render_traps_banner"),
        ("filaments",    wr._safe_render_filaments,    "render_filaments_section"),
        ("blocks",       wr._safe_render_blocks,       "render_blocks_section"),
        ("outstanding",  wr._safe_render_outstanding,  "render_outstanding_section"),
    ]

    def setUp(self):
        wr._ROADMAP_MODULES_CACHE.clear()

    def tearDown(self):
        wr._ROADMAP_MODULES_CACHE.clear()

    def test_returns_empty_string_when_module_missing(self):
        for section, fn, _ in self.SECTIONS:
            wr._ROADMAP_MODULES_CACHE.clear()
            with patch.object(wr, "_lazy_roadmap_module", return_value=None):
                self.assertEqual(fn(), "", f"{section} did not return empty")

    def test_returns_module_output_on_success(self):
        for section, fn, render_method in self.SECTIONS:
            wr._ROADMAP_MODULES_CACHE.clear()
            class FakeMod:
                pass
            fake = FakeMod()
            setattr(fake, render_method, lambda **_: f"<{section}>")
            with patch.object(wr, "_lazy_roadmap_module", return_value=fake):
                out = fn()
            self.assertEqual(out, f"<{section}>")

    def test_renders_error_badge_when_module_raises(self):
        for section, fn, render_method in self.SECTIONS:
            wr._ROADMAP_MODULES_CACHE.clear()
            class FakeMod:
                pass
            fake = FakeMod()
            def boom(**_):
                raise RuntimeError(f"{section} boom")
            setattr(fake, render_method, boom)
            with patch.object(wr, "_lazy_roadmap_module", return_value=fake):
                out = fn()
            self.assertIn(section, out)
            self.assertIn("RuntimeError", out)


# =============================================================================
# Headless Textual pilot — exercises the M5Watcher app
# =============================================================================

class TestHeadlessTextualWarRoom(unittest.IsolatedAsyncioTestCase):
    """Smoke pilots that walk the war_room compose tree + tab/key bindings.

    Mirrors test_suite.TestHeadlessTextual but for app_war_room. Each test boots
    M5Watcher headless, exercises a binding, and asserts no exception escapes.
    """

    async def test_compose_no_error(self):
        async with wr.M5Watcher().run_test(headless=True) as pilot:
            self.assertIsNotNone(pilot.app)

    async def test_tab_switch_walks_all_panes(self):
        from textual.widgets import TabbedContent
        async with wr.M5Watcher().run_test(headless=True) as pilot:
            for key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
                await pilot.press(key)
                await pilot.pause(0.05)
            tc = pilot.app.query_one(TabbedContent)
            # Last tab pressed was '9' → pulse pane
            self.assertEqual(tc.active, "tab-pulse")

    async def test_pause_toggle(self):
        async with wr.M5Watcher().run_test(headless=True) as pilot:
            self.assertFalse(pilot.app._paused)
            await pilot.press("p")
            await pilot.pause(0.05)
            self.assertTrue(pilot.app._paused)
            await pilot.press("p")
            await pilot.pause(0.05)
            self.assertFalse(pilot.app._paused)

    async def test_graph_filter_cycle(self):
        import graph_widget
        async with wr.M5Watcher().run_test(headless=True) as pilot:
            await pilot.press("5")
            await pilot.pause(0.05)
            initial = pilot.app._graph_filter
            await pilot.press("f")
            await pilot.pause(0.05)
            after = pilot.app._graph_filter
            modes = list(graph_widget.FILTER_MODES)
            self.assertEqual(after, modes[(modes.index(initial) + 1) % len(modes)])

    async def test_debug_tab_renders(self):
        async with wr.M5Watcher().run_test(headless=True) as pilot:
            await pilot.press("d")
            await pilot.pause(0.05)
            from textual.widgets import TabbedContent
            tc = pilot.app.query_one(TabbedContent)
            self.assertEqual(tc.active, "tab-debug")


if __name__ == "__main__":
    unittest.main(verbosity=2)
