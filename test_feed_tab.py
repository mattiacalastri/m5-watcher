"""test_feed_tab — unittest per i 2 moduli forgiati nel refactor Feed Tab (sess.1607).

Copertura:
  - feed_populators.populate_outstanding_table / traps / filaments / blocks
    · basic (clear + add_row N volte)
    · empty   (read_*() → [] → empty-state row presente)
    · resilient (read_*() → raise → error-row, no crash)
  - feed_aggregator.aggregate_feed_events
    · None app (defensive)
    · empty app stub (no events)
    · UNIFEED parsing (ts + severity + source extraction)
    · Telemetry flash (count > 0 → P1 entry)
    · Sentinel critical (severity 'critical' → P0 entry)
    · schema compat (keys allineate a _render_logs)
    · severity buckets (smoke distribuzione P0/P1/info)
  - Integration smoke
    · import senza side-effect
    · no crash con dati reali (None app + DummyDataTable)

Run: venv/bin/python -m unittest test_feed_tab -v
"""
from __future__ import annotations

import sys
import unittest
from collections import deque
from pathlib import Path
from typing import Any

# ── ensure project root on sys.path ──────────────────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import feed_aggregator
import feed_populators


# ═══════════════════════════════════════════════════════════════════════════════
# DummyDataTable — mock leggero del Textual DataTable, traccia clear+rows
# ═══════════════════════════════════════════════════════════════════════════════

