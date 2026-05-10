"""test_roadmap_blocks.py — hermetic coverage for roadmap_blocks module.

Targets the previously-uncovered branches (21% → 80%+):
  - _parse_duration_days (every format)
  - _parse_it_date (Italian month parsing + year inference)
  - _deadline_delta (D+N / OGGI / scaduto branches)
  - _classify_severity (every threshold + cronico)
  - _parse_blocchi_table (markdown table extraction)
  - _audit_block (escalation + missing-sblocco)
  - _drift_audit (ghost detection)
  - read_blocks + render_blocks_section (hermetic roadmap)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import roadmap_blocks as rb
import roadmap_common as rc


# =============================================================================
# Pure helpers
# =============================================================================

class TestParseDurationDays(unittest.TestCase):

    def test_cronico_returns_999(self):
        self.assertEqual(rb._parse_duration_days("cronico"), 999)
        self.assertEqual(rb._parse_duration_days("CRONICA"), 999)
        self.assertEqual(rb._parse_duration_days("chronic"), 999)

    def test_strips_leading_qualifier(self):
        self.assertEqual(rb._parse_duration_days(">6 sett"), 42)
        self.assertEqual(rb._parse_duration_days("≥3 mesi"), 90)
        self.assertEqual(rb._parse_duration_days("~2 sett"), 14)

    def test_mesi(self):
        self.assertEqual(rb._parse_duration_days("1 mese"), 30)
        self.assertEqual(rb._parse_duration_days("3 mesi"), 90)

    def test_settimane(self):
        self.assertEqual(rb._parse_duration_days("2 settimane"), 14)
        self.assertEqual(rb._parse_duration_days("4 sett"), 28)
        self.assertEqual(rb._parse_duration_days("1 week"), 7)

    def test_giorni(self):
        self.assertEqual(rb._parse_duration_days("10 giorni"), 10)
        self.assertEqual(rb._parse_duration_days("5 days"), 5)
        self.assertEqual(rb._parse_duration_days("3d"), 3)

    def test_bare_number(self):
        self.assertEqual(rb._parse_duration_days("42"), 42)

    def test_unrecognized_returns_zero(self):
        self.assertEqual(rb._parse_duration_days("forever"), 0)
        self.assertEqual(rb._parse_duration_days(""), 0)


class TestParseItDate(unittest.TestCase):

    def test_full_date_with_year(self):
        d = rb._parse_it_date("entro 15 mar 2027", today=date(2026, 5, 1))
        self.assertEqual(d, date(2027, 3, 15))

    def test_year_inferred_future(self):
        # "10 dic" with today=01/05 → still this year (Dec 10 > May 1)
        d = rb._parse_it_date("scadenza 10 dic", today=date(2026, 5, 1))
        self.assertEqual(d, date(2026, 12, 10))

    def test_year_inferred_next_year(self):
        # "10 mar" with today=01/05 → next year (Mar already past)
        d = rb._parse_it_date("scadenza 10 mar", today=date(2026, 5, 1))
        self.assertEqual(d, date(2027, 3, 10))

    def test_invalid_month_skipped(self):
        # "30 zzz" — invalid month abbrev, returns None
        self.assertIsNone(rb._parse_it_date("30 zzz", today=date(2026, 5, 1)))

    def test_no_date_present(self):
        self.assertIsNone(rb._parse_it_date("just text", today=date(2026, 5, 1)))


class TestDeadlineDelta(unittest.TestCase):

    def setUp(self):
        # Pin today via env override consumed by today_date()
        os.environ["M5_TODAY_OVERRIDE"] = "2026-05-10"

    def tearDown(self):
        os.environ.pop("M5_TODAY_OVERRIDE", None)

    def test_future_returns_d_plus(self):
        # 15 mag 2026 = today+5 → "D+5"
        self.assertEqual(rb._deadline_delta("entro 15 mag 2026"), "D+5")

    def test_today_returns_oggi(self):
        self.assertEqual(rb._deadline_delta("entro 10 mag 2026"), "OGGI")

    def test_past_returns_scaduto(self):
        self.assertEqual(rb._deadline_delta("entro 1 mag 2026"), "scaduto 9gg")

    def test_no_date_returns_none(self):
        self.assertIsNone(rb._deadline_delta("nessuna data"))


class TestClassifySeverity(unittest.TestCase):

    def test_cronico_is_p0(self):
        self.assertEqual(rb._classify_severity("cronico", 999), "P0")
        self.assertEqual(rb._classify_severity("CRONICA", 999), "P0")

    def test_42_or_more_p0(self):
        self.assertEqual(rb._classify_severity("> 6 sett", 42), "P0")
        self.assertEqual(rb._classify_severity("100 giorni", 100), "P0")

    def test_21_p1(self):
        self.assertEqual(rb._classify_severity("3 sett", 21), "P1")
        self.assertEqual(rb._classify_severity("30 giorni", 30), "P1")

    def test_14_info(self):
        self.assertEqual(rb._classify_severity("2 sett", 14), "info")
        self.assertEqual(rb._classify_severity("20 giorni", 20), "info")

    def test_below_14_info_lime(self):
        self.assertEqual(rb._classify_severity("1 sett", 7), "info-lime")
        self.assertEqual(rb._classify_severity("3 giorni", 3), "info-lime")

    def test_zero_returns_info(self):
        self.assertEqual(rb._classify_severity("", 0), "info")


# =============================================================================
# Markdown table parser
# =============================================================================

_TABLE_FIXTURE = """\
# Q2 2026 Roadmap

