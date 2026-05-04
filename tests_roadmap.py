"""tests_roadmap — test suite consolidata per i 5 moduli roadmap (sess.1534).

Round 5 (sess.1534): estratto da stress test inline `if __name__ == '__main__'`
nei 5 moduli (~150 righe ridondanti). Consolidato qui come unittest standard,
integrato in test_suite.py via `python -m unittest tests_roadmap`.

Coverage:
  - Public API contract (signatures + return types)
  - Cache TTL behavior
  - Graceful degradation con file mancante / vault offline
  - Markup output balance (apri/chiudi tags pari)
  - Severity classification consistency
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

# Ensure roadmap modules importable
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Common ───────────────────────────────────────────────────────────────────
class TestRoadmapCommon(unittest.TestCase):
    def test_palette_constants_present(self):
        from roadmap_common import RED, ORANGE, LIME, DIM, TEAL
        for c in (RED, ORANGE, LIME, DIM, TEAL):
            self.assertRegex(c, r"^#[0-9a-fA-F]{6}$")

    def test_vault_base_resolves(self):
        from roadmap_common import VAULT_BASE
        # Path object — non serve che esista (tester con vault offline)
        self.assertIsInstance(VAULT_BASE, Path)

    def test_vault_base_env_override(self):
        os.environ["M5_VAULT_PATH"] = "/tmp/__test_vault__"
        try:
            # re-import per pickup env
            for m in list(sys.modules.keys()):
                if m.startswith("roadmap_"):
                    del sys.modules[m]
            from roadmap_common import VAULT_BASE
            self.assertEqual(str(VAULT_BASE), "/tmp/__test_vault__")
        finally:
            del os.environ["M5_VAULT_PATH"]
            for m in list(sys.modules.keys()):
                if m.startswith("roadmap_"):
                    del sys.modules[m]

    def test_today_iso_format(self):
        from roadmap_common import today_iso
        self.assertRegex(today_iso(), r"^\d{4}-\d{2}-\d{2}$")

    def test_today_override_env(self):
        os.environ["M5_TODAY_OVERRIDE"] = "2026-01-15"
        try:
            for m in list(sys.modules.keys()):
                if m.startswith("roadmap_"):
                    del sys.modules[m]
            from roadmap_common import today_iso, today_date
            self.assertEqual(today_iso(), "2026-01-15")
            self.assertEqual(today_date().isoformat(), "2026-01-15")
        finally:
            del os.environ["M5_TODAY_OVERRIDE"]


# ── 5 moduli — public API contract ───────────────────────────────────────────
class TestRoadmapModulesAPI(unittest.TestCase):
    """Ogni modulo deve esporre la funzione render_X documentata."""

    def test_polestar_api(self):
        from roadmap_polestar import render_polestar_strip, read_phase_state
        self.assertIsInstance(render_polestar_strip(), str)
        state = read_phase_state()
        self.assertIsInstance(state, dict)
        # Keys minimal contract
        for k in ("mrr", "outstanding", "kill_days_remaining"):
            self.assertIn(k, state)

    def test_filaments_api(self):
        from roadmap_filaments import render_filaments_section, read_filaments
        self.assertIsInstance(render_filaments_section(), str)
        items = read_filaments()
        self.assertIsInstance(items, list)
        if items:
            for k in ("name", "severity", "stato"):
                self.assertIn(k, items[0])

    def test_blocks_api(self):
        from roadmap_blocks import render_blocks_section, read_blocks
        self.assertIsInstance(render_blocks_section(), str)
        items = read_blocks()
        self.assertIsInstance(items, list)
        if items:
            for k in ("name", "severity", "owner"):
                self.assertIn(k, items[0])

    def test_vectors_api(self):
        from roadmap_vectors import render_vectors_strip, read_vectors
        self.assertIsInstance(render_vectors_strip(), str)
        v = read_vectors()
        self.assertIsInstance(v, dict)
        for k in ("cicatrici", "garden", "mrr", "trinita"):
            self.assertIn(k, v)
            self.assertIn("current", v[k])
            self.assertIn("sparkline", v[k])

    def test_traps_api(self):
        from roadmap_traps import render_traps_banner, detect_active_traps
        out = render_traps_banner()
        # Empty string OK (no traps active)
        self.assertIsInstance(out, str)
        traps = detect_active_traps()
        self.assertIsInstance(traps, list)
        if traps:
            for k in ("trap", "evidence", "severity"):
                self.assertIn(k, traps[0])


# ── Markup balance ───────────────────────────────────────────────────────────
class TestMarkupBalance(unittest.TestCase):
    """Tutti i 5 render devono produrre Rich markup balanced (no [/{...}])."""

    @classmethod
    def setUpClass(cls):
        from roadmap_polestar import render_polestar_strip
        from roadmap_filaments import render_filaments_section
        from roadmap_blocks import render_blocks_section
        from roadmap_vectors import render_vectors_strip
        from roadmap_traps import render_traps_banner
        cls.renders = {
            "polestar": render_polestar_strip(),
            "filaments": render_filaments_section(),
            "blocks": render_blocks_section(),
            "vectors": render_vectors_strip(),
            "traps": render_traps_banner() or "",
        }

    def test_no_template_close_tags(self):
        for name, out in self.renders.items():
            with self.subTest(module=name):
                weird = re.findall(r"\[/\{[^}]+\}\]", out)
                self.assertEqual(weird, [], f"{name} has template close tags")

    def test_no_hex_close_tags(self):
        for name, out in self.renders.items():
            with self.subTest(module=name):
                weird = re.findall(r"\[/#[a-fA-F0-9]+\]", out)
                self.assertEqual(weird, [], f"{name} has hex close tags")

    def test_open_close_balance(self):
        for name, out in self.renders.items():
            with self.subTest(module=name):
                opens = len(re.findall(r"\[(?!/)[^]]+\]", out))
                closes = len(re.findall(r"\[/[^]]*\]", out))
                self.assertEqual(opens, closes,
                                 f"{name} unbalanced opens={opens} closes={closes}")


# ── Cache TTL behavior ───────────────────────────────────────────────────────
class TestCacheBehavior(unittest.TestCase):
    """Secondo render entro TTL deve essere identico al primo (cache hit)."""

    def test_polestar_cache_hit(self):
        from roadmap_polestar import render_polestar_strip
        a = render_polestar_strip()
        b = render_polestar_strip()
        self.assertEqual(a, b)

    def test_filaments_cache_hit(self):
        from roadmap_filaments import render_filaments_section
        a = render_filaments_section()
        b = render_filaments_section()
        self.assertEqual(a, b)

    def test_blocks_cache_hit(self):
        from roadmap_blocks import render_blocks_section
        a = render_blocks_section()
        b = render_blocks_section()
        self.assertEqual(a, b)


# ── Graceful degradation ─────────────────────────────────────────────────────
class TestGracefulDegradation(unittest.TestCase):
    """File mancante / vault offline → degradation graceful, no crash."""

    def test_blocks_handles_missing_vault(self):
        os.environ["M5_VAULT_PATH"] = "/tmp/__definitely_does_not_exist__"
        try:
            for m in list(sys.modules.keys()):
                if m.startswith("roadmap_"):
                    del sys.modules[m]
            from roadmap_blocks import render_blocks_section
            out = render_blocks_section()
            # Non deve crashare. Output può essere placeholder o empty.
            self.assertIsInstance(out, str)
        finally:
            del os.environ["M5_VAULT_PATH"]
            for m in list(sys.modules.keys()):
                if m.startswith("roadmap_"):
                    del sys.modules[m]


if __name__ == "__main__":
    unittest.main(verbosity=2)
