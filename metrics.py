"""🐙 m5-watcher · telemetry spine — sess.1224 (Mattia: telemetry round).

Pattern identificato (audit pre-spine): i 3 round di ottimizzazione precedenti
(sess.1494, sess.1508 r2, sess.1508 r3) hanno introdotto:
  - lru_cache su _rainbow_hex (claim hit ratio ~99% post-warmup)
  - diff cache _render_cache su Static.update (claim ~70% skip)
  - idle freeze rainbow + critical flash (claim coerenza dato↔motion)

NESSUNA delle tre era misurabile: claim non verificabili senza spine.

Questo modulo è la spine. Misura davvero, scrive su disco, espone debug panel.

Surface: TUI + JSONL append-only su ~/.m5-watcher/metrics.jsonl.
Stack: stdlib only (deque, logging.handlers, statistics, json) + psutil (già dep).
Render: Rich markup via primitive polpo_charts.

Verità sopra estetica: dati prima, design dopo.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import statistics
import sys
import time
from collections import deque
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

try:
    import psutil  # già dep app.py
except ImportError:  # graceful: spine non blocca app se psutil mancasse
    psutil = None  # type: ignore

from polpo_charts import (
    BG,
    DIM,
    ELEC_BLUE,
    FG,
    HOT_PINK,
    LIME,
    ORANGE,
    TEAL,
    empty_state,
    fmt_int_eu,
    gb,
    pct_bar,
    pct_color,
    sparkline,
)

__all__ = [
    "Metrics",
    "setup_logger",
    "cache_info_dict",
    "render_debug_panel",
    "JsonlWriter",
    "LOG_PATH",
    "JSONL_PATH",
]


# ── Paths ────────────────────────────────────────────────────────────────────
def _default_log_path() -> Path:
    """macOS → ~/Library/Logs/m5-watcher.log, altrove → ~/.local/state/m5-watcher.log."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "m5-watcher.log"
    return Path.home() / ".local" / "state" / "m5-watcher.log"


def _default_jsonl_path() -> Path:
    return Path.home() / ".m5-watcher" / "metrics.jsonl"


LOG_PATH:   Path = _default_log_path()
JSONL_PATH: Path = _default_jsonl_path()


