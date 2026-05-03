"""📊 KPI Widget — Business vitals panel for M5 Max Watcher.

Reads KPI.md frontmatter from the Obsidian vault and renders a Rich-markup
dashboard: MRR gauge, Outstanding bar, Pipeline bar, Setter funnel.
TTL-cached (30s). Safe for asyncio.to_thread.

Pattern mirrors graph_widget.py — no imports from app.py.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_P         = json.loads((Path(__file__).parent / "polpo.tokens.json").read_text())["palette"]
TEAL       = _P["polpo_teal"]
DIM        = _P["polpo_dim"]
FG         = _P["polpo_fg"]
WHITE      = "#ffffff"
LIME       = "#a8ff60"
HOT_PINK   = "#ff2d92"
ELEC_BLUE  = "#00e5ff"
ORANGE     = "#ff8a3d"
DEEP_PURPL = "#9d4dff"
SOFT_GREEN = "#5dffaa"

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
    filled = min(w, max(0, round(pct / 100 * w)))
    return f'[{color}]{"█" * filled}[/][{DIM}]{"░" * (w - filled)}[/]'


def _eur(v: float) -> str:
    return f"€{int(v):,}".replace(',', '.')


def render_kpi(kpi: dict, w: int = 28) -> str:
    """Render business KPI panel as Rich markup string."""
    if not kpi:
        return f"[{DIM}]🔄 Leggendo KPI.md dal vault…[/]"

    mrr       = float(kpi.get('mrr',               0))
    mrr_prev  = float(kpi.get('mrr_previous',      mrr))
    outstand  = float(kpi.get('outstanding',        0))
    pipeline  = float(kpi.get('pipeline_weighted',  0))
    tot_leads = float(kpi.get('setter_total_leads', 0))
    active    = float(kpi.get('setter_active',      0))
    cold_avg  = float(kpi.get('setter_cold_avg',    0))
    updated   = kpi.get('updated', '—')

    delta       = mrr - mrr_prev
    delta_s     = ('+' if delta >= 0 else '') + _eur(delta)
    delta_color = LIME if delta >= 0 else HOT_PINK
    mrr_pct     = min(mrr      / _TARGET_MRR  * 100, 100)
    out_pct     = min(outstand / _TARGET_CASH * 100, 100)
    pipe_pct    = min(pipeline / _TARGET_PIPE * 100, 100)
    conv_pct    = min((active  / tot_leads    * 100) if tot_leads > 0 else 0, 100)
    cold        = max(0.0, tot_leads - active)
    cold_pct    = min(cold_avg / 90.0 * 100, 100)   # 90 gg = soglia lead stale
    cold_color  = HOT_PINK if cold_avg > 60 else (ORANGE if cold_avg > 30 else LIME)

    # Layout: "  EMOJI label(14)  bar  value" — sub-line indented 20 cols to align
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
        _eur(mrr),
        f"[{delta_color}]{delta_s}[/] [{DIM}]vs prev · goal {_eur(_TARGET_MRR)} · {mrr_pct:.0f}%[/]",
    )
    lines.append("")
    lines += row(
        "📌", HOT_PINK, "Outstanding", out_pct, HOT_PINK,
        _eur(outstand),
        f"[{DIM}]da incassare · 4 debitori attivi[/]",
    )
    lines.append("")
    lines += row(
        "🎯", ELEC_BLUE, "Pipeline", pipe_pct, ELEC_BLUE,
        _eur(pipeline),
        f"[{DIM}]weighted · goal {_eur(_TARGET_PIPE)} · {pipe_pct:.0f}%[/]",
    )
    lines.append("")
    lines.append(f"  [{DIM}]── PI  Pipeline Indicators  ·  setter · lead commerciali ──────────[/]")
    lines.append("")
    lines += row(
        "🔥", HOT_PINK, "Lead Caldi", conv_pct, HOT_PINK,
        f"{int(active)} lead",
        f"[{DIM}]{conv_pct:.1f}% tasso conv. · [/][{ORANGE}]{int(cold)} lead[/][{DIM}] freddi · {int(tot_leads)} lead totali[/]",
    )
    lines.append("")
    lines += row(
        "🕐", cold_color, "Cold Avg", cold_pct, cold_color,
        f"{cold_avg:.1f} gg",
        f"[{DIM}]media gg inattività per lead freddo · goal < 30 gg[/]",
    )
    return "\n".join(lines)
