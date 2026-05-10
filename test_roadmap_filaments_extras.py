"""test_roadmap_filaments_extras.py — fill remaining branches in roadmap_filaments.

Targets:
  - _strip_md_emph (backticks, bold, italic, plain)
  - _resolve_year (boundary at ±200 days)
  - parse_italian_date (month abbrev + Italian weekday + miss)
  - classify_severity (every emoji branch + drift)
  - _split_table_row + _is_separator_row
  - parse_filaments (full markdown table + missing section)
  - read_filaments (missing file, empty parse, force refresh)
  - _short_stato (truncation)
  - _render_one_line (drift tag + severity dots)
  - render_filaments_section (empty / populated / drift-check failure)
  - _match_rule (resolved / active / no-match)
  - detect_session_drift (unknown rule / skip rule / matched rule)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import roadmap_filaments as rf


# =============================================================================
# Pure helpers
# =============================================================================

class TestStripMdEmph(unittest.TestCase):

    def test_backticks(self):
        self.assertEqual(rf._strip_md_emph("`code`"), "code")

    def test_bold(self):
        self.assertEqual(rf._strip_md_emph("**bold**"), "bold")

    def test_italic(self):
        self.assertEqual(rf._strip_md_emph("*italic*"), "italic")

    def test_plain(self):
        self.assertEqual(rf._strip_md_emph("nothing"), "nothing")

    def test_whitespace(self):
        self.assertEqual(rf._strip_md_emph("  spaced  "), "spaced")


class TestResolveYear(unittest.TestCase):

    def test_same_year_default(self):
        # Today is May 2026; June 2026 is within ±200 days → year=2026
        self.assertEqual(rf._resolve_year(6, date(2026, 5, 1)), 2026)

    def test_far_future_candidate_rolls_back(self):
        # delta = today - candidate. Candidate far in the future → delta < -200
        # → year - 1 (the parser interprets it as a date from last year of that month).
        # today=Feb 1 2026, month=12 → candidate=Dec 1 2026 → delta=-303 → year-1.
        self.assertEqual(rf._resolve_year(12, date(2026, 2, 1)), 2025)

    def test_far_past_candidate_rolls_forward(self):
        # Candidate far in the past → delta > 200 → year+1 (a future occurrence).
        # today=Oct 1 2026, month=1 → candidate=Jan 1 2026 → delta=273 → year+1.
        self.assertEqual(rf._resolve_year(1, date(2026, 10, 1)), 2027)


class TestParseItalianDate(unittest.TestCase):

    def test_day_then_month(self):
        d = rf.parse_italian_date("9 Apr", today=date(2026, 5, 1))
        self.assertEqual(d, date(2026, 4, 9))

    def test_month_then_day(self):
        d = rf.parse_italian_date("Apr 9", today=date(2026, 5, 1))
        self.assertEqual(d, date(2026, 4, 9))

    def test_italian_short_month(self):
        d = rf.parse_italian_date("9 Mag", today=date(2026, 4, 1))
        self.assertIsNotNone(d)
        self.assertEqual(d.month, 5)

    def test_no_date(self):
        self.assertIsNone(rf.parse_italian_date("nothing parseable"))
        self.assertIsNone(rf.parse_italian_date(""))

    def test_weekday_with_day(self):
        # "ven 11" → next Friday with day=11. With today=2026-05-01 (a Friday),
        # the closest Friday-the-11th lands in September 2026. Result must be a date.
        d = rf.parse_italian_date("ven 11", today=date(2026, 5, 1))
        self.assertIsNotNone(d)
        self.assertEqual(d.day, 11)


class TestClassifySeverity(unittest.TestCase):

    def setUp(self):
        self.today = date(2026, 5, 10)

    def test_fire_emoji_p0(self):
        sev, _ = rf.classify_severity("🔥 urgente", None, self.today)
        self.assertEqual(sev, "P0")

    def test_kit_pronto_p0(self):
        sev, _ = rf.classify_severity("KIT PRONTO oggi", None, self.today)
        self.assertEqual(sev, "P0")

    def test_lightning_priority_p0(self):
        sev, _ = rf.classify_severity("⚡ PRIORITÀ assoluta", None, self.today)
        self.assertEqual(sev, "P0")

    def test_call_keyword_p0(self):
        sev, _ = rf.classify_severity("CALL domani", None, self.today)
        self.assertEqual(sev, "P0")

    def test_dopo_call_not_p0(self):
        # "DOPO CALL" must NOT promote to P0
        sev, _ = rf.classify_severity("DOPO CALL aspetta", None, self.today)
        self.assertNotEqual(sev, "P0")

    def test_burn_rate_p0(self):
        sev, _ = rf.classify_severity("brucia €500/giorno", None, self.today)
        self.assertEqual(sev, "P0")

    def test_warning_emoji_p1(self):
        sev, _ = rf.classify_severity("⚠️ pending", None, self.today)
        self.assertEqual(sev, "P1")

    def test_waiting_keyword_p1(self):
        sev, _ = rf.classify_severity("WAITING risposta", None, self.today)
        self.assertEqual(sev, "P1")

    def test_drift_keyword_p1(self):
        sev, _ = rf.classify_severity("DRIFT", None, self.today)
        self.assertEqual(sev, "P1")

    def test_hourglass_no_drift_info(self):
        # ⏳ alone (no past deadline) → info
        sev, drift = rf.classify_severity("⏳ in attesa", None, self.today)
        self.assertEqual(sev, "info")
        self.assertIsNone(drift)

    def test_hourglass_with_drift_p1(self):
        old = self.today - timedelta(days=5)
        sev, drift = rf.classify_severity("⏳ aspetta", old, self.today)
        self.assertEqual(sev, "P1")
        self.assertEqual(drift, 5)

    def test_past_deadline_alone_p1(self):
        old = self.today - timedelta(days=3)
        sev, drift = rf.classify_severity("normal stato", old, self.today)
        self.assertEqual(sev, "P1")
        self.assertEqual(drift, 3)

    def test_default_info(self):
        sev, drift = rf.classify_severity("plain text", None, self.today)
        self.assertEqual(sev, "info")
        self.assertIsNone(drift)


# =============================================================================
# Markdown table helpers
# =============================================================================

class TestSplitTableRow(unittest.TestCase):

    def test_basic_split(self):
        cells = rf._split_table_row("| a | b | c |")
        self.assertEqual(cells, ["a", "b", "c"])

    def test_no_outer_pipes(self):
        cells = rf._split_table_row("a | b | c")
        self.assertEqual(cells, ["a", "b", "c"])


class TestIsSeparatorRow(unittest.TestCase):

    def test_true_for_separator(self):
        self.assertTrue(rf._is_separator_row(["---", "---", "---"]))
        self.assertTrue(rf._is_separator_row([":---:", "---:", ":---"]))

    def test_false_for_data(self):
        self.assertFalse(rf._is_separator_row(["a", "b", "c"]))

    def test_empty_returns_false(self):
        self.assertFalse(rf._is_separator_row([]))


# =============================================================================
# parse_filaments + read_filaments + render
# =============================================================================

_FIXTURE = """\
# Roadmap