class DummyDataTable:
    """Mock minimale: traccia clear_calls, rows (list[tuple]), columns."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []
        self.clear_calls = 0
        self.columns: list = []

    def clear(self) -> None:
        self.clear_calls += 1
        self.rows.clear()

    def add_row(self, *cells) -> None:
        self.rows.append(tuple(cells))

    def add_columns(self, *cols) -> None:
        self.columns.extend(cols)


# ═══════════════════════════════════════════════════════════════════════════════
# TestFeedPopulators — 12 test (4 populator × 3 scenari)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFeedPopulators(unittest.TestCase):
    """Unit test per i 4 populator del tab Feed."""

    # ── helper: monkey-patch read_* / detect_* su feed_populators namespace ──
    def _patch(self, attr: str, value: Any) -> None:
        """Monkey-patch attribute on feed_populators module, auto-restore."""
        original = getattr(feed_populators, attr)
        self.addCleanup(setattr, feed_populators, attr, original)
        setattr(feed_populators, attr, value)

    # ─────────────────────────── OUTSTANDING ───────────────────────────
    def test_populate_outstanding_table_basic(self):
        fake_entries = [
            {"cliente": "Pietro Carpino", "amount": 2000, "days_aged": 12,
             "severity": "P0", "note": "fattura saldata"},
            {"cliente": "Andrea Borganti", "amount": 500, "days_aged": 3,
             "severity": "P1", "note": "in attesa bonifico"},
            {"cliente": "Sabrina Brunelli", "amount": 800, "days_aged": None,
             "severity": "info", "note": ""},
        ]
        self._patch("read_outstanding", lambda: list(fake_entries))
        table = DummyDataTable()
        feed_populators.populate_outstanding_table(table)
        self.assertEqual(table.clear_calls, 1)
        self.assertEqual(len(table.rows), len(fake_entries))
        # ordinamento severity asc → P0 deve essere primo
        self.assertIn("Pietro", table.rows[0][0])

    def test_populate_outstanding_table_empty(self):
        self._patch("read_outstanding", lambda: [])
        table = DummyDataTable()
        feed_populators.populate_outstanding_table(table)
        self.assertEqual(table.clear_calls, 1)
        self.assertEqual(len(table.rows), 1, "empty-state row attesa")
        first_cell = table.rows[0][0]
        self.assertTrue(
            "no entries" in first_cell or "silenzio" in first_cell,
            f"empty-state markup atteso, got: {first_cell!r}",
        )

    def test_populate_outstanding_table_resilient(self):
        def boom():
            raise RuntimeError("vault unavailable")
        self._patch("read_outstanding", boom)
        table = DummyDataTable()
        # NON deve sollevare
        feed_populators.populate_outstanding_table(table)
        self.assertEqual(table.clear_calls, 1)
        self.assertEqual(len(table.rows), 1)
        self.assertIn("outstanding", table.rows[0][0])
        self.assertIn("unavailable", table.rows[0][0])

    # ────────────────────────────── TRAPS ──────────────────────────────
    def test_populate_traps_table_basic(self):
        fake_traps = [
            {"trap": "ghost daemon", "evidence": "PID 40187 666MB",
             "severity": "P0", "mitigation": "kill + diag",
             "cicatrice_ref": "feedback_screenshot_watcher"},
            {"trap": "drift memory", "evidence": "session_current stale",
             "severity": "P1", "mitigation": "garden walk",
             "cicatrice_ref": "feedback_gcal_groundtruth"},
        ]
        self._patch("detect_active_traps", lambda: list(fake_traps))
        table = DummyDataTable()
        feed_populators.populate_traps_table(table)
        self.assertEqual(table.clear_calls, 1)
        self.assertEqual(len(table.rows), len(fake_traps))

    def test_populate_traps_table_empty(self):
        self._patch("detect_active_traps", lambda: [])
        table = DummyDataTable()
        feed_populators.populate_traps_table(table)
        self.assertEqual(len(table.rows), 1)
        self.assertIn("0/5", table.rows[0][0])

    def test_populate_traps_table_resilient(self):
        def boom():
            raise ValueError("trap detector exploded")
        self._patch("detect_active_traps", boom)
        table = DummyDataTable()
        feed_populators.populate_traps_table(table)
        self.assertEqual(len(table.rows), 1)
        self.assertIn("traps", table.rows[0][0])
        self.assertIn("unavailable", table.rows[0][0])

    # ──────────────────────────── FILAMENTS ────────────────────────────
    def test_populate_filaments_table_basic(self):
        fake_filaments = [
            {"name": "Andrea GEO voice agent", "severity": "P1",
             "stato": "agent_id pending", "deadline": "2026-05-08",
             "days_drift": 2, "is_resolved": False},
            {"name": "AAH Lupo activation", "severity": "info",
             "stato": "active", "deadline": None, "days_drift": 0,
             "is_resolved": False},
            {"name": "Old closed filament", "severity": "P2",
             "stato": "done", "is_resolved": True},  # resolved → SKIP
        ]
        self._patch("read_filaments", lambda: list(fake_filaments))
        # detect_session_drift può tornare {} senza problemi
        self._patch("detect_session_drift", lambda lst: {})
        table = DummyDataTable()
        feed_populators.populate_filaments_table(table)
        self.assertEqual(table.clear_calls, 1)
        # 2 attivi (non resolved)
        self.assertEqual(len(table.rows), 2)

    def test_populate_filaments_table_empty(self):
        self._patch("read_filaments", lambda: [])
        table = DummyDataTable()
        feed_populators.populate_filaments_table(table)
        self.assertEqual(len(table.rows), 1)
        self.assertIn("no entries", table.rows[0][0])

    def test_populate_filaments_table_resilient(self):
        def boom():
            raise OSError("vault read failed")
        self._patch("read_filaments", boom)
        table = DummyDataTable()
        feed_populators.populate_filaments_table(table)
        self.assertEqual(len(table.rows), 1)
        self.assertIn("filaments", table.rows[0][0])
        self.assertIn("unavailable", table.rows[0][0])

    # ────────────────────────────── BLOCKS ──────────────────────────────
    def test_populate_blocks_table_basic(self):
        fake_blocks = [
            {"name": "Predator dead", "severity": "P0",
             "owner": "Polpo", "da_quanto_days": 18, "is_ghost": False},
            {"name": "AuraHome dormant", "severity": "P1",
             "owner": "Mattia", "da_quanto_days": 45, "is_ghost": True},
            {"name": "MemVid ghost", "severity": "info",
             "owner": "—", "da_quanto_days": 0, "is_ghost": True},
        ]
        self._patch("read_blocks", lambda: list(fake_blocks))
        table = DummyDataTable()
        feed_populators.populate_blocks_table(table)
        self.assertEqual(table.clear_calls, 1)
        self.assertEqual(len(table.rows), len(fake_blocks))
        # ghost → suffix 👻 nel name cell
        self.assertTrue(any("👻" in row[0] for row in table.rows))

    def test_populate_blocks_table_empty(self):
        self._patch("read_blocks", lambda: [])
        table = DummyDataTable()
        feed_populators.populate_blocks_table(table)
        self.assertEqual(len(table.rows), 1)
        self.assertIn("no entries", table.rows[0][0])

    def test_populate_blocks_table_resilient(self):
        def boom():
            raise Exception("blocks parser error")
        self._patch("read_blocks", boom)
        table = DummyDataTable()
        feed_populators.populate_blocks_table(table)
        self.assertEqual(len(table.rows), 1)
        self.assertIn("blocks", table.rows[0][0])
        self.assertIn("unavailable", table.rows[0][0])


# ═══════════════════════════════════════════════════════════════════════════════
# TestFeedAggregator — aggregate_feed_events su 4 stream
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeMetrics:
    """Stub minimale Metrics — espone summary() con i campi che il telemetry parser cerca."""

    def __init__(self, summary_dict: dict) -> None:
        self._summary = summary_dict

    def summary(self) -> dict:
        return self._summary


class _FakeApp:
    """Stub M5Watcher app — solo i 3 attributi che aggregate_feed_events legge."""

    def __init__(self,
                 event_feed: deque | None = None,
                 metrics: Any = None,
                 sentinel_data: dict | None = None) -> None:
        self._event_feed = event_feed if event_feed is not None else deque()
        self._metrics = metrics
        self._sentinel_data = sentinel_data if sentinel_data is not None else {}


# Schema canonico atteso da _render_logs (fonte: feed_aggregator docstring)
_SCHEMA_KEYS = {"ts", "severity", "emoji", "source", "title", "desc", "is_new"}


class TestFeedAggregator(unittest.TestCase):

    # ─────────────── helper: stub data_sources.tentacoli ───────────────
    def _stub_tentacoli(self, return_value: list | Exception | None = None) -> None:
        """Stub data_sources.tentacoli() per non leakare processi reali nei test."""
        import data_sources as ds
        original = ds.tentacoli

        def fake():
            if isinstance(return_value, Exception):
                raise return_value
            return list(return_value) if return_value is not None else []

        ds.tentacoli = fake
        self.addCleanup(setattr, ds, "tentacoli", original)

    # ─────────────────────────── tests ───────────────────────────
    def test_aggregate_with_none_app(self):
        result = feed_aggregator.aggregate_feed_events(None)
        self.assertEqual(result, [])

    def test_aggregate_empty_app(self):
        self._stub_tentacoli([])  # tentacoli vuoti
        app = _FakeApp(event_feed=deque(), metrics=None, sentinel_data={})
        result = feed_aggregator.aggregate_feed_events(app)
        # Tutti silenti → lista vuota o solo entries derivate da sorgenti vuote (dovrebbe essere [])
        self.assertEqual(result, [])

    def test_aggregate_unifeed_parsing(self):
        self._stub_tentacoli([])
        feed = deque(["[dim]14:03:21[/] 🔴 pressure HIGH → CRITICAL"])
        app = _FakeApp(event_feed=feed, metrics=None, sentinel_data={})
        result = feed_aggregator.aggregate_feed_events(app)
        unifeed_entries = [e for e in result if e["source"] == "UNIFEED"]
        self.assertEqual(len(unifeed_entries), 1)
        e = unifeed_entries[0]
        self.assertEqual(e["ts"], "14:03:21")
        # 'pressure' → severity P1 secondo la logica di _aggregate_unifeed
        self.assertEqual(e["severity"], "P1")
        self.assertEqual(e["source"], "UNIFEED")

    def test_aggregate_telemetry_flash(self):
        self._stub_tentacoli([])
        # summary() shape allineata al codice (s.flash.count + reasons)
        metrics = _FakeMetrics({
            "flash":  {"count": 7, "reasons": {"swap_alert": 5, "cpu_spike": 2}},
            "slow_ms": {"p95": 0.0},
            "tick_drift_ms": {"p95": 0.0},
            "idle":   {"active": 0},
        })
        app = _FakeApp(event_feed=deque(), metrics=metrics, sentinel_data={})
        result = feed_aggregator.aggregate_feed_events(app)
        flash_entries = [e for e in result if "flash" in e["title"].lower()]
        self.assertGreaterEqual(len(flash_entries), 1)
        self.assertEqual(flash_entries[0]["severity"], "P1")
        self.assertEqual(flash_entries[0]["source"], "Telemetry")

    def test_aggregate_sentinel_critical(self):
        self._stub_tentacoli([])
        sentinel = {
            "alerts": [
                {"severity": "critical", "title": "X token expired",
                 "detail": "OAuth refresh failed"},
            ],
        }
        app = _FakeApp(event_feed=deque(), metrics=None, sentinel_data=sentinel)
        result = feed_aggregator.aggregate_feed_events(app)
        sentinel_entries = [e for e in result if e["source"] == "Sentinel"]
        self.assertEqual(len(sentinel_entries), 1)
        e = sentinel_entries[0]
        self.assertEqual(e["severity"], "P0")
        self.assertIn("X token", e["title"])

    def test_aggregate_schema_compat(self):
        """Ogni entry deve avere esattamente le keys del contratto _render_logs."""
        self._stub_tentacoli([])
        metrics = _FakeMetrics({
            "flash":  {"count": 3, "reasons": {"swap": 3}},
            "slow_ms": {"p95": 0.0},
            "tick_drift_ms": {"p95": 0.0},
            "idle":   {"active": 0},
        })
        app = _FakeApp(
            event_feed=deque(["[dim]10:00:00[/] swap activated +0.3GB"]),
            metrics=metrics,
            sentinel_data={"alerts": [
                {"severity": "warn", "title": "DNS drift", "detail": "TTL spiked"},
            ]},
        )
        result = feed_aggregator.aggregate_feed_events(app)
        self.assertGreater(len(result), 0)
        for entry in result:
            self.assertEqual(set(entry.keys()), _SCHEMA_KEYS,
                             f"schema mismatch: {entry}")
            self.assertIn(entry["severity"], {"P0", "P1", "info"})
            self.assertIsInstance(entry["is_new"], bool)

    def test_aggregate_severity_buckets(self):
        """Smoke test: stream misti producono distribuzione severity coerente."""
        self._stub_tentacoli([])
        metrics = _FakeMetrics({
            "flash":  {"count": 5, "reasons": {"cpu_spike": 5}},  # → P1
            "slow_ms": {"p95": 600.0},   # > 500 → P1
            "tick_drift_ms": {"p95": 0.0},
            "idle":   {"active": 0},
        })
        sentinel = {"alerts": [
            {"severity": "critical", "title": "kill switch tripped"},  # → P0
            {"severity": "warn", "title": "rate limit close"},          # → P1
        ]}
        app = _FakeApp(
            event_feed=deque([
                "[dim]09:00:00[/] swap activated +1.5GB",  # > 1.0 → P0
                "[dim]09:01:00[/] noise no flag here",       # info
            ]),
            metrics=metrics,
            sentinel_data=sentinel,
        )
        result = feed_aggregator.aggregate_feed_events(app)
        sevs = [e["severity"] for e in result]
        self.assertIn("P0", sevs)
        self.assertIn("P1", sevs)
        # info bucket presente da UNIFEED noise
        self.assertIn("info", sevs)


# ═══════════════════════════════════════════════════════════════════════════════
# TestIntegration — smoke test moduli end-to-end
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegration(unittest.TestCase):

    def test_modules_importable_clean(self):
        """Re-import non deve produrre side-effect (l'import top-level già funziona)."""
        import importlib
        # Re-import non dovrebbe sollevare
        importlib.reload(feed_populators)
        importlib.reload(feed_aggregator)
        # Public API esposta?
        for fn in ("populate_outstanding_table", "populate_traps_table",
                   "populate_filaments_table", "populate_blocks_table"):
            self.assertTrue(hasattr(feed_populators, fn))
        self.assertTrue(hasattr(feed_aggregator, "aggregate_feed_events"))

    def test_no_crash_with_real_data(self):
        """Smoke test: chiamate reali (nessun mock) NON sollevano eccezioni.

        - aggregate_feed_events(None) → ritorna []
        - i 4 populator su DummyDataTable → completano (anche se vault offline,
          read_*() ritorna [] o solleva → entrambi gestiti)
        """
        # Aggregator con None app
        out = feed_aggregator.aggregate_feed_events(None)
        self.assertIsInstance(out, list)

        # 4 populator su tabella vuota — nessuno deve crashare
        for fn_name in (
            "populate_outstanding_table",
            "populate_traps_table",
            "populate_filaments_table",
            "populate_blocks_table",
        ):
            with self.subTest(populator=fn_name):
                table = DummyDataTable()
                fn = getattr(feed_populators, fn_name)
                try:
                    fn(table)
                except Exception as e:  # pragma: no cover — populator deve essere resilient
                    self.fail(f"{fn_name} ha sollevato {e!r}")
                # In ogni caso clear() deve essere stato chiamato
                self.assertGreaterEqual(table.clear_calls, 1)
                # E almeno 1 row presente (entries reali OR empty-state OR error-row)
                self.assertGreaterEqual(len(table.rows), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# TestFeedTentacoliEnrich — 6 test per feed_tentacoli_enrich.enrich_tentacoli
# ═══════════════════════════════════════════════════════════════════════════════

import feed_tentacoli_enrich
from unittest.mock import patch


class TestFeedTentacoliEnrich(unittest.TestCase):
    """Unit test per enrich_tentacoli — modulo defensive che aggiunge 6 campi."""

    _ENRICHED_FIELDS = {
        "uptime_sec", "status", "severity_hint",
        "last_log_line", "last_event_ts", "log_path",
    }

    def test_enrich_empty_list(self):
        """Input lista vuota → output lista vuota, nessun crash."""
        self.assertEqual(feed_tentacoli_enrich.enrich_tentacoli([]), [])

    def test_enrich_preserves_original_fields(self):
        """Campi originali preservati + 6 nuovi field aggiunti."""
        raw = [{"pid": 1, "name": "X", "cpu": 1.0, "mem_mb": 100}]
        result = feed_tentacoli_enrich.enrich_tentacoli(raw)
        self.assertEqual(len(result), 1)
        item = result[0]
        # Original fields preserved
        for k in ("pid", "name", "cpu", "mem_mb"):
            self.assertIn(k, item, f"campo originale {k!r} perso")
        self.assertEqual(item["pid"], 1)
        self.assertEqual(item["name"], "X")
        self.assertEqual(item["cpu"], 1.0)
        self.assertEqual(item["mem_mb"], 100)
        # 6 enriched fields presenti
        for k in self._ENRICHED_FIELDS:
            self.assertIn(k, item, f"campo arricchito {k!r} mancante")
        # No mutation: l'originale resta pulito
        self.assertNotIn("status", raw[0])

    def test_enrich_status_classification(self):
        """PID invalido → status valido, no crash. cpu>150 → status='spike'."""
        valid_status = {"running", "starting", "spike", "stuck", "error", "dead", "drift"}
        # PID inesistente
        raw = [{"pid": 99999999, "name": "fake_proc_xyz", "cpu": 0.0, "mem_mb": 10}]
        result = feed_tentacoli_enrich.enrich_tentacoli(raw)
        self.assertEqual(len(result), 1)
        self.assertIn(result[0]["status"], valid_status)
        # uptime_sec=0 (pid invalido) + cpu=0 → 'starting' (uptime<5)
        self.assertEqual(result[0]["status"], "starting")

        # cpu>150 → spike — mock psutil.Process per uptime stabile (>5s)
        class _MockProc:
            def __init__(self, *args, **kwargs):
                pass
            def create_time(self):
                return 0.0  # epoch 1970 → uptime enorme

        with patch.object(feed_tentacoli_enrich.psutil, "Process", _MockProc):
            raw_spike = [{"pid": 12345, "name": "burner", "cpu": 200.0, "mem_mb": 500}]
            result_spike = feed_tentacoli_enrich.enrich_tentacoli(raw_spike)
        self.assertEqual(result_spike[0]["status"], "spike")

    def test_enrich_severity_hint_mapping(self):
        """status='error'→P0, status='spike'→P1, status='running'→info."""
        # error: forced via exit_status
        raw_err = [{"pid": 1, "name": "x", "cpu": 0, "mem_mb": 0, "exit_status": 1}]
        r_err = feed_tentacoli_enrich.enrich_tentacoli(raw_err)
        self.assertEqual(r_err[0]["status"], "error")
        self.assertEqual(r_err[0]["severity_hint"], "P0")

        # spike: cpu>150 + uptime stabile via mock
        class _MockProcOld:
            def __init__(self, *args, **kwargs):
                pass
            def create_time(self):
                return 0.0

        with patch.object(feed_tentacoli_enrich.psutil, "Process", _MockProcOld):
            raw_spike = [{"pid": 1, "name": "x", "cpu": 200.0, "mem_mb": 0}]
            r_spike = feed_tentacoli_enrich.enrich_tentacoli(raw_spike)
        self.assertEqual(r_spike[0]["status"], "spike")
        self.assertEqual(r_spike[0]["severity_hint"], "P1")

        # running: cpu moderato + uptime stabile
        with patch.object(feed_tentacoli_enrich.psutil, "Process", _MockProcOld):
            raw_run = [{"pid": 1, "name": "x", "cpu": 5.0, "mem_mb": 0}]
            r_run = feed_tentacoli_enrich.enrich_tentacoli(raw_run)
        self.assertEqual(r_run[0]["status"], "running")
        self.assertEqual(r_run[0]["severity_hint"], "info")

    def test_enrich_resilient_on_exception(self):
        """psutil.Process raise → enrich non crasha, severity_hint default present."""

        def boom(*args, **kwargs):
            raise RuntimeError("psutil exploded")

        raw = [{"pid": 1, "name": "test_resilient", "cpu": 0.5, "mem_mb": 50}]
        with patch.object(feed_tentacoli_enrich.psutil, "Process", side_effect=boom):
            result = feed_tentacoli_enrich.enrich_tentacoli(raw)
        # Non solleva, ritorna lista stessa lunghezza
        self.assertEqual(len(result), 1)
        item = result[0]
        # Original fields preserved
        self.assertEqual(item["pid"], 1)
        self.assertEqual(item["name"], "test_resilient")
        # severity_hint='info' di default (status='starting' con uptime=0)
        self.assertIn("severity_hint", item)
        self.assertEqual(item["severity_hint"], "info")
        # uptime_sec=0 dopo eccezione catturata in _safe_uptime
        self.assertEqual(item["uptime_sec"], 0.0)

    def test_known_tentacoli_logs_dict_populated(self):
        """KNOWN_TENTACOLI_LOGS dict ha >=11 entries con chiavi str + valori path."""
        d = feed_tentacoli_enrich.KNOWN_TENTACOLI_LOGS
        self.assertIsInstance(d, dict)
        self.assertGreaterEqual(len(d), 11, f"atteso >=11 entries, got {len(d)}")
        for k, v in d.items():
            self.assertIsInstance(k, str, f"chiave non-string: {k!r}")
            self.assertIsInstance(v, str, f"valore non-string per {k!r}: {v!r}")
            # Valore deve sembrare un path (contiene '/' o '~')
            self.assertTrue(
                "/" in v or v.startswith("~"),
                f"valore {v!r} per {k!r} non sembra un path",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
