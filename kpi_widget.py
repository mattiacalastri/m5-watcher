"""📊 KPI Widget — Business vitals panel for M5 Max Watcher.

Reads KPI.md frontmatter from the Obsidian vault and renders a Rich-markup
dashboard: MRR gauge, Outstanding bar, Pipeline bar + PI section (Lead Caldi · Cold Avg).
TTL-cached (30s). Safe for asyncio.to_thread.

Pattern mirrors graph_widget.py — no imports from app.py.
Tutte le primitive grafiche vivono in polpo_charts.py (sess.1508 audit).
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from polpo_charts import (
    DIM, TEAL, WHITE,
    HOT_PINK, ELEC_BLUE, LIME, ORANGE,
    proportional_bar, eur_full, eur_compact, empty_state,
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

_KPI_PATH = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Astra Digital Marketing"
    / "KPI.md"
)

_cache:    dict | None = None
_cache_ts: float       = 0.0
_CACHE_TTL             = 30.0

_TARGET_MRR  = 10_000.0
_TARGET_CASH = 10_000.0
_TARGET_PIPE = 20_000.0
_COLD_GOAL   = 30.0   # gg target media inattività
_COLD_LIMIT  = 90.0   # gg soglia lead stale


def read_kpi_data() -> dict:
    """Read KPI.md YAML frontmatter. TTL-cached. Safe for asyncio.to_thread."""
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache and now - _cache_ts < _CACHE_TTL:
        return _cache
    try:
        lines = _KPI_PATH.read_text(encoding="utf-8").splitlines()
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
        _cache, _cache_ts = data, now
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
    if cold_avg >= _COLD_LIMIT * 2 / 3:   # ≥ 60gg
        return HOT_PINK
    if cold_avg >= _COLD_GOAL:            # ≥ 30gg
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

    delta       = mrr - mrr_prev
    delta_s     = ('+' if delta >= 0 else '') + _fmt_eur(delta)
    delta_color = LIME if delta >= 0 else HOT_PINK
    mrr_pct     = min(mrr      / _TARGET_MRR  * 100, 100)
    out_pct     = min(outstand / _TARGET_CASH * 100, 100)
    pipe_pct    = min(pipeline / _TARGET_PIPE * 100, 100)
    conv_pct    = min((active  / tot_leads    * 100) if tot_leads > 0 else 0, 100)
    cold        = max(0.0, tot_leads - active)
    cold_pct    = min(cold_avg / _COLD_LIMIT * 100, 100)
    cold_color  = _cold_color(cold_avg)

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
    lines += row(
        "💰", LIME, "MRR", mrr_pct, LIME,
        _fmt_eur(mrr),
        f"[{delta_color}]{delta_s}[/] [{DIM}]vs prev · goal {_fmt_eur(_TARGET_MRR)} · {mrr_pct:.0f}%[/]",
    )
    lines.append("")
    lines += row(
        "📌", HOT_PINK, "Outstanding", out_pct, HOT_PINK,
        _fmt_eur(outstand),
        f"[{DIM}]{out_sub}[/]",
    )
    lines.append("")
    lines += row(
        "🎯", ELEC_BLUE, "Pipeline", pipe_pct, ELEC_BLUE,
        _fmt_eur(pipeline),
        f"[{DIM}]weighted · goal {_fmt_eur(_TARGET_PIPE)} · {pipe_pct:.0f}%[/]",
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
        f"[{DIM}]media gg inattività per lead freddo · goal < {int(_COLD_GOAL)} gg[/]",
    )
    return "\n".join(lines)
