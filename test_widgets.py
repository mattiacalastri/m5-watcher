"""test_widgets.py — coverage for health_widget and tg_bots_widget.

These widgets had ~9% / ~44% coverage and no dedicated test class.
Run: python -m unittest test_widgets   (from project root)
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import health_widget as hw
import polpo_charts as pc


# =============================================================================
# health_widget — pure helpers (color thresholds)
# =============================================================================

class TestHealthSafeFloat(unittest.TestCase):

    def test_none_returns_default(self):
        self.assertIsNone(hw._safe_float(None))
        self.assertEqual(hw._safe_float(None, 7.0), 7.0)

    def test_string_number(self):
        self.assertEqual(hw._safe_float("3.14"), 3.14)

    def test_invalid_string_returns_default(self):
        self.assertIsNone(hw._safe_float("xyz"))
        self.assertEqual(hw._safe_float("xyz", 1.0), 1.0)

    def test_nan_and_inf_treated_as_invalid(self):
        self.assertIsNone(hw._safe_float(float("nan")))
        self.assertIsNone(hw._safe_float(float("inf")))


class TestPesoColor(unittest.TestCase):

    def test_none_is_dim(self):
        self.assertEqual(hw._peso_color(None, target=72.0), pc.DIM)

    def test_at_or_below_target_is_lime(self):
        self.assertEqual(hw._peso_color(72.0, target=72.0), pc.LIME)
        self.assertEqual(hw._peso_color(73.0, target=72.0), pc.LIME)

    def test_progressing_below_ref_is_soft_green(self):
        self.assertEqual(hw._peso_color(78.0, target=72.0), pc.SOFT_GREEN)

    def test_above_reference_is_orange(self):
        self.assertEqual(hw._peso_color(82.0, target=72.0), pc.ORANGE)


class TestPassiColor(unittest.TestCase):

    def test_none_is_dim(self):
        self.assertEqual(hw._passi_color(None), pc.DIM)

    def test_at_target_lime(self):
        self.assertEqual(hw._passi_color(8000), pc.LIME)
        self.assertEqual(hw._passi_color(12000), pc.LIME)

    def test_60pct_orange(self):
        self.assertEqual(hw._passi_color(5000), pc.ORANGE)

    def test_well_below_hot_pink(self):
        self.assertEqual(hw._passi_color(2000), pc.HOT_PINK)


class TestSonnoColor(unittest.TestCase):

    def test_thresholds(self):
        self.assertEqual(hw._sonno_color(None), pc.DIM)
        self.assertEqual(hw._sonno_color(8.0), pc.LIME)
        self.assertEqual(hw._sonno_color(7.0), pc.SOFT_GREEN)
        self.assertEqual(hw._sonno_color(6.0), pc.ORANGE)
        self.assertEqual(hw._sonno_color(4.0), pc.HOT_PINK)


class TestHrvColor(unittest.TestCase):

    def test_none_returns_dim(self):
        self.assertEqual(hw._hrv_color(None, 50.0), pc.DIM)

    def test_no_baseline_returns_blue(self):
        self.assertEqual(hw._hrv_color(40.0, None), pc.ELEC_BLUE)
        self.assertEqual(hw._hrv_color(40.0, 0), pc.ELEC_BLUE)

    def test_severe_drop_hot_pink(self):
        # delta_pct = -25% → HOT_PINK (< -20)
        self.assertEqual(hw._hrv_color(45.0, 60.0), pc.HOT_PINK)

    def test_moderate_drop_orange(self):
        # delta_pct = -15% → ORANGE
        self.assertEqual(hw._hrv_color(51.0, 60.0), pc.ORANGE)

    def test_normal_lime(self):
        self.assertEqual(hw._hrv_color(60.0, 60.0), pc.LIME)


class TestRhrColor(unittest.TestCase):

    def test_none_dim(self):
        self.assertEqual(hw._rhr_color(None, 60.0), pc.DIM)

    def test_no_baseline_blue(self):
        self.assertEqual(hw._rhr_color(60.0, None), pc.ELEC_BLUE)
        self.assertEqual(hw._rhr_color(60.0, 0), pc.ELEC_BLUE)

    def test_high_elevation_hot_pink(self):
        # +15% → HOT_PINK (> 10)
        self.assertEqual(hw._rhr_color(69.0, 60.0), pc.HOT_PINK)

    def test_moderate_elevation_orange(self):
        # +8% → ORANGE
        self.assertEqual(hw._rhr_color(65.0, 60.0), pc.ORANGE)

    def test_normal_lime(self):
        self.assertEqual(hw._rhr_color(60.0, 60.0), pc.LIME)


class TestSpo2Color(unittest.TestCase):

    def test_thresholds(self):
        self.assertEqual(hw._spo2_color(None), pc.DIM)
        self.assertEqual(hw._spo2_color(91.0), pc.HOT_PINK)
        self.assertEqual(hw._spo2_color(94.0), pc.ORANGE)
        self.assertEqual(hw._spo2_color(98.0), pc.LIME)


class TestStaleMarker(unittest.TestCase):

    def test_none_is_dim_pause(self):
        emoji, color = hw._stale_marker(None)
        self.assertEqual(emoji, "⏸")
        self.assertEqual(color, pc.DIM)

    def test_under_one_hour_is_lime_full_dot(self):
        emoji, color = hw._stale_marker(30)
        self.assertEqual(emoji, "●")
        self.assertEqual(color, pc.LIME)

    def test_under_a_day_is_orange_half(self):
        _, color = hw._stale_marker(60 * 5)
        self.assertEqual(color, pc.ORANGE)

    def test_more_than_a_day_is_hot_pink(self):
        _, color = hw._stale_marker(60 * 36)
        self.assertEqual(color, pc.HOT_PINK)


class TestFmtGiorniFa(unittest.TestCase):

    def test_zero_is_oggi(self):
        self.assertEqual(hw._fmt_giorni_fa(0), "oggi")

    def test_one_is_ieri(self):
        self.assertEqual(hw._fmt_giorni_fa(1), "ieri")

    def test_n_is_n_gg_fa(self):
        self.assertEqual(hw._fmt_giorni_fa(5), "5gg fa")

    def test_none_is_empty(self):
        self.assertEqual(hw._fmt_giorni_fa(None), "")


# =============================================================================
# health_widget — read_health_data + render_health (snapshot-driven)
# =============================================================================

class TestReadHealthData(unittest.TestCase):

    def setUp(self):
        # Tests bypass the 30s module cache by passing path explicitly
        hw._cache = None
        hw._cache_ts = 0.0
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_health_")
        self.snap = Path(self._tmp.name) / "snapshot.json"

    def tearDown(self):
        self._tmp.cleanup()
        hw._cache = None
        hw._cache_ts = 0.0

    def test_missing_file_returns_empty_dict(self):
        self.assertEqual(hw.read_health_data(self.snap), {})

    def test_corrupted_json_returns_empty_dict(self):
        self.snap.write_text("{not valid json", encoding="utf-8")
        self.assertEqual(hw.read_health_data(self.snap), {})

    def test_valid_json_passes_through(self):
        payload = {"n_records": 3, "peso": {"current": 75.0}}
        self.snap.write_text(json.dumps(payload), encoding="utf-8")
        out = hw.read_health_data(self.snap)
        self.assertEqual(out, payload)


class TestRenderHealth(unittest.TestCase):

    def setUp(self):
        hw._cache = None
        hw._cache_ts = 0.0

    def tearDown(self):
        hw._cache = None
        hw._cache_ts = 0.0

    def test_empty_dict_returns_empty_state(self):
        out = hw.render_health({})
        # empty_state returns markup containing the title icon
        self.assertIn("🩺", out)

    def test_zero_records_returns_empty_state(self):
        out = hw.render_health({"n_records": 0})
        self.assertIn("🩺", out)

    def test_full_snapshot_produces_all_sections(self):
        snap = {
            "n_records": 1234,
            "stale_min": 12,
            "peso": {
                "current": 74.0, "target": 72.0, "delta_target": 2.0,
                "delta_lug25": -7.0, "trend_14d": {"spark": "▁▂▃", "delta": -0.4},
                "giorni_fa": 0,
            },
            "passi": {
                "today": 9500, "target": 8000, "qualita": "buona",
                "trend_7d": {"spark": "▂▃▄"},
            },
            "sonno": {
                "totali": 7.6, "profondo": 1.4, "rem": 1.7, "leggero": 4.5,
                "trend_7d": {"spark": "▃▄▅"},
            },
            "cardio": {
                "fc_riposo": 58, "hrv": 55, "spo2": 97, "freq_resp": 14,
                "rhr_trend_7d": {"avg": 60}, "hrv_trend_7d": {"avg": 50},
            },
            "attivita": {
                "energia_kcal": 410, "minuti_esercizio": 35, "vo2_max": 42.5,
                "energia_trend_7d": {"spark": "▂▃▄"},
            },
            "workout": {
                "tipo": "Run", "durata_min": 30, "kcal": 320, "fc_max": 168,
                "note": "tempo run",
            },
            "alerts": ["⚠️ HRV calo", "⚠️ Sonno corto", "⚠️ Stress alto", "⚠️ Quarto"],
        }
        out = hw.render_health(snap, w=20)
        self.assertIn("HEALTH", out)
        self.assertIn("Peso", out)
        self.assertIn("Passi", out)
        self.assertIn("Sonno", out)
        self.assertIn("Attività", out)
        self.assertIn("Run", out)
        # Only first 3 alerts are listed, the 4th collapses into "+1 altri"
        self.assertIn("HRV calo", out)
        self.assertIn("Stress alto", out)
        self.assertNotIn("Quarto", out)
        self.assertIn("+1", out)

    def test_partial_snapshot_falls_back_gracefully(self):
        # Only n_records → all sections should fall through to placeholder lines
        out = hw.render_health({"n_records": 10})
        self.assertIn("Peso", out)
        self.assertIn("Passi", out)
        self.assertIn("Sonno", out)


class TestHealthForTitlebar(unittest.TestCase):

    def test_empty_returns_empty_dict(self):
        self.assertEqual(hw.health_for_titlebar({}), {})
        self.assertEqual(hw.health_for_titlebar({"n_records": 0}), {})

    def test_full_payload_extracts_compact(self):
        snap = {
            "n_records": 100,
            "peso": {"current": 75.0, "trend_14d": {"spark": "▁▂"}},
            "passi": {"today": 8500},
            "sonno": {"totali": 7.2},
            "cardio": {"hrv": 55, "fc_riposo": 60},
            "alerts": ["one", "two"],
            "stale_min": 12,
        }
        out = hw.health_for_titlebar(snap)
        self.assertEqual(out["peso"], 75.0)
        self.assertEqual(out["peso_spark"], "▁▂")
        self.assertEqual(out["passi"], 8500)
        self.assertEqual(out["sonno"], 7.2)
        self.assertEqual(out["hrv"], 55.0)
        self.assertEqual(out["rhr"], 60.0)
        self.assertEqual(out["alerts_n"], 2)
        self.assertEqual(out["stale_min"], 12)


# =============================================================================
# tg_bots_widget — render path
# =============================================================================

class TestTgBotsWidget(unittest.TestCase):

    def setUp(self):
        import tg_bots_widget as tg
        self.tg = tg
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_tg_")
        self.state_path = Path(self._tmp.name) / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_state_missing_file_returns_none(self):
        from unittest.mock import patch
        with patch.object(self.tg, "STATE_PATH", self.state_path):
            self.assertIsNone(self.tg.load_state())

    def test_load_state_invalid_json_returns_none(self):
        self.state_path.write_text("{not json", encoding="utf-8")
        from unittest.mock import patch
        with patch.object(self.tg, "STATE_PATH", self.state_path):
            self.assertIsNone(self.tg.load_state())

    def test_load_state_valid_json_round_trips(self):
        payload = {"updated": "2026-05-10T12:00:00", "total_seen": 7, "by_severity": {"high": 1}}
        self.state_path.write_text(json.dumps(payload), encoding="utf-8")
        from unittest.mock import patch
        with patch.object(self.tg, "STATE_PATH", self.state_path):
            self.assertEqual(self.tg.load_state(), payload)

    def test_render_state_empty_returns_help_text(self):
        out = self.tg.render_state(None)
        self.assertIsInstance(out, str)
        self.assertIn("daemon non attivo", out)
        self.assertIn(str(self.tg.STATE_PATH), out)

    def test_render_state_full_payload(self):
        state = {
            "updated": "2026-05-10T12:34:56",
            "total_seen": 42,
            "by_severity": {"high": 3, "medium": 5, "low": 10, "noise": 24},
            "by_bot": {"@bot_one": 12, "@bot_two": 8},
            "recent": [
                {
                    "ts": "2026-05-10T12:30:00", "bot": "@bot_one",
                    "severity": "high", "preview": "alarm fired",
                    "tags": ["urgent"],
                },
                {
                    "ts": "2026-05-10T12:31:00", "bot": "@bot_two",
                    "severity": "noise", "preview": "ping ping",
                    "tags": ["noise", "verbose"],
                },
            ],
        }
        out = self.tg.render_state(state, max_recent=2)
        self.assertIn("TG Bots Watcher", out)
        self.assertIn("@bot_one", out)
        self.assertIn("alarm fired", out)
        self.assertIn("urgent", out)
        # Severity glyphs surface
        self.assertIn("🔴", out)
        self.assertIn("⚪", out)

    def test_render_state_truncates_recent_to_max(self):
        state = {
            "updated": "x", "total_seen": 0, "by_severity": {},
            "recent": [
                {"ts": f"2026-05-10T00:0{i}:00", "bot": f"b{i}",
                 "severity": "low", "preview": f"line {i}", "tags": []}
                for i in range(8)
            ],
        }
        out = self.tg.render_state(state, max_recent=3)
        self.assertIn("line 0", out)
        self.assertIn("line 2", out)
        self.assertNotIn("line 3", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
