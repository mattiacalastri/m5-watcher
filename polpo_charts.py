"""🐙 Polpo Charts — primitive condivise per sparkline / bar / gauge / formatting.

Estratto da app.py / kpi_widget.py / graph_widget.py per evitare drift tra
3 alfabeti sparkline e 3 algoritmi normalize incoerenti (audit sess.1508).

Tutto legge da polpo.tokens.json — niente hardcoded hex qui dentro.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

_TOKENS    = json.loads((Path(__file__).parent / "polpo.tokens.json").read_text())
_PALETTE   = _TOKENS["palette"]
_ENERGY    = _TOKENS["energy_palette"]
_GLYPHS    = _TOKENS["chart_glyphs"]

# ── Palette canonica ─────────────────────────────────────────────────────────
TEAL       = _PALETTE["polpo_teal"]
DIM        = _PALETTE["polpo_dim"]
FG         = _PALETTE["polpo_fg"]
BG         = _PALETTE["polpo_bg"]
BG_ALT     = _PALETTE["polpo_bg_alt"]
RED        = _PALETTE["polpo_red"]
SCAR       = _PALETTE["polpo_scar"]
MAGENTA    = _PALETTE["polpo_magenta"]
GREEN      = _PALETTE["polpo_green"]
CYAN       = _PALETTE["polpo_cyan"]
YELLOW     = _PALETTE["polpo_yellow"]

# ── Energy palette (drift-prone hex storicamente) ────────────────────────────
HOT_PINK   = _ENERGY["hot_pink"]
ELEC_BLUE  = _ENERGY["elec_blue"]
LIME       = _ENERGY["lime"]
ORANGE     = _ENERGY["orange"]
DEEP_PURPL = _ENERGY["deep_purple"]
SOFT_GREEN = _ENERGY["soft_green"]
WHITE      = _ENERGY["white"]
ENERGY_YEL = _ENERGY["yellow"]   # alias per chiamare yellow brand-canonico

# ── Chart glyphs ─────────────────────────────────────────────────────────────
SPARK9     = _GLYPHS["spark9"]    # ' ▁▂▃▄▅▆▇█' — 9 step, gradiente continuo
BAR8       = _GLYPHS["bar8"]      # ' ▏▎▍▌▋▊▉█' — 8 step subpixel left-fill
BAR2       = _GLYPHS["bar2"]      # '█░' — discreto, alta densità
HEATMAP8   = _GLYPHS["heatmap8"]  # '·░▒▓▚▞▣█' — 8 livelli percettivamente distinti
ELLIPSIS   = _GLYPHS["ellipsis"]


# ── Sparkline ────────────────────────────────────────────────────────────────
def sparkline(values: Iterable[float], width: int = 50, color: str | None = None) -> str:
    """Sparkline Unicode a 9 livelli con normalize **min-max**.

    Risolve il bug storico (sess.1508 audit): serie costanti restituivano
    sempre `█████` perché normalizzavano solo su max(vals).

    Args:
        values: serie numerica (deque, list, tuple).
        width: char visibili (slice degli ultimi N).
        color: hex Rich markup; se None ritorna stringa nuda.
    """
    vals = list(values)[-width:]
    if not vals:
        # sess.1508 round 2: empty state coerente — markup solo se color
        # esplicito, altrimenti plain (nullity contract docstring).
        empty = "─" * width
        return f'[{DIM}]{empty}[/]' if color else empty
    vmin = min(vals)
    vmax = max(vals)
    rng  = vmax - vmin
    n    = len(SPARK9) - 1  # 8
    if rng < 1e-9:
        # Serie costante → barra a metà altezza, segnale visivo "flat"
        glyph = SPARK9[n // 2]
        out = glyph * len(vals)
    else:
        out = ''.join(SPARK9[min(n, int((v - vmin) / rng * n))] for v in vals)
    if color:
        return f'[{color}]{out}[/]'
    return out


# ── Bars ─────────────────────────────────────────────────────────────────────
def pct_bar(pct: float, width: int = 20, color: str = LIME, dim_track: bool = True) -> str:
    """Barra discreta 2-step (█/░) con percentuale 0-100.

    Returns Rich markup. Per UI dense / barre kpi / gauge classici.
    """
    pct = max(0.0, min(100.0, pct))
    filled = round(pct / 100 * width)
    track  = f'[{DIM}]{"░" * (width - filled)}[/]' if dim_track else "░" * (width - filled)
    return f'[{color}]{"█" * filled}[/]{track}'


def subpixel_bar(pct: float, width: int = 20, color: str = LIME) -> str:
    """Barra 8-step subpixel (BAR8) per gauge ad alta granularità.

    Usata storicamente come `bar()` in app.py:178.
    """
    pct = max(0.0, min(100.0, pct))
    total = pct / 100 * width
    full  = int(total)
    rem   = total - full
    sub   = BAR8[round(rem * (len(BAR8) - 1))]
    out   = "█" * full + (sub if full < width else "")
    pad   = width - len(out)
    return f'[{color}]{out}[/][{DIM}]{"░" * pad}[/]' if pad > 0 else f'[{color}]{out}[/]'


def proportional_bar(val: float, total: float, width: int = 20, color: str = LIME) -> str:
    """Barra proporzionale val/total con guardia anti div-zero."""
    filled = min(width, round(val / max(total, 1e-9) * width))
    return f'[{color}]{"█" * filled}[/][{DIM}]{"░" * (width - filled)}[/]'


# ── Gauge ────────────────────────────────────────────────────────────────────
def gauge(
    val: float,
    lo: float,
    hi: float,
    width: int = 24,
    higher_is_better: bool = True,
) -> tuple[str, str]:
    """Gauge lineare mappato su [lo, hi]. Color semantica direzionale.

    higher_is_better=True  → verde sopra, rosso sotto (default, KPI growth).
    higher_is_better=False → rosso sopra, verde sotto (CPU%, error rate).

    Returns (bar_markup, color).

    sess.1508 round 2: short-circuit se hi-lo ≈ 0 (vault 0 note → density=0,
    range=0) → DIM bar invece di ORANGE warning fuorviante. Anche guard
    su val non finito (NaN/inf da dati corrotti).
    """
    import math as _m
    if not _m.isfinite(val) or hi - lo < 1e-9:
        bar = f'[{DIM}]{"░" * width}[/]'
        return bar, DIM
    norm = max(0.0, min(1.0, (val - lo) / (hi - lo)))
    if higher_is_better:
        color = LIME if norm >= 0.65 else (TEAL if norm >= 0.35 else ORANGE)
    else:
        color = ORANGE if norm >= 0.65 else (TEAL if norm >= 0.35 else LIME)
    filled = round(norm * width)
    bar = f'[{color}]{"█" * filled}[/][{DIM}]{"░" * (width - filled)}[/]'
    return bar, color


def pct_color(pct: float) -> str:
    """Color semantico standard per percentuali load (CPU, mem, disk)."""
    if pct >= 80: return HOT_PINK
    if pct >= 60: return ORANGE
    if pct >= 40: return ENERGY_YEL
    return LIME


# ── Formatting ───────────────────────────────────────────────────────────────
def truncate(s: str, n: int) -> str:
    """Tronca con ellipsis (…) invece di clip muto.

    Risolve il bug audit sess.1508: tagli a [:24]/[:36]/[:60] silenti.
    """
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    if n == 1:
        return ELLIPSIS
    return s[: n - 1] + ELLIPSIS


def eur_compact(v: float) -> str:
    """€1.2M / €12k / €234. Soglie 1k / 1M con fallback formato europeo."""
    av = abs(v)
    sign = "-" if v < 0 else ""
    if av >= 1_000_000:
        return f"{sign}€{av / 1_000_000:.1f}M"
    if av >= 10_000:
        return f"{sign}€{av / 1000:.0f}k"
    if av >= 1_000:
        return f"{sign}€{av / 1000:.1f}k"
    return f"{sign}€{int(av)}"


def eur_full(v: float) -> str:
    """€12.345 — formato europeo con separatore migliaia (per fatture, importi specifici)."""
    return f"€{int(v):,}".replace(",", ".")


def gb(n: int) -> str:
    """Bytes → '1.2G'."""
    return f"{n / 1024 ** 3:.1f}G"


def fmt_int_eu(n: int | float) -> str:
    """Int europeo con separatore '.' → 1.234.567."""
    return f"{int(n):,}".replace(",", ".")


# ── Empty state ──────────────────────────────────────────────────────────────
def empty_state(icon: str, msg: str, hint: str | None = None) -> str:
    """Empty state standardizzato: icona + messaggio + hint opzionale.

    Sostituisce 3 design diversi (kpi/graph/feed) con un'unica forma.
    """
    head = f"[{DIM}]{icon} {msg}[/]"
    if hint:
        return f"{head}\n[{DIM}]   {hint}[/]"
    return head
