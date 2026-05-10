"""📊 KPI Widget — Business vitals panel for M5 Max Watcher.

Reads KPI.md frontmatter from the Obsidian vault and renders a Rich-markup
dashboard: MRR gauge, Outstanding bar, Pipeline bar + PI section (Lead Caldi · Cold Avg).
TTL-cached (30s). Safe for asyncio.to_thread.

Pattern mirrors graph_widget.py — no imports from app.py.
Tutte le primitive grafiche vivono in polpo_charts.py (sess.1508 audit).
"""
from __future__ import annotations

import math
import os
import time
from collections import deque
from pathlib import Path

from polpo_charts import (
    DIM, TEAL, WHITE,
    HOT_PINK, ELEC_BLUE, LIME, ORANGE,
    proportional_bar, eur_full, eur_compact, empty_state,
)

# sess.1525: sparkline storico in-memory per MRR/Outstanding/Pipeline.
# Volatile (deque, no fs write) — si perde al restart, ma il TUI è longevo
# (uptime tipico 20h+) quindi raccoglie sempre abbastanza punti.
# maxlen=30 = ~15 minuti di dati a refresh slow 30s = trend recente.
_HISTORY_MRR:      deque[float] = deque(maxlen=30)
_HISTORY_OUTSTAND: deque[float] = deque(maxlen=30)
_HISTORY_PIPELINE: deque[float] = deque(maxlen=30)
_SPARK_BARS = " ▁▂▃▄▅▆▇█"  # 9 livelli, primo è spazio


def _sparkline(values, width: int = 8) -> str:
    """Render Unicode sparkline of len `width` from sequence of values.

    Edge cases:
    - <2 punti: stringa vuota (niente trend visibile)
    - tutti uguali: barra mid (▄) ripetuta
    - downsample lineare se len > width
    """
    vs = list(values)
    if len(vs) < 2:
        return ""
    if len(vs) > width:
        step = len(vs) / width
        vs = [vs[int(i * step)] for i in range(width)]
    elif len(vs) < width:
        vs = [vs[0]] * (width - len(vs)) + vs
    lo, hi = min(vs), max(vs)
    if hi == lo:
        return _SPARK_BARS[4] * width
    return "".join(
        _SPARK_BARS[1 + int((v - lo) / (hi - lo) * 7)]
        for v in vs
    )


