"""Vault Intelligence Panel — Neural Density cockpit. Polpo palette. Rich markup.

Primitive grafiche unificate via polpo_charts.py (sess.1508 audit).
"""
from __future__ import annotations

from collections import deque
from statistics import mean
from typing import Optional

import networkx as nx

from polpo_charts import (
    TEAL, DIM, FG, WHITE,
    HOT_PINK, ELEC_BLUE, LIME, ORANGE, DEEP_PURPL, SOFT_GREEN, ENERGY_YEL,
    sparkline as _spark_unified,
    pct_bar as _pct_bar_unified,
    proportional_bar,
    gauge as _gauge_unified,
    pct_color as _pct_color_unified,
    gb as _gb_unified,
)

FILTER_MODES    = ("all", "moc", "orphan")
MAX_FOCUS_NODES = 40
CANVAS_W        = 90
CANVAS_H        = 28


# ── Local thin shims (back-compat con i call site esistenti) ─────────────────
def _sparkline(data: "deque[float]", w: int = 50) -> str:
    """Sparkline normalize **min-max** (sess.1508 fix)."""
    return _spark_unified(data, w)


def _pct_bar(pct: float, w: int = 20) -> str:
    """Barra 2-step nuda (no markup) per integrazione in stringhe colorate esterne."""
    p = max(0.0, min(100.0, pct))
    filled = round(p / 100 * w)
    return '█' * filled + '░' * (w - filled)


def _pct_color(pct: float) -> str:
    return _pct_color_unified(pct)


def _gb(n: int) -> str:
    return _gb_unified(n)


# Neural density gauge thresholds (realistic for large knowledge graphs ~3k notes)
_ND_LOW  = 0.0003   # very sparse
_ND_MID  = 0.001    # moderate
_ND_HIGH = 0.002    # dense


def _bar(val: float, total: float, w: int = 20, color: str = LIME) -> str:
    """Proportional filled bar."""
    return proportional_bar(val, total, w, color)


def _gauge(val: float, lo: float, hi: float, w: int = 24,
           higher_is_better: bool = True) -> tuple[str, str]:
    """Linear gauge mapped to [lo, hi]. Returns (bar_markup, color).

    higher_is_better=True default (vault metrics: density/giant alti = bene).
    """
    return _gauge_unified(val, lo, hi, w, higher_is_better=higher_is_better)