# ── Metrics ──────────────────────────────────────────────────────────────────
class Metrics:
    """Telemetry collector — singleton-like (un'istanza per app).

    Tutti i counter sono additivi (mai decrementati). I deque sono ring buffer
    a finestra fissa: leggono recency, perdono storia profonda. Per long-term
    storage usa JsonlWriter.flush_metrics(m) periodico.

    Convenzioni:
      - frame_ms / slow_ms: durata refresh in millisecondi (perf_counter delta).
      - cache_hits/misses: diff manuale tra cache_info() snapshot consecutivi.
      - tick_drift_ms: scarto tra atteso (set_interval) e reale (now - last).
      - rss_mb: RSS proprio processo via psutil.Process(os.getpid()).
    """

    __slots__ = (
        "frame_ms", "slow_ms",
        "cache_hits", "cache_misses",
        "idle_enters", "idle_exits",
        "flash_count", "flash_reasons",
        "tick_drift_ms", "rss_mb",
        "_last_tick_perf", "_started_at",
        "_proc",
    )

    def __init__(self) -> None:
        self.frame_ms:      deque[float]    = deque(maxlen=120)
        self.slow_ms:       deque[float]    = deque(maxlen=60)
        self.cache_hits:    int             = 0
        self.cache_misses:  int             = 0
        self.idle_enters:   int             = 0
        self.idle_exits:    int             = 0
        self.flash_count:   int             = 0
        self.flash_reasons: dict[str, int]  = {}
        self.tick_drift_ms: deque[float]    = deque(maxlen=60)
        self.rss_mb:        deque[float]    = deque(maxlen=60)
        self._last_tick_perf: float | None  = None
        self._started_at:   float           = time.time()
        self._proc                          = None
        if psutil is not None:
            try:
                self._proc = psutil.Process(os.getpid())
            except Exception:
                self._proc = None

    # ── Recorders ────────────────────────────────────────────────────────────
    def record_frame(self, ms: float) -> None:
        """Durata _refresh_fast in ms (perf_counter delta * 1000)."""
        self.frame_ms.append(float(ms))

    def record_slow(self, ms: float) -> None:
        """Durata _refresh_slow in ms."""
        self.slow_ms.append(float(ms))

    def cache_hit(self, n: int = 1) -> None:
        self.cache_hits += int(n)

    def cache_miss(self, n: int = 1) -> None:
        self.cache_misses += int(n)

    def idle_enter(self) -> None:
        """Round 3 idle freeze: motion congelata → contatore enter."""
        self.idle_enters += 1

    def idle_exit(self) -> None:
        """Round 3 idle freeze: motion riavviata."""
        self.idle_exits += 1

    def flash(self, reason: str = "") -> None:
        """Round 3 critical flash trigger. Conta totale + breakdown reason."""
        self.flash_count += 1
        key = reason.strip() or "unspecified"
        self.flash_reasons[key] = self.flash_reasons.get(key, 0) + 1

    def record_tick_drift(self, ms: float) -> None:
        """Scarto temporale set_interval — quanto si è scostato dal periodo atteso."""
        self.tick_drift_ms.append(float(ms))

    def record_rss(self, mb: float | None = None) -> None:
        """RSS proprio in MB. Se mb=None lo legge via psutil."""
        if mb is None:
            if self._proc is None:
                return
            try:
                mb = self._proc.memory_info().rss / 1024 ** 2
            except Exception:
                return
        self.rss_mb.append(float(mb))

    # ── Auto-helpers ─────────────────────────────────────────────────────────
    def auto_tick_drift(self, expected_period_s: float) -> None:
        """Da chiamare INIZIO _refresh_fast. Se chiamato 2× consecutive,
        misura quanto la 2ª chiamata è arrivata in ritardo rispetto a periodo
        atteso. Drift = (now - last) - expected_period_s, in ms.
        """
        now = time.perf_counter()
        if self._last_tick_perf is not None:
            elapsed = now - self._last_tick_perf
            drift_ms = (elapsed - expected_period_s) * 1000.0
            self.record_tick_drift(drift_ms)
        self._last_tick_perf = now

    # ── Reporters ────────────────────────────────────────────────────────────
    @staticmethod
    def _percentile(values: Iterable[float], p: float) -> float:
        """Percentile robusto. Se vuoto → 0.0. p in [0,100]."""
        vals = sorted(values)
        if not vals:
            return 0.0
        if len(vals) == 1:
            return vals[0]
        # statistics.quantiles richiede >=2; fallback manuale
        try:
            # 100 quantiles = percentili interi, n=100 → idx p-1
            idx = max(0, min(99, int(round(p)) - 1))
            qs = statistics.quantiles(vals, n=100, method="inclusive")
            return qs[idx]
        except Exception:
            k = max(0, min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1)))))
            return vals[k]

    def hit_ratio(self) -> float:
        """Cache hit ratio in [0,1]. 0.0 se nessun sample (no division-by-zero)."""
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0

    def summary(self) -> dict[str, Any]:
        """Snapshot aggregato per dashboard / JSONL / debug panel.

        Tutti i numeri sono nativi (no numpy). Ready-to-serialize.
        """
        rss_last = self.rss_mb[-1] if self.rss_mb else 0.0
        return {
            "ts":            time.time(),
            "uptime_s":      round(time.time() - self._started_at, 1),
            "frame_ms": {
                "p50": round(self._percentile(self.frame_ms, 50), 2),
                "p95": round(self._percentile(self.frame_ms, 95), 2),
                "p99": round(self._percentile(self.frame_ms, 99), 2),
                "n":   len(self.frame_ms),
            },
            "slow_ms": {
                "p50": round(self._percentile(self.slow_ms, 50), 2),
                "p95": round(self._percentile(self.slow_ms, 95), 2),
                "p99": round(self._percentile(self.slow_ms, 99), 2),
                "n":   len(self.slow_ms),
            },
            "cache": {
                "hits":   self.cache_hits,
                "misses": self.cache_misses,
                "ratio":  round(self.hit_ratio(), 4),
            },
            "idle": {
                "enters": self.idle_enters,
                "exits":  self.idle_exits,
                "active": self.idle_enters - self.idle_exits,
            },
            "flash": {
                "count":   self.flash_count,
                "reasons": dict(self.flash_reasons),
            },
            "tick_drift_ms": {
                "p50": round(self._percentile(self.tick_drift_ms, 50), 2),
                "p95": round(self._percentile(self.tick_drift_ms, 95), 2),
                "n":   len(self.tick_drift_ms),
            },
            "rss_mb": {
                "last": round(rss_last, 1),
                "p95":  round(self._percentile(self.rss_mb, 95), 1),
                "n":    len(self.rss_mb),
            },
        }

    def to_jsonl_line(self) -> str:
        """Snapshot serializzato come 1 riga JSON (newline-terminated)."""
        return json.dumps(self.summary(), separators=(",", ":")) + "\n"