def _safe_float(v, default: float = 0.0) -> float:
    """Cast a float, sostituendo NaN/inf con default — sess.1508 round 2.

    KPI source = YAML frontmatter scritto a mano: dati corrotti possibili.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isfinite(f):
        return f
    return default

_DEFAULT_KPI_PATH = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Astra Digital Marketing"
    / "KPI.md"
)
# Override esplicito via env M5W_KPI_PATH — abilita uso fuori dal Mac di Mattia.
_KPI_PATH = Path(os.environ.get("M5W_KPI_PATH", str(_DEFAULT_KPI_PATH))).expanduser()

_cache:    dict | None = None
_cache_ts: float       = 0.0
_CACHE_TTL             = 30.0

# sess.1758: target estratti da YAML config (~/.config/astra/kpi_targets.yaml)
# con fallback ai default storici. Mattia può sovrascrivere senza toccare il
# codice. Mai più "perché TUI mostra 10k MRR target se sto puntando a 15k".
_KPI_TARGETS_FILE = Path.home() / ".config" / "astra" / "kpi_targets.yaml"
_KPI_TARGETS_DEFAULTS = {
    "target_mrr_eur":      10_000.0,
    "target_cash_eur":     10_000.0,
    "target_pipeline_eur": 20_000.0,
    "cold_goal_days":      30.0,   # target media inattività lead
    "cold_limit_days":     90.0,   # soglia lead stale
    # Polestar (roadmap_polestar.py)
    "outstanding_target_eur": 3_000.0,
    "mrr_target_q2_eur":      5_200.0,
    "kill_target_amount_eur": 2_500.0,
    # Trap (roadmap_traps.py)
    "trap_commit_per_24h": 5.0,
    "trap_open_projects":  3.0,
    "trap_stale_memory":   100.0,
    "trap_future_events":  40.0,
}
_kpi_targets_cache:    dict | None = None
_kpi_targets_cache_ts: float       = 0.0
_KPI_TARGETS_TTL                   = 60.0


def _load_kpi_targets() -> dict:
    """Read KPI targets da YAML config con fallback ai default. TTL 60s.

    File schema (~/.config/astra/kpi_targets.yaml):
        target_mrr_eur: 12000.0
        target_cash_eur: 8000.0
        target_pipeline_eur: 25000.0
        cold_goal_days: 30
        cold_limit_days: 90

    Mancano chiavi → fallback sui default. File assente → tutto default.
    """
    global _kpi_targets_cache, _kpi_targets_cache_ts
    now = time.monotonic()
    if _kpi_targets_cache is not None and now - _kpi_targets_cache_ts < _KPI_TARGETS_TTL:
        return _kpi_targets_cache
    out = dict(_KPI_TARGETS_DEFAULTS)
    out["_source"] = "defaults"
    if _KPI_TARGETS_FILE.exists():
        try:
            import yaml as _yaml  # type: ignore
            data = _yaml.safe_load(_KPI_TARGETS_FILE.read_text()) or {}
            if isinstance(data, dict):
                for k in _KPI_TARGETS_DEFAULTS:
                    if k in data:
                        try:
                            out[k] = float(data[k])
                        except (TypeError, ValueError):
                            pass
                out["_source"] = "yaml_config"
        except Exception:
            pass
    _kpi_targets_cache = out
    _kpi_targets_cache_ts = now
    return out


def read_kpi_data(path: Path | None = None) -> dict:
    """Read KPI.md YAML frontmatter. TTL-cached. Safe for asyncio.to_thread.

    Args:
        path: override esplicito (utile per test e per config utente).
              Quando path != None, il TTL cache è bypassato.

    sess.1508 round 3 fix: prima il test_suite chiamava `read_kpi_data(path=...)`
    ma il param non esisteva → TypeError silente perché il test non era nel
    groups list runner. Ora API coerente.
    """
    global _cache, _cache_ts
    target = path if path is not None else _KPI_PATH
    if path is None:
        now = time.monotonic()
        if _cache and now - _cache_ts < _CACHE_TTL:
            return _cache
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
        in_fm, data = False, {}
        for line in lines:
            if line.strip() == '---':
                if not in_fm:
                    in_fm = True
                    continue
                break
            if in_fm and ':' in line:
                k, _, v = line.partition(':')
                v = v.strip().strip('"\'')
                try:
                    data[k.strip()] = float(v)
                except ValueError:
                    data[k.strip()] = v
        if path is None:
            _cache, _cache_ts = data, time.monotonic()
        return data
    except Exception:
        return {}


def _bar(pct: float, w: int = 28, color: str = LIME) -> str:
    """Compat shim: pct_bar via proportional_bar with total=100."""
    return proportional_bar(pct, 100.0, w, color)


def _cold_color(cold_avg: float) -> str:
    """Allineata sulla scala 0/30/60/90 (= cold_pct). Sess.1508 audit fix.

    Round 2: dato invalido (negativo o non finito) → DIM, non LIME falso-OK.
    """
    if not math.isfinite(cold_avg) or cold_avg < 0:
        return DIM
    # sess.1758: soglie cold da config invece di hard-coded.
    targets = _load_kpi_targets()
    cold_limit = targets["cold_limit_days"]
    cold_goal = targets["cold_goal_days"]
    if cold_avg >= cold_limit * 2 / 3:
        return HOT_PINK
    if cold_avg >= cold_goal:
        return ORANGE
    return LIME


def render_kpi(kpi: dict, w: int = 28) -> str:
    """Render business KPI panel as Rich markup string."""
    if not kpi:
        return empty_state("🔄", "Leggendo KPI.md dal vault…", "TTL 30s · prima esecuzione ~1s")

    # sess.1508 round 2: NaN/inf guard via _safe_float — KPI.md è scritto a
    # mano da Mattia, dati corrotti hanno già crashato render storicamente.
    mrr       = _safe_float(kpi.get('mrr',               0))
    mrr_prev  = _safe_float(kpi.get('mrr_previous',      mrr), mrr)
    outstand  = _safe_float(kpi.get('outstanding',        0))
    debtors   = kpi.get('outstanding_debtors')           # ⬅ no più hardcoded "4"
    pipeline  = _safe_float(kpi.get('pipeline_weighted',  0))
    tot_leads = _safe_float(kpi.get('setter_total_leads', 0))
    active    = _safe_float(kpi.get('setter_active',      0))
    cold_avg  = _safe_float(kpi.get('setter_cold_avg',    0))
    updated   = kpi.get('updated', '—')

    # sess.1508 round 2: MRR ≥ 1M → eur_compact ("€1.2M") per evitare
    # overflow riga (15+ char nel layout fisso 14-col label).
    def _fmt_eur(v: float) -> str:
        return eur_compact(v) if abs(v) >= 1_000_000 else eur_full(v)

    # sess.1758: target da YAML config invece di hard-coded.
    targets = _load_kpi_targets()
    target_mrr  = targets["target_mrr_eur"]
    target_cash = targets["target_cash_eur"]
    target_pipe = targets["target_pipeline_eur"]
    cold_limit  = targets["cold_limit_days"]

    delta       = mrr - mrr_prev
    delta_s     = ('+' if delta >= 0 else '') + _fmt_eur(delta)
    delta_color = LIME if delta >= 0 else HOT_PINK
    mrr_pct     = min(mrr      / target_mrr  * 100, 100) if target_mrr > 0 else 0
    out_pct     = min(outstand / target_cash * 100, 100) if target_cash > 0 else 0
    pipe_pct    = min(pipeline / target_pipe * 100, 100) if target_pipe > 0 else 0
    conv_pct    = min((active  / tot_leads    * 100) if tot_leads > 0 else 0, 100)
    cold        = max(0.0, tot_leads - active)
    cold_pct    = min(cold_avg / cold_limit * 100, 100) if cold_limit > 0 else 0
    cold_color  = _cold_color(cold_avg)

    # sess.1525: append ai deque storici per sparkline. Append condizionato:
    # solo se ultimo valore è cambiato (evita ripetizioni che azzerano min/max).
    if not _HISTORY_MRR or _HISTORY_MRR[-1] != mrr:
        _HISTORY_MRR.append(mrr)
    if not _HISTORY_OUTSTAND or _HISTORY_OUTSTAND[-1] != outstand:
        _HISTORY_OUTSTAND.append(outstand)
    if not _HISTORY_PIPELINE or _HISTORY_PIPELINE[-1] != pipeline:
        _HISTORY_PIPELINE.append(pipeline)
    spark_mrr  = _sparkline(_HISTORY_MRR)
    spark_out  = _sparkline(_HISTORY_OUTSTAND)
    spark_pipe = _sparkline(_HISTORY_PIPELINE)

    # Outstanding sub-line: numero debitori dinamico, fallback a stringa neutra
    if debtors is None:
        out_sub = "da incassare"
    else:
        try:
            n = int(float(debtors))
            out_sub = f"da incassare · {n} debitor{'i attivi' if n != 1 else 'e attivo'}"
        except (TypeError, ValueError):
            out_sub = "da incassare"

    def row(emoji: str, ec: str, label: str, pct: float, bc: str,
            value: str, sub: str) -> list[str]:
        b = _bar(pct, w, bc)
        return [
            f"  [{ec}]{emoji}[/] [{DIM}]{label:<14}[/] {b}  [bold {WHITE}]{value}[/]",
            f"  [{DIM}]{'':18}[/]{sub}",
        ]

    lines: list[str] = [
        "",
        f"  [bold {WHITE}]📊 BUSINESS KPI[/]  [{DIM}]· Astra Digital Marketing · {updated}[/]",
        "",
    ]
    # sess.1525: sparkline trend inline accanto al value
    mrr_value_str = f"{_fmt_eur(mrr)}  [dim {LIME}]{spark_mrr}[/]" if spark_mrr else _fmt_eur(mrr)
    lines += row(
        "💰", LIME, "MRR", mrr_pct, LIME,
        mrr_value_str,
        f"[{delta_color}]{delta_s}[/] [{DIM}]vs prev · goal {_fmt_eur(target_mrr)} · {mrr_pct:.0f}%[/]",
    )
    lines.append("")
    # sess.1525: alert visivo quando Outstanding > MRR (incassi attesi
    # superano il fatturato mensile). Da informazione passiva a segnale d'azione.
    out_critical = outstand > mrr and mrr > 0
    spark_out_styled = f"  [dim {HOT_PINK}]{spark_out}[/]" if spark_out else ""
    if out_critical:
        out_emoji = "⚠️"
        out_color = "#ff3366"  # polpo_red
        out_value = f"{_fmt_eur(outstand)}{spark_out_styled}  [bold {out_color}]· CRITICAL[/]"
        out_sub_styled = f"[bold {out_color}]{out_sub} · supera MRR mensile ({outstand/mrr:.1f}×)[/]"
    else:
        out_emoji = "📌"
        out_color = HOT_PINK
        out_value = f"{_fmt_eur(outstand)}{spark_out_styled}"
        out_sub_styled = f"[{DIM}]{out_sub}[/]"
    lines += row(
        out_emoji, out_color, "Outstanding", out_pct, out_color,
        out_value,
        out_sub_styled,
    )
    lines.append("")
    pipe_value_str = f"{_fmt_eur(pipeline)}  [dim {ELEC_BLUE}]{spark_pipe}[/]" if spark_pipe else _fmt_eur(pipeline)
    lines += row(
        "🎯", ELEC_BLUE, "Pipeline", pipe_pct, ELEC_BLUE,
        pipe_value_str,
        f"[{DIM}]weighted · goal {_fmt_eur(target_pipe)} · {pipe_pct:.0f}%[/]",
    )
    lines.append("")
    lines.append(f"  [{DIM}]── PI  Pipeline Indicators  ·  setter · lead commerciali ──────────[/]")
    lines.append("")
    lines += row(
        "🔥", HOT_PINK, "Lead Caldi", conv_pct, HOT_PINK,
        f"{int(active)} lead",
        f"[{DIM}]{conv_pct:.1f}% tasso conv. · [/][{ORANGE}]{int(cold)} lead freddi[/][{DIM}] · {int(tot_leads)} lead totali[/]",
    )
    lines.append("")
    lines += row(
        "🕐", cold_color, "Cold Avg", cold_pct, cold_color,
        f"{cold_avg:.1f} gg",
        f"[{DIM}]media gg inattività per lead freddo · goal < {int(targets['cold_goal_days'])} gg[/]",
    )
    return "\n".join(lines)


# ─── Public API for TitleBar line5 (sess.1539 round 2) ──────────────────────
# Single source of truth — app.py importa solo questo, niente _safe_float
# duplicato né accessi diretti a _HISTORY_*.
def kpi_for_titlebar(data: dict, spark_w: int = 6) -> dict:
    """Build payload pronto per TitleBar line5 — Nome · Dato · Unità + sparkline.

    Garanzie:
    - Tutti i campi numerici NaN/inf-safe via _safe_float.
    - Sparkline letti dai medesimi _HISTORY_* deque popolati da render_kpi
      (zero divergenza tra panel KPI e TitleBar).
    - Width sparkline default 6 (compatto per cintura header).

    Returns:
        dict con chiavi mrr/mrr_delta/outstanding/pipeline/leads/cold_avg
        + spark_mrr/spark_out/spark_pipe (str unicode).
        Empty dict {} se data vuoto o non-dict (sess.1539 round 3 hardening).
    """
    # sess.1539 round 3: guard non-dict (list/int/str/None). YAML parser
    # malformato può ritornare list al posto di dict → AttributeError nel
    # main loop _update_subtitle ogni 1s. Fallback graceful a {}.
    if not data or not isinstance(data, dict):
        return {}
    mrr      = _safe_float(data.get('mrr',                0))
    mrr_prev = _safe_float(data.get('mrr_previous',      mrr), mrr)
    return {
        'mrr':         mrr,
        'mrr_delta':   mrr - mrr_prev,
        'outstanding': _safe_float(data.get('outstanding',         0)),
        'pipeline':    _safe_float(data.get('pipeline_weighted',   0)),
        'leads':       _safe_float(data.get('setter_active',       0)),
        'cold_avg':    _safe_float(data.get('setter_cold_avg',     0)),
        'spark_mrr':   _sparkline(_HISTORY_MRR,      spark_w),
        'spark_out':   _sparkline(_HISTORY_OUTSTAND, spark_w),
        'spark_pipe':  _sparkline(_HISTORY_PIPELINE, spark_w),
    }