## Blocchi Viventi

| Blocco | Da quanto | Energia bloccata | Sblocco | Owner |
|---|---|---|---|---|
| GHL onboarding | cronico | onboarding cliente | rifare flow entro 20 mag 2026 | Mattia |
| Pricing pivot | 4 sett | 30% MRR | call con Marco | Marco |
| WhatsApp template | 2 sett | leads QF | aspetta riposta Meta | Mattia |
| Old block | 5 giorni | small | — | Anna |

## Altra Sezione

irrilevante
"""

_TABLE_GHOST_FIXTURE = """\
## Blocchi Viventi

| Blocco | Da quanto | Energia bloccata | Sblocco | Owner |
|---|---|---|---|---|
| Phantom Project Foo | 30 giorni | nothing | — | Mattia |
| Active Initiative | 10 giorni | active | done by Friday | Anna |

"""

_NO_BLOCCHI_FIXTURE = """\
# Roadmap

## Other section

nothing here
"""


class TestParseBlocchiTable(unittest.TestCase):

    def test_no_section_returns_empty(self):
        self.assertEqual(rb._parse_blocchi_table(_NO_BLOCCHI_FIXTURE), [])

    def test_full_fixture_yields_rows(self):
        rows = rb._parse_blocchi_table(_TABLE_FIXTURE)
        self.assertEqual(len(rows), 4)
        names = [r["name"] for r in rows]
        self.assertIn("GHL onboarding", names)
        self.assertIn("Pricing pivot", names)

    def test_severity_classifications(self):
        rows = rb._parse_blocchi_table(_TABLE_FIXTURE)
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["GHL onboarding"]["severity"], "P0")  # cronico
        self.assertEqual(by_name["Pricing pivot"]["severity"], "P1")    # 28d
        self.assertEqual(by_name["WhatsApp template"]["severity"], "info")  # 14d
        self.assertEqual(by_name["Old block"]["severity"], "info-lime")     # 5d

    def test_owner_extracted(self):
        rows = rb._parse_blocchi_table(_TABLE_FIXTURE)
        owners = {r["name"]: r["owner"] for r in rows}
        self.assertEqual(owners["GHL onboarding"], "Mattia")
        self.assertEqual(owners["Pricing pivot"], "Marco")


# =============================================================================
# Audits
# =============================================================================

class TestAuditBlock(unittest.TestCase):

    def test_missing_sblocco_warns(self):
        b = {"name": "x", "sblocco": "", "owner": "Anna", "da_quanto_days": 10}
        warns = rb._audit_block(b)
        self.assertTrue(any("sblocco mancante" in w for w in warns))

    def test_missing_sblocco_dash_variants(self):
        for placeholder in ("-", "—", "N/A", "?"):
            b = {"name": "x", "sblocco": placeholder, "owner": "Anna", "da_quanto_days": 5}
            warns = rb._audit_block(b)
            self.assertTrue(any("sblocco mancante" in w for w in warns), f"failed for {placeholder!r}")

    def test_mattia_escalation_at_28_days(self):
        b = {"name": "x", "sblocco": "ok", "owner": "Mattia", "da_quanto_days": 28}
        warns = rb._audit_block(b)
        self.assertTrue(any("ESCALATION" in w for w in warns))

    def test_mattia_below_28_no_escalation(self):
        b = {"name": "x", "sblocco": "ok", "owner": "Mattia", "da_quanto_days": 14}
        warns = rb._audit_block(b)
        self.assertFalse(any("ESCALATION" in w for w in warns))

    def test_other_owner_no_escalation(self):
        b = {"name": "x", "sblocco": "ok", "owner": "Anna", "da_quanto_days": 60}
        warns = rb._audit_block(b)
        self.assertFalse(any("ESCALATION" in w for w in warns))


class TestDriftAudit(unittest.TestCase):

    def test_block_mentioned_is_not_ghost(self):
        blocks = [{"name": "Pricing pivot", "da_quanto_days": 30}]
        session = "Discusso il pricing pivot ieri con Marco"
        out = rb._drift_audit(blocks, session)
        # Each >=4-char token in the name is counted independently:
        # "pricing" + "pivot" → 2 hits when both appear in session.
        self.assertEqual(out["Pricing pivot"]["hit_count"], 2)
        self.assertFalse(out["Pricing pivot"]["is_ghost"])

    def test_block_not_mentioned_is_ghost_when_old(self):
        blocks = [{"name": "Phantom Foo", "da_quanto_days": 30}]
        out = rb._drift_audit(blocks, "totally unrelated content")
        self.assertEqual(out["Phantom Foo"]["hit_count"], 0)
        self.assertTrue(out["Phantom Foo"]["is_ghost"])

    def test_recent_block_not_marked_ghost_even_without_hits(self):
        # da_quanto_days <= 7 → never ghost
        blocks = [{"name": "Just Started", "da_quanto_days": 5}]
        out = rb._drift_audit(blocks, "no mention")
        self.assertFalse(out["Just Started"]["is_ghost"])

    def test_block_with_only_short_words_skipped(self):
        # name with no >=4-letter tokens → no keywords → not ghost
        blocks = [{"name": "x y z", "da_quanto_days": 30}]
        out = rb._drift_audit(blocks, "anything")
        self.assertFalse(out["x y z"]["is_ghost"])
        self.assertEqual(out["x y z"]["hit_count"], 0)


# =============================================================================
# Public API — read_blocks + render_blocks_section (hermetic roadmap)
# =============================================================================

class TestReadBlocksHermetic(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="m5w_blocks_")
        self.dir = Path(self._tmp.name)
        self.roadmap = self.dir / "roadmap.md"
        self.sessioni = self.dir / "Sessioni"
        self.sessioni.mkdir()
        # Drop a session that mentions one block by name
        (self.sessioni / "session_2026-05-10.md").write_text(
            "Lavorato sul pricing pivot oggi, decision pendente."
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_blocks_with_fixture(self):
        self.roadmap.write_text(_TABLE_FIXTURE)
        with patch.object(rb, "_ROADMAP", self.roadmap), \
             patch.object(rb, "_SESSIONI", self.sessioni):
            # bust the @cached(ttl=60) wrapper
            blocks = rb.read_blocks(force=True)
        self.assertGreater(len(blocks), 0)
        # Each entry has expected keys
        for b in blocks:
            for key in ("name", "severity", "owner", "warnings", "drift_hits", "is_ghost"):
                self.assertIn(key, b)

    def test_read_blocks_empty_roadmap_returns_empty_list(self):
        self.roadmap.write_text("")
        with patch.object(rb, "_ROADMAP", self.roadmap), \
             patch.object(rb, "_SESSIONI", self.sessioni):
            self.assertEqual(rb.read_blocks(force=True), [])

    def test_load_recent_session_text_with_files(self):
        with patch.object(rb, "_SESSIONI", self.sessioni):
            text = rb._load_recent_session_text(n_sessions=5)
        self.assertIn("pricing pivot", text)

    def test_load_recent_session_text_missing_dir(self):
        ghost = self.dir / "no_such_dir"
        with patch.object(rb, "_SESSIONI", ghost):
            self.assertEqual(rb._load_recent_session_text(), "")


class TestRenderBlocksSection(unittest.TestCase):

    def test_render_with_explicit_blocks(self):
        blocks = [
            {
                "name": "Phantom Project", "da_quanto_raw": "cronico",
                "da_quanto_days": 999, "energia_bloccata": "30% MRR",
                "sblocco": "rifare flow entro 20 mag 2026", "owner": "Mattia",
                "severity": "P0", "deadline": "D+10",
                "warnings": ["ESCALATION: …"], "drift_hits": 0, "is_ghost": True,
            },
            {
                "name": "Mid Block", "da_quanto_raw": "3 sett",
                "da_quanto_days": 21, "energia_bloccata": "leads",
                "sblocco": "ping Marco", "owner": "Marco",
                "severity": "P1", "deadline": None,
                "warnings": [], "drift_hits": 3, "is_ghost": False,
            },
            {
                "name": "Recent Block", "da_quanto_raw": "5 giorni",
                "da_quanto_days": 5, "energia_bloccata": "small",
                "sblocco": "", "owner": "Anna",
                "severity": "info-lime", "deadline": None,
                "warnings": ["sblocco mancante — blocco senza uscita definita"],
                "drift_hits": 1, "is_ghost": False,
            },
        ]
        out = rb.render_blocks_section(blocks)
        self.assertIn("BLOCCHI VIVENTI", out)
        self.assertIn("Phantom Project", out)
        self.assertIn("Mid Block", out)
        self.assertIn("Recent Block", out)
        # Ghost flag present
        self.assertIn("fantasma", out)
        # Escalation footer
        self.assertIn("decision point", out)

    def test_render_empty_blocks_returns_placeholder(self):
        out = rb.render_blocks_section([])
        self.assertIn("nessun blocco trovato", out)

    def test_render_no_escalation_when_owner_not_mattia(self):
        blocks = [{
            "name": "x", "da_quanto_raw": "60 giorni", "da_quanto_days": 60,
            "energia_bloccata": "y", "sblocco": "z", "owner": "Marco",
            "severity": "P0", "deadline": None,
            "warnings": [], "drift_hits": 5, "is_ghost": False,
        }]
        out = rb.render_blocks_section(blocks)
        self.assertNotIn("decision point", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