# ── Logger ───────────────────────────────────────────────────────────────────
def setup_logger(name: str = "m5-watcher", level: int = logging.INFO) -> logging.Logger:
    """Logger root del TUI con RotatingFileHandler 1MB × 3 backups.

    Scrive su:
      - macOS:  ~/Library/Logs/m5-watcher.log
      - linux:  ~/.local/state/m5-watcher.log

    Idempotente: chiamare 2× non duplica handler.
    """
    logger = logging.getLogger(name)
    if getattr(logger, "_m5w_configured", False):
        return logger

    logger.setLevel(level)
    log_path = LOG_PATH
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fallback hard: stderr only
        pass

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # File handler con rotation
    try:
        fh = RotatingFileHandler(
            log_path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        fh.setLevel(level)
        logger.addHandler(fh)
    except Exception:
        # Se filesystem read-only / permessi → stderr only
        pass

    # Boot info riga (utile per ground-truth troubleshooting)
    try:
        logger.info(
            "m5-watcher logger online | platform=%s python=%s pid=%d log=%s",
            platform.platform(), platform.python_version(), os.getpid(), log_path,
        )
    except Exception:
        pass

    logger._m5w_configured = True  # type: ignore[attr-defined]
    return logger


# ── Cache info ───────────────────────────────────────────────────────────────
def cache_info_dict(lru_cached_func: Any) -> dict[str, Any]:
    """Espone functools.lru_cache CacheInfo come dict serializzabile.

    Returns:
        {"hits", "misses", "maxsize", "currsize", "ratio"} — ratio in [0,1].
        {} se la funzione non ha cache_info() (no @lru_cache).
    """
    try:
        info = lru_cached_func.cache_info()
    except AttributeError:
        return {}
    total = info.hits + info.misses
    ratio = info.hits / total if total > 0 else 0.0
    return {
        "hits":     info.hits,
        "misses":   info.misses,
        "maxsize":  info.maxsize,
        "currsize": info.currsize,
        "ratio":    round(ratio, 4),
    }


# ── Health thresholds (sess.1564 redesign) ───────────────────────────────────
# Soglie semantiche per il pannello HEALTH. ✓ sotto WARN, ⚠ tra WARN e CRIT, ✗ oltre CRIT.
FRAME_P95_WARN_MS = 80.0
FRAME_P95_CRIT_MS = 200.0
SLOW_P95_WARN_MS  = 300.0
SLOW_P95_CRIT_MS  = 800.0
CACHE_RATIO_WARN  = 0.80   # ≥ 80% sano
CACHE_RATIO_CRIT  = 0.50   # < 50% rotto
MEM_WARN_MB       = 200.0
MEM_CRIT_MB       = 400.0
DRIFT_P95_WARN_MS = 200.0
DRIFT_P95_CRIT_MS = 500.0
FLASH_WARN_COUNT  = 1
FLASH_CRIT_COUNT  = 5

_GLYPH_OK   = "✓"
_GLYPH_WARN = "⚠"
_GLYPH_CRIT = "✗"
_GLYPH_NONE = "—"


def _status_glyph(value: float | None, *, warn: float, crit: float,
                  lower_is_better: bool = True) -> str:
    """Return ✓/⚠/✗/— based on thresholds."""
    if value is None:
        return _GLYPH_NONE
    if lower_is_better:
        if value >= crit:
            return _GLYPH_CRIT
        if value >= warn:
            return _GLYPH_WARN
        return _GLYPH_OK
    if value <= crit:
        return _GLYPH_CRIT
    if value <= warn:
        return _GLYPH_WARN
    return _GLYPH_OK


def _glyph_color(glyph: str) -> str:
    """Map status glyph to palette color."""
    return {
        _GLYPH_OK:   LIME,
        _GLYPH_WARN: ORANGE,
        _GLYPH_CRIT: HOT_PINK,
        _GLYPH_NONE: DIM,
    }.get(glyph, DIM)


def _fmt_uptime(s: float) -> str:
    """184 → '3m04s', 3661 → '1h01m', 42 → '42s'."""
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    h, rem = divmod(s, 3600)
    return f"{h}h{rem // 60:02d}m"


def _fmt_int_compact(n: int) -> str:
    """182543 → '182.5k', 3717 → '3.7k', 280 → '280'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _health_cell(label: str, glyph: str, value: str, *, width: int = 26) -> str:
    """Cella HEALTH: 'frame  ✓  55ms p95   ' larga `width` caratteri visibili."""
    color = _glyph_color(glyph)
    body = f"{label:<6} [{color}]{glyph}[/] {value}"
    visible_len = len(label) + 1 + 1 + 1 + len(value)  # tag invisibili
    pad = max(0, width - visible_len)
    return body + " " * pad


# ── Debug panel render ───────────────────────────────────────────────────────
def render_debug_panel(m: Metrics, lru_funcs: list | None = None) -> str:
    """Pannello debug Rich-markup per Tab "🔬 Debug" (redesign sess.1564).

    Layout:
      • status bar    — uptime + ts + sample counts
      • HEALTH        — 6 KPI in griglia 3×2 con glifo ✓/⚠/✗
      • FRAME / SLOW  — sparkline + percentili separati da ·
      • CACHE         — manual + lru introspection, label allineate
      • MEM           — sparkline + last/p95
      • FLASH         — solo se attivo, evidenziato
      • IDLE / DRIFT  — collassati a una riga quando vuoti

    Args:
        m: Metrics collector.
        lru_funcs: lista opzionale di funzioni @lru_cache da introspezionare
                   (es. [_rainbow_hex]). Mostrate come "<name> hit/miss ratio".
    """
    s = m.summary()
    lines: list[str] = []

    # ── Status bar (uptime + ts + sample counts) ─────────────────────────
    n_frame = s["frame_ms"]["n"]
    n_slow  = s["slow_ms"]["n"]
    lines.append(
        f"[{DIM}]uptime[/] {_fmt_uptime(s['uptime_s'])}   "
        f"[{DIM}]ts[/] {time.strftime('%H:%M:%S')}   "
        f"[{DIM}]samples[/] {n_frame}f / {n_slow}s"
    )
    lines.append("")

    # ── HEALTH (3×2 griglia con glifi semantici) ─────────────────────────
    frame_p95 = s["frame_ms"]["p95"] if n_frame else None
    slow_p95  = s["slow_ms"]["p95"]  if n_slow  else None
    cache_r   = s["cache"]["ratio"]
    cache_total = s["cache"]["hits"] + s["cache"]["misses"]
    mem_last  = s["rss_mb"]["last"]  if s["rss_mb"]["n"] else None
    drift_p95 = s["tick_drift_ms"]["p95"] if s["tick_drift_ms"]["n"] else None
    flash_n   = s["flash"]["count"]

    g_frame = _status_glyph(frame_p95, warn=FRAME_P95_WARN_MS, crit=FRAME_P95_CRIT_MS)
    g_slow  = _status_glyph(slow_p95,  warn=SLOW_P95_WARN_MS,  crit=SLOW_P95_CRIT_MS)
    g_cache = (_status_glyph(1.0 - cache_r, warn=1.0 - CACHE_RATIO_WARN,
                             crit=1.0 - CACHE_RATIO_CRIT) if cache_total else _GLYPH_NONE)
    g_mem   = _status_glyph(mem_last, warn=MEM_WARN_MB, crit=MEM_CRIT_MB)
    g_drift = _status_glyph(abs(drift_p95) if drift_p95 is not None else None,
                            warn=DRIFT_P95_WARN_MS, crit=DRIFT_P95_CRIT_MS)
    g_flash = (_GLYPH_CRIT if flash_n >= FLASH_CRIT_COUNT
               else _GLYPH_WARN if flash_n >= FLASH_WARN_COUNT
               else _GLYPH_OK)

    v_frame = f"{frame_p95:5.1f}ms p95" if frame_p95 is not None else "—"
    v_slow  = f"{slow_p95:5.1f}ms p95"  if slow_p95  is not None else "—"
    v_cache = f"{cache_r * 100:4.1f}%"   if cache_total          else "—"
    v_mem   = f"{mem_last:5.1f} MB"      if mem_last is not None else "—"
    v_drift = f"{abs(drift_p95):5.1f}ms" if drift_p95 is not None else "—"
    v_flash = f"{flash_n} active" if flash_n > 0 else "0"

    lines.append(f"[bold {ELEC_BLUE}]🩺 HEALTH[/]")
    lines.append(
        "   "
        + _health_cell("frame", g_frame, v_frame)
        + _health_cell("cache", g_cache, v_cache)
        + _health_cell("mem",   g_mem,   v_mem)
    )
    lines.append(
        "   "
        + _health_cell("slow",  g_slow,  v_slow)
        + _health_cell("flash", g_flash, v_flash)
        + _health_cell("drift", g_drift, v_drift)
    )
    lines.append("")

    # ── FRAME ────────────────────────────────────────────────────────────
    lines.append(
        f"[bold {LIME}]⏱  FRAME[/]   [{DIM}]_refresh_fast · target 2000ms · n={n_frame}[/]"
    )
    if n_frame == 0:
        lines.append("   " + empty_state("⏳", "Nessun frame ancora misurato",
                                         hint="Attendi 2-3 tick dopo mount."))
    else:
        spark = sparkline(m.frame_ms, width=60, color=LIME)
        lines.append(f"   {spark}")
        p50 = s["frame_ms"]["p50"]
        p95 = s["frame_ms"]["p95"]
        p99 = s["frame_ms"]["p99"]
        c95 = _glyph_color(g_frame)
        lines.append(
            f"   [{DIM}]p50[/] {p50:>6.1f}ms"
            f"   [{DIM}]·[/]   [{DIM}]p95[/] [{c95}]{p95:>6.1f}ms[/]"
            f"   [{DIM}]·[/]   [{DIM}]p99[/] {p99:>6.1f}ms"
        )
    lines.append("")

    # ── SLOW ─────────────────────────────────────────────────────────────
    lines.append(
        f"[bold {TEAL}]🐌  SLOW[/]    [{DIM}]_refresh_slow · target 5000ms · n={n_slow}[/]"
    )
    if n_slow == 0:
        lines.append("   " + empty_state("⏳", "Nessun slow-tick misurato"))
    else:
        spark_s = sparkline(m.slow_ms, width=60, color=TEAL)
        lines.append(f"   {spark_s}")
        c95s = _glyph_color(g_slow)
        lines.append(
            f"   [{DIM}]p50[/] {s['slow_ms']['p50']:>6.1f}ms"
            f"   [{DIM}]·[/]   [{DIM}]p95[/] [{c95s}]{s['slow_ms']['p95']:>6.1f}ms[/]"
            f"   [{DIM}]·[/]   [{DIM}]p99[/] {s['slow_ms']['p99']:>6.1f}ms"
        )
    lines.append("")

    # ── CACHE ────────────────────────────────────────────────────────────
    lines.append(f"[bold {ORANGE}]💾  CACHE[/]")
    cache = s["cache"]
    total = cache["hits"] + cache["misses"]
    if total == 0 and not lru_funcs:
        lines.append("   " + empty_state("∅", "Nessuna operazione cache registrata"))
    else:
        if total > 0:
            ratio_pct = cache["ratio"] * 100
            bar_color = LIME if ratio_pct >= 80 else (ORANGE if ratio_pct >= 50 else HOT_PINK)
            bar = pct_bar(ratio_pct, width=22, color=bar_color)
            lines.append(
                f"   [{DIM}]manual       [/] {bar} "
                f"[{bar_color}]{ratio_pct:5.1f}%[/]   "
                f"[{DIM}]hits[/] {_fmt_int_compact(cache['hits']):<8} "
                f"[{DIM}]·[/] [{DIM}]miss[/] {_fmt_int_compact(cache['misses'])}"
            )
        if lru_funcs:
            for fn in lru_funcs:
                info = cache_info_dict(fn)
                if not info:
                    continue
                fname = getattr(fn, "__name__", "lru_func").lstrip("_").replace("_", " ")
                ratio_pct = info["ratio"] * 100
                fill_pct = (info["currsize"] / info["maxsize"] * 100) if info["maxsize"] else 0.0
                bar_color = LIME if ratio_pct >= 80 else (ORANGE if ratio_pct >= 50 else HOT_PINK)
                bar = pct_bar(ratio_pct, width=22, color=bar_color)
                lines.append(
                    f"   [{DIM}]{fname:<13}[/] {bar} "
                    f"[{bar_color}]{ratio_pct:5.1f}%[/]   "
                    f"[{DIM}]hits[/] {_fmt_int_compact(info['hits']):<8} "
                    f"[{DIM}]·[/] [{DIM}]miss[/] {_fmt_int_compact(info['misses']):<6} "
                    f"[{DIM}]·[/] [{DIM}]fill[/] {fill_pct:.0f}%"
                )
    lines.append("")

    # ── MEM ──────────────────────────────────────────────────────────────
    lines.append(
        f"[bold {ELEC_BLUE}]🧠  MEM[/]     [{DIM}]this process · RSS via psutil · n={s['rss_mb']['n']}[/]"
    )
    rss = s["rss_mb"]
    if rss["n"] == 0:
        lines.append("   " + empty_state("⏳", "RSS non ancora campionato",
                                         hint="record_rss() chiamato in _refresh_slow."))
    else:
        spark_r = sparkline(m.rss_mb, width=60, color=ELEC_BLUE)
        lines.append(f"   {spark_r}")
        c_mem = _glyph_color(g_mem)
        lines.append(
            f"   [{DIM}]last[/] [{c_mem}]{rss['last']:>6.1f} MB[/]"
            f"   [{DIM}]·[/]   [{DIM}]p95[/] {rss['p95']:>6.1f} MB"
        )
    lines.append("")

    # ── FLASH (prominente solo se attivo) ────────────────────────────────
    flash = s["flash"]
    if flash["count"] == 0:
        lines.append(
            f"[bold {DIM}]🔥  FLASH[/]   [{DIM}]critical border events[/]"
            f"{' ' * 32}[{LIME}]none[/]"
        )
    else:
        lines.append(
            f"[bold {HOT_PINK}]🔥  FLASH[/]   [{DIM}]critical border events[/]"
            f"   [{HOT_PINK}]× {flash['count']} active[/]"
        )
        for reason, n in sorted(flash["reasons"].items(), key=lambda x: -x[1]):
            lines.append(
                f"   [{DIM}]·[/] {reason:<32} [{HOT_PINK}]× {n}[/]"
            )
    lines.append("")

    # ── IDLE (collassato se vuoto) ───────────────────────────────────────
    idle = s["idle"]
    if idle["enters"] == 0:
        lines.append(
            f"[bold {DIM}]💤  IDLE[/]    [{DIM}]freeze rainbow events[/]"
            f"{' ' * 33}[{DIM}]none[/]"
        )
    else:
        marker = (f"[{ORANGE}]⏸ FROZEN[/]" if idle["active"] > 0
                  else f"[{LIME}]▶ ACTIVE[/]")
        lines.append(f"[bold {ORANGE}]💤  IDLE[/]    [{DIM}]freeze rainbow events[/]   {marker}")
        lines.append(
            f"   [{DIM}]enters[/] {fmt_int_eu(idle['enters'])}"
            f"   [{DIM}]·[/]   [{DIM}]exits[/] {fmt_int_eu(idle['exits'])}"
            f"   [{DIM}]·[/]   [{DIM}]net[/] {idle['active']}"
        )
    lines.append("")

    # ── TICK DRIFT (collassato se vuoto) ─────────────────────────────────
    drift = s["tick_drift_ms"]
    if drift["n"] == 0:
        lines.append(
            f"[bold {DIM}]📐  DRIFT[/]   [{DIM}]vs target tick (set_interval 2.0s)[/]"
            f"{' ' * 22}[{DIM}]—[/]"
        )
    else:
        c95d = _glyph_color(g_drift)
        spark_d = sparkline(m.tick_drift_ms, width=60, color=ORANGE)
        lines.append(
            f"[bold {ORANGE}]📐  DRIFT[/]   [{DIM}]vs target tick · n={drift['n']}[/]"
        )
        lines.append(f"   {spark_d}")
        lines.append(
            f"   [{DIM}]p50[/] {drift['p50']:>+7.1f}ms"
            f"   [{DIM}]·[/]   [{DIM}]p95[/] [{c95d}]{drift['p95']:>+7.1f}ms[/]"
        )

    return "\n".join(lines)


# ── JSONL writer ─────────────────────────────────────────────────────────────
class JsonlWriter:
    """Append-only writer per snapshot Metrics su disco.

    Ogni riga = 1 snapshot Metrics.summary(). Pattern: chiamare flush_metrics(m)
    da set_interval(60.0, ...) in app.py per dump periodico.

    Buffering: scrive in append e flush ogni N entry (default 1 = sync).
    File rotation NON gestita qui — usa logrotate / cron per truncate se cresce.
    """

    __slots__ = ("path", "_buffer", "_flush_every", "_logger")

    def __init__(self, path: Path | None = None, flush_every: int = 1) -> None:
        self.path: Path = path or JSONL_PATH
        self._buffer: list[str] = []
        self._flush_every: int = max(1, int(flush_every))
        self._logger = logging.getLogger("m5-watcher")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self._logger.warning("JsonlWriter mkdir fail %s: %s", self.path.parent, e)

    def append(self, line: str) -> None:
        """Aggiunge 1 riga al buffer. Auto-flush se >= flush_every."""
        if not line.endswith("\n"):
            line = line + "\n"
        self._buffer.append(line)
        if len(self._buffer) >= self._flush_every:
            self._flush()

    def _flush(self) -> None:
        """Scrive buffer su disco in append. Idempotente su buffer vuoto."""
        if not self._buffer:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.writelines(self._buffer)
            self._buffer.clear()
        except Exception as e:
            self._logger.exception("JsonlWriter flush fail: %s", e)
            # NON svuotiamo buffer su errore — riprovieremo al prossimo append.

    def flush_metrics(self, m: Metrics) -> None:
        """Snapshot di m → 1 riga JSONL → flush sync.

        Da chiamare periodicamente (es. set_interval(60.0, lambda: writer.flush_metrics(metrics))).
        """
        try:
            self.append(m.to_jsonl_line())
        except Exception as e:
            self._logger.exception("flush_metrics fail: %s", e)

    def close(self) -> None:
        """Best-effort flush finale (chiamare on_unmount)."""
        self._flush()


# ── Self-test (executable as script) ─────────────────────────────────────────
if __name__ == "__main__":
    # Sanity-check rapido: niente network, niente side-effect grossi.
    log = setup_logger()
    m = Metrics()
    for v in [4.2, 5.1, 3.8, 12.3, 6.5, 4.4, 5.0, 5.2]:
        m.record_frame(v)
    for v in [22.0, 28.5, 24.1]:
        m.record_slow(v)
    m.cache_hit(990)
    m.cache_miss(10)
    m.idle_enter()
    m.flash("CPU spike sustained")
    m.flash("pressure=error")
    m.record_tick_drift(15.2)
    m.record_tick_drift(-3.1)
    m.record_rss(312.5)
    m.record_rss(318.7)
    print(render_debug_panel(m))
    print()
    print("--- summary ---")
    print(json.dumps(m.summary(), indent=2))
    print(f"--- log path ---\n{LOG_PATH}")
    print(f"--- jsonl path ---\n{JSONL_PATH}")