### Filamenti attivi

| Nome | Vita | Morte | Stato |
|------|------|-------|-------|
| Bressan | ricontatto | silenzio | 🔥 KIT PRONTO 9 Apr |
| Diella | follow-up | scomparso | ⚠️ pending firma |
| Fondi | erogazione | rinuncia | normale |
| Old | x | y | scaduto 1 Apr |

### Altra Sezione

irrelevant
"""


class TestParseFilaments(unittest.TestCase):

    def test_no_section_returns_empty(self):
        self.assertEqual(rf.parse_filaments("# Just a doc\nno table"), [])

    def test_full_fixture_yields_rows(self):
        out = rf.parse_filaments(_FIXTURE, today=date(2026, 5, 10))
        self.assertEqual(len(out), 4)
        names = [f.name for f in out]
        self.assertIn("Bressan", names)
        self.assertIn("Diella", names)

    def test_severity_classification_in_parse(self):
        out = rf.parse_filaments(_FIXTURE, today=date(2026, 5, 10))
        by_name = {f.name: f for f in out}
        self.assertEqual(by_name["Bressan"].severity, "P0")
        self.assertEqual(by_name["Diella"].severity, "P1")
        self.assertEqual(by_name["Fondi"].severity, "info")
        # Old: deadline 1 Apr, today 10 May → ~39 days drift → P1
        self.assertEqual(by_name["Old"].severity, "P1")
        self.assertGreater(by_name["Old"].days_drift, 0)


class TestReadFilamentsHermetic(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_fil_")
        self.path = Path(self._tmp.name) / "roadmap.md"
        # Reset module-level cache
        rf._CACHE.clear()
        rf._SESSION_CACHE.clear()

    def tearDown(self):
        rf._CACHE.clear()
        rf._SESSION_CACHE.clear()
        self._tmp.cleanup()

    def test_missing_file_returns_empty(self):
        ghost = self.path.parent / "ghost.md"
        self.assertEqual(rf.read_filaments(force=True, path=ghost), [])

    def test_with_fixture(self):
        self.path.write_text(_FIXTURE)
        out = rf.read_filaments(force=True, path=self.path)
        self.assertEqual(len(out), 4)
        for d in out:
            for k in ("name", "severity", "stato"):
                self.assertIn(k, d)


# =============================================================================
# Render helpers
# =============================================================================

class TestShortStato(unittest.TestCase):

    def test_short_unchanged(self):
        self.assertEqual(rf._short_stato("hi"), "hi")

    def test_long_truncated_with_ellipsis(self):
        long = "x" * 200
        out = rf._short_stato(long, max_len=20)
        self.assertEqual(len(out), 20)
        self.assertTrue(out.endswith("…"))

    def test_collapses_whitespace(self):
        self.assertEqual(rf._short_stato("  a    b\n c  "), "a b c")


class TestRenderOneLine(unittest.TestCase):

    def test_severity_dots(self):
        f0 = rf.Filament(name="A", segnale_vita="", segnale_morte="", stato="x", severity="P0")
        f1 = rf.Filament(name="B", segnale_vita="", segnale_morte="", stato="x", severity="P1")
        fi = rf.Filament(name="C", segnale_vita="", segnale_morte="", stato="x", severity="info")
        self.assertIn("A", rf._render_one_line(f0))
        self.assertIn("B", rf._render_one_line(f1))
        self.assertIn("C", rf._render_one_line(fi))

    def test_drift_tag_present_when_drift(self):
        f = rf.Filament(name="X", segnale_vita="", segnale_morte="", stato="late",
                        severity="P1", days_drift=5)
        self.assertIn("DRIFT", rf._render_one_line(f))
        self.assertIn("D+5", rf._render_one_line(f))

    def test_no_drift_tag_without_drift(self):
        f = rf.Filament(name="X", segnale_vita="", segnale_morte="", stato="ok",
                        severity="info", days_drift=None)
        self.assertNotIn("DRIFT", rf._render_one_line(f))


class TestRenderFilamentsSection(unittest.TestCase):

    def setUp(self):
        rf._SESSION_CACHE.clear()

    def tearDown(self):
        rf._SESSION_CACHE.clear()

    def test_empty_filaments(self):
        out = rf.render_filaments_section([])
        self.assertIn("FILAMENTI RADICI", out)
        self.assertIn("no roadmap found", out)

    def test_with_filaments(self):
        # Run through the rendering path. detect_session_drift may read external
        # files; we patch to return a deterministic empty map.
        with patch.object(rf, "detect_session_drift", return_value={}):
            out = rf.render_filaments_section([
                {
                    "name": "A", "segnale_vita": "", "segnale_morte": "",
                    "stato": "🔥 KIT", "severity": "P0",
                    "days_drift": None, "deadline": None,
                },
            ])
        self.assertIn("FILAMENTI RADICI", out)
        self.assertIn("A", out)

    def test_drift_check_failure_logs_and_continues(self):
        # When drift detection raises, render must still produce output.
        def boom(_filaments):
            raise RuntimeError("drift check exploded")
        with patch.object(rf, "detect_session_drift", side_effect=boom):
            out = rf.render_filaments_section([
                {
                    "name": "A", "segnale_vita": "", "segnale_morte": "",
                    "stato": "x", "severity": "info",
                    "days_drift": None, "deadline": None,
                },
            ])
        self.assertIn("FILAMENTI RADICI", out)


# =============================================================================
# Drift detection rule engine
# =============================================================================

class TestSnippetAround(unittest.TestCase):

    def test_short_snippet(self):
        import re
        body = "header info Bressan SALDATA tutto bene poi"
        m = re.search(r"SALDATA", body)
        out = rf._snippet_around(body, m)
        self.assertIn("SALDATA", out)


class TestMatchRule(unittest.TestCase):

    def test_resolved_pattern_returns_roadmap_stale(self):
        rule = ("name", [r"Bressan.*saldata"], [r"Bressan.*pending"], None)
        status, evidence = rf._match_rule(rule, "ieri Bressan saldata tutto")
        self.assertEqual(status, "roadmap_stale")
        self.assertIn("saldata", evidence.lower())

    def test_active_pattern_returns_session_still_active(self):
        rule = ("name", [r"Bressan.*saldata"], [r"Bressan.*pending"], None)
        status, _ = rf._match_rule(rule, "Bressan pending firma")
        self.assertEqual(status, "session_still_active")

    def test_no_match_returns_unknown(self):
        rule = ("name", [r"ZZZRESOLVED"], [r"ZZZACTIVE"], None)
        status, evidence = rf._match_rule(rule, "completely different content")
        self.assertEqual(status, "unknown")
        self.assertIn("no match", evidence)


class TestDetectSessionDrift(unittest.TestCase):

    def setUp(self):
        rf._SESSION_CACHE.clear()
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_drift_")
        self.dir = Path(self._tmp.name)
        self.session = self.dir / "session.md"
        self.kpi = self.dir / "kpi.md"

    def tearDown(self):
        rf._SESSION_CACHE.clear()
        self._tmp.cleanup()

    def test_empty_session_returns_empty(self):
        with patch.object(rf, "SESSION_PATH", self.dir / "ghost.md"), \
             patch.object(rf, "KPI_PATH", self.dir / "ghost2.md"):
            self.assertEqual(rf.detect_session_drift([{"name": "X", "stato": "y"}]), {})

    def test_unknown_rule_for_unrecognised_filament(self):
        self.session.write_text("Bressan saldata tutto")
        self.kpi.write_text("")
        with patch.object(rf, "SESSION_PATH", self.session), \
             patch.object(rf, "KPI_PATH", self.kpi):
            out = rf.detect_session_drift([{"name": "Unknown Filament", "stato": "x"}])
        self.assertEqual(out["Unknown Filament"]["status"], "unknown")

    def test_skip_rule_for_fondi(self):
        self.session.write_text("anything")
        self.kpi.write_text("")
        with patch.object(rf, "SESSION_PATH", self.session), \
             patch.object(rf, "KPI_PATH", self.kpi):
            out = rf.detect_session_drift([{"name": "Fondi attivi", "stato": "x"}])
        self.assertEqual(out["Fondi attivi"]["status"], "skip")

    def test_roadmap_stale_when_resolution_in_session(self):
        self.session.write_text("Bressan SALDATA tutto incassato")
        self.kpi.write_text("")
        with patch.object(rf, "SESSION_PATH", self.session), \
             patch.object(rf, "KPI_PATH", self.kpi):
            out = rf.detect_session_drift([{"name": "Bressan", "stato": "pending"}])
        self.assertEqual(out["Bressan"]["status"], "roadmap_stale")
        self.assertEqual(out["Bressan"]["severity_override"], "info")

    def test_in_sync_when_active_pattern_matches(self):
        self.session.write_text("Bressan pending review")
        self.kpi.write_text("")
        with patch.object(rf, "SESSION_PATH", self.session), \
             patch.object(rf, "KPI_PATH", self.kpi):
            out = rf.detect_session_drift([{"name": "Bressan", "stato": "pending"}])
        self.assertEqual(out["Bressan"]["status"], "in_sync")


# =============================================================================
# Session content cache
# =============================================================================

class TestReadSessionContent(unittest.TestCase):

    def setUp(self):
        rf._SESSION_CACHE.clear()
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_sc_")
        self.dir = Path(self._tmp.name)
        self.session = self.dir / "session.md"
        self.kpi = self.dir / "kpi.md"

    def tearDown(self):
        rf._SESSION_CACHE.clear()
        self._tmp.cleanup()

    def test_concatenates_session_and_kpi(self):
        self.session.write_text("SESSION_BODY")
        self.kpi.write_text("KPI_BODY")
        with patch.object(rf, "SESSION_PATH", self.session), \
             patch.object(rf, "KPI_PATH", self.kpi):
            text = rf._read_session_content(force=True)
        self.assertIn("SESSION_BODY", text)
        self.assertIn("KPI_BODY", text)

    def test_missing_files_returns_empty_string(self):
        with patch.object(rf, "SESSION_PATH", self.dir / "ghost1.md"), \
             patch.object(rf, "KPI_PATH", self.dir / "ghost2.md"):
            self.assertEqual(rf._read_session_content(force=True), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