def render_graph(
    gdata: dict,
    w: int = CANVAS_W,
    h: int = CANVAS_H,
    filter_mode: str = "all",
    focus_node: Optional[str] = None,
    cpu_percents: Optional[list] = None,
    cpu_history: Optional["deque[float]"] = None,
    mem: Optional[dict] = None,
    mem_history: Optional["deque[float]"] = None,
) -> str:
    """Vault Intelligence Panel — Neural Density cockpit. Full Rich markup string."""

    if "error" in gdata:
        return (
            f"[bold {ELEC_BLUE}]🕸 VAULT INTELLIGENCE[/]\n\n"
            f"  [{ORANGE}]⚠ {gdata['error']}[/]\n\n"
            f"  [{DIM}]Vault: ~/Library/Mobile Documents/iCloud~md~obsidian/[/]\n"
            f"  [{DIM}]Assicurati che Obsidian sia sincronizzato.[/]"
        )

    stats = gdata.get("stats", {})
    intel = gdata.get("intel", {})

    if not stats or not intel:
        return (
            f"[bold {ELEC_BLUE}]🕸 VAULT INTELLIGENCE[/]\n\n"
            f"  [{DIM}]🔄 Calcolo Neural Density in corso…[/]\n"
            f"  [{DIM}]Prima esecuzione: ~5-10s (parsing {stats.get('total', '?')} note)[/]"
        )

    total   = stats.get("total",   0)
    edges   = stats.get("edges",   0)
    mocs    = stats.get("mocs",    0)
    orphans = stats.get("orphans", 0)

    density    = intel.get("density",     0.0)
    clustering = intel.get("clustering",  0.0)
    avg_degree = intel.get("avg_degree",  0.0)
    giant      = intel.get("giant_ratio", 0.0)
    n_clusters = intel.get("n_clusters",  0)
    recent_7d  = intel.get("recent_7d",   0)

    orphan_ratio = orphans / max(total, 1)

    sep = f"  [{DIM}]·[/]  "

    # ── Header ────────────────────────────────────────────────────────────────
    header = (
        f"[bold {TEAL}]🕸 VAULT INTELLIGENCE[/]"
        f"{sep}[{DIM}]{total} note[/]"
        f"{sep}[{DIM}]{edges} link[/]"
        f"{sep}[{TEAL}]◆ {mocs} MOC[/]"
        f"{sep}[{DIM}]{orphans} orphan[/]"
    )

    div = f"  [{DIM}]{'─' * 82}[/]"

    # ── Neural Density gauges ─────────────────────────────────────────────────
    d_bar, d_col  = _gauge(density,    _ND_LOW, _ND_HIGH)
    c_bar, c_col  = _gauge(clustering, 0.0, 0.35)
    g_bar, g_col  = _gauge(giant,      0.3, 0.9)
    # NB: orphan_ratio non viene reso come bar — entra solo nel nd_score.

    nd_score = int((
        (density / _ND_HIGH) * 0.30 +
        clustering * 0.25 +
        giant * 0.25 +
        (1 - orphan_ratio) * 0.20
    ) * 100)
    nd_score = min(100, nd_score)
    nd_color = LIME if nd_score >= 65 else (TEAL if nd_score >= 40 else ORANGE)

    neural_lines = [
        "",
        f"  [{ELEC_BLUE}]⚡ NEURAL DENSITY[/]  "
        f"[bold {nd_color}]{nd_score:3d}[/][{DIM}]/100[/]  "
        f"[{nd_color}]{_bar(nd_score, 100, 30, nd_color)}[/]  "
        f"[{DIM}]Cluster:{n_clusters}[/]  [{LIME}]+{recent_7d}[/][{DIM}]/7gg[/]",
        "",
        f"  [{DIM}]Sinapsi [/]{d_bar}[{DIM}]{density:.4f}[/]  "
        f"[{DIM}]Cluster [/]{c_bar}[{c_col}]{clustering:.3f}[/]  "
        f"[{DIM}]Giant [/]{g_bar}[{g_col}]{giant * 100:.0f}%[/]  "
        f"[{DIM}]Deg.[bold]{avg_degree:.1f}[/][/]",
        "",
    ]

    # ── Data Attractors ───────────────────────────────────────────────────────
    top_ind = intel.get("top_indegree", [])
    max_ind = top_ind[0][1] if top_ind else 1

    def attractor_row(name: str, in_d: int, out_d: int, ntype: str, bet: float) -> str:
        glyph = "◆" if ntype == "moc" else "●"
        color = TEAL if ntype == "moc" else (ELEC_BLUE if in_d >= 40 else FG)
        label = name[:26]
        bar26 = _bar(in_d, max_ind, 22, color)
        bet_str = f"[{ORANGE}]{bet:.3f}[/]" if bet > 0.01 else f"[{DIM}]{bet:.3f}[/]"
        # Numeri tipo identico (in/out degree) → entrambi right-aligned per
        # leggibilità colonnare (sess.1508 audit fix).
        return (
            f"  [{color}]{glyph}[/] [{color}]{label:<26}[/] "
            f"{bar26} "
            f"[{DIM}]↑[/][bold {color}]{in_d:>4}[/]"
            f"[{DIM}] ↓{out_d:>3}[/]  "
            f"[{DIM}]btw[/] {bet_str}"
        )

    attractor_lines = [
        "",
        f"  [{ELEC_BLUE}]🧠 DATA ATTRACTORS[/]  "
        f"[{DIM}](in-degree · out-degree · betweenness centrality)[/]",
        div,
        "",
        *[attractor_row(*row) for row in top_ind[:8]],
        "",
    ]

    # ── Status distribution ───────────────────────────────────────────────────
    sd       = intel.get("status_dist", {})
    sd_total = sum(sd.values()) or 1

    def sd_row(label: str, key: str, color: str) -> str:
        n = sd.get(key, 0)
        return (
            f"  [{color}]{label:<10}[/] "
            f"{_bar(n, sd_total, 16, color)} "
            f"[{DIM}]{n:>5}[/]"
        )

    status_lines = [
        "",
        f"  [{ELEC_BLUE}]📊 STATO VAULT[/]",
        div,
        "",
        sd_row("seed",      "seed",      LIME),
        sd_row("growing",   "growing",   TEAL),
        sd_row("evergreen", "evergreen", ELEC_BLUE),
        sd_row("stub",      "stub",      DIM),
        "",
    ]

    # ── Semantic areas (folder distribution) ─────────────────────────────────
    _AREA_COLORS: dict[str, str] = {
        "Sessioni":              ELEC_BLUE,
        "🧠 Memory":             HOT_PINK,
        "6 — Knowledge Library": ORANGE,
        "Seeds":                 LIME,
        "3.1 — Deep Research":   TEAL,
        "4 — Operations":        SOFT_GREEN,
        "🧠 Knowledge":          FG,
        "🐙 Claude":             DEEP_PURPL,
        "Clienti":               TEAL,
        "Cicatrici":             HOT_PINK,
        "Dream Cycle":           SOFT_GREEN,
        "5 — Vision":            LIME,
        "Persone":               FG,
    }
    _sem_comm = intel.get("semantic_communities", [])
    if _sem_comm:
        _sem_total = sum(c["size"] for c in _sem_comm) or 1
        _PALETTE = [TEAL, ELEC_BLUE, LIME, HOT_PINK, ORANGE, DEEP_PURPL, SOFT_GREEN, FG]
        def _sem_row(i: int, c: dict) -> str:
            color = _PALETTE[i % len(_PALETTE)]
            label = c["label"][:20]
            return (
                f"  [{color}]{label:<20}[/] "
                f"{_bar(c['size'], _sem_total, 12, color)} "
                f"[{DIM}]{c['size']:>5}[/]"
            )
        area_lines = [
            f"  [{ELEC_BLUE}]🗂 AREE SEMANTICHE[/]  [{DIM}]· community detection[/]",
            div,
            *[_sem_row(i, c) for i, c in enumerate(_sem_comm[:8])],
            "",
        ]
    else:
        fd       = intel.get("folder_dist", {})
        fd_total = sum(fd.values()) or 1
        top_areas = sorted(fd.items(), key=lambda x: x[1], reverse=True)[:8]

        def area_row(folder: str, count: int) -> str:
            color = _AREA_COLORS.get(folder, DIM)
            label = folder[:18]
            return (
                f"  [{color}]{label:<18}[/] "
                f"{_bar(count, fd_total, 14, color)} "
                f"[{DIM}]{count:>5}[/]"
            )

        area_lines = [
            f"  [{ELEC_BLUE}]🗂 AREE SEMANTICHE[/]",
            div,
            *[area_row(f, c) for f, c in top_areas],
            "",
        ]

    # ── Recent activity ───────────────────────────────────────────────────────
    recent_today = intel.get("recent_today", [])
    recent_rows  = [
        f"  [{DIM}]{t}[/]  [{FG}]{n[:42]}[/]"
        for n, t in recent_today[:6]
    ] or [f"  [{DIM}]nessuna modifica oggi[/]"]

    recent_lines = [
        "",
        f"  [{ELEC_BLUE}]🕐 MODIFICATE OGGI[/]  [{DIM}]· {recent_7d} negli ultimi 7gg[/]",
        div,
        "",
        *recent_rows,
        "",
    ]

    # ── Bridge nodes + clusters ───────────────────────────────────────────────
    top_bridges = intel.get("top_bridges",  [])
    top_clusters = intel.get("top_clusters", [])

    bridge_str = "   ".join(
        f"[{ORANGE}]{n[:18]}[/][{DIM}]({s:.3f})[/]"
        for n, s in top_bridges[:4]
    ) or f"[{DIM}]—[/]"

    cluster_rows = [
        f"  [{DIM}]{i + 1}.[/] [{TEAL}]{hub[:30]}[/]  [{DIM}]{size} note[/]"
        for i, (hub, size) in enumerate(top_clusters[:4])
    ]

    conn_lines = [
        "",
        f"  [{ELEC_BLUE}]🕸 TOPOLOGIA[/]",
        div,
        "",
        f"  [{ORANGE}]Bridge (betweenness):[/]  {bridge_str}",
        "",
        f"  [{TEAL}]Cluster principali:[/]",
        "",
        *cluster_rows,
    ]

    # ── CPU section ───────────────────────────────────────────────────────────
    cpu_lines: list[str] = []
    if cpu_percents and cpu_history is not None:
        e_cores = 6
        p_cores = 12
        e_vals  = cpu_percents[:e_cores]
        p_vals  = cpu_percents[e_cores:e_cores + p_cores]
        overall = mean(cpu_percents)
        e_avg   = mean(e_vals) if e_vals else 0.0
        p_avg   = mean(p_vals) if p_vals else 0.0
        ov_col  = _pct_color(overall)
        e_col   = _pct_color(e_avg)
        p_col   = _pct_color(p_avg)
        spark_w = max(20, w - 14)
        cpu_lines = [
            "",
            f"  [{ELEC_BLUE}]⚡ CPU[/]  [{DIM}]· M5 Max 18C[/]",
            div,
            "",
            f"  [{DIM}]Overall  [/][{ov_col}]{_pct_bar(overall, 22)}[/]  [bold {ov_col}]{overall:4.1f}%[/]",
            f"  [{DIM}]S-cores  [/][{e_col}]{_pct_bar(e_avg, 22)}[/]  [{e_col}]{e_avg:4.1f}%[/]  [{DIM}]6E[/]",
            f"  [{DIM}]P-cores  [/][{p_col}]{_pct_bar(p_avg, 22)}[/]  [{p_col}]{p_avg:4.1f}%[/]  [{DIM}]12P[/]",
            "",
            f"  [{ov_col}]{_sparkline(cpu_history, spark_w)}[/]  ⚡ [{DIM}]2-min[/]",
            "",
        ]

    # ── Unified memory section ────────────────────────────────────────────────
    mem_lines: list[str] = []
    if mem and mem_history is not None:
        pct        = mem.get('pct', 0)
        total      = max(mem.get('total', 1), 1)
        free       = mem.get('free', 0)
        swap       = mem.get('swap', 0)
        wired      = mem.get('wired', 0)
        active     = mem.get('active', 0)
        inactive   = mem.get('inactive', 0)
        compressed = mem.get('compressed', 0)
        prs_label, prs_key = mem.get('pressure', ('—', 'ok'))
        prs_col  = {
            'ok': LIME, 'info': ELEC_BLUE,
            'warning': ORANGE, 'error': HOT_PINK,
        }.get(prs_key, DIM)
        prs_emoji = {'ok': '🟢', 'info': '🔵', 'warning': '🟡', 'error': '🔴'}.get(prs_key, '⚪')
        swap_col = HOT_PINK if swap > 0.5e9 else (ORANGE if swap > 0 else DIM)
        mem_col  = _pct_color(pct)
        spark_w  = max(20, w - 14)

        def _seg(label: str, val: int, color: str) -> str:
            b = _pct_bar(val / total * 100, 14)
            return f"   [{color}]{label:<10}[/] [{color}]{b}[/] [bold {color}]{_gb(val):>7}[/]"

        mem_lines = [
            "",
            f"  [{LIME}]🧠 UNIFIED MEMORY[/]  [{DIM}]· {_gb(total)}[/]",
            div,
            "",
            (
                f"  [{mem_col}]{_pct_bar(pct, 22)}[/]  [bold {mem_col}]{pct:4.1f}%[/]"
                f"  {prs_emoji} [bold {prs_col}]{prs_label}[/]"
                f"  [{DIM}]swap[/] [bold {swap_col}]{_gb(swap)}[/]"
            ),
            "",
            _seg("Wired",      wired,      HOT_PINK),
            _seg("Active",     active,     _pct_color(active / total * 100)),
            _seg("Inactive",   inactive,   DIM),
            _seg("Compressed", compressed, ORANGE),
            _seg("Free",       free,       LIME),
            "",
            f"  [{mem_col}]{_sparkline(mem_history, spark_w)}[/]  🧠 [{DIM}]2-min[/]",
            "",
        ]

    # ── Filter hint ───────────────────────────────────────────────────────────
    filter_tabs = "  ".join(
        f"[bold {TEAL}][{m}][/]" if m == filter_mode else f"[{DIM}][{m}][/]"
        for m in FILTER_MODES
    )
    footer = f"  [{DIM}]f filter · r refresh[/]    {filter_tabs}"

    return "\n".join([
        header, "",
        *neural_lines,
        *area_lines,
        *attractor_lines,
        *status_lines,
        *recent_lines,
        *conn_lines,
        *cpu_lines,
        *mem_lines,
        "",
        footer,
    ])
