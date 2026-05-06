"""🐙 M5 MAX WATCHER — Visual Analytics TUI for Apple M5 Max
================================================================================

A real-time Terminal UI cockpit for Apple Silicon M5 Max systems built with
Textual. Streams CPU per-core (6 efficiency + 12 performance), unified memory
breakdown (wired/active/inactive/compressed/free), thermal/battery, disk &
network I/O, plus Polpo "tentacoli" — the live cross-pillar process map of
Astra Digital Marketing background agents (Claude sessions, MCP servers, Jarvis
voice daemon, watchdogs, dashboards).

Design philosophy:
- Data viz first: every glyph, color and emoji is a semantic ancor
- Polpo Design System: pastel rainbow ad onda title, energy palette
  (LIME · ELEC_BLUE · DEEP_PURPL · HOT_PINK · ORANGE · SOFT_GREEN · WHITE)
- Hierarchy explicit: H1 rainbow > H2 colored emoji > H3 cluster > critical
  values WHITE bold > body semantic-colored > chrome DIM
- Zero-architecture-change polish: stable layout, refresh logic, bindings

Tabs:
  🌡 Heatmap     — temporal core heatmap (88s window, Δt=2s)
  📈 Analytics   — min/avg/p95/max + P/S efficiency ratio + 2-min sparkline
  🔝 Processes   — top 16 by CPU+RAM
  🐙 Tentacoli   — Polpo background processes (Claude/MCP/daemons)
  🕸 Graph       — Vault Intelligence Panel (Neural Density · Data Attractors · Topologia)
  📊 KPI         — Business vitals (MRR · Outstanding · Pipeline · Setter)
  📋 Logs        — Cross-system activity stream (leads · payments · calls · voice · security)
  🛡 Sentinel    — Cyber Sentinel live status (Canary watchpoints · Honeypot alerts)

Keybindings: q quit · r refresh · p pause · 1-7 tab switch · f cycle graph filter
Zoom: bottom-right + / − buttons (delegate Cmd+/− to Ghostty)

================================================================================
"""
from __future__ import annotations

# ── Metadata ──────────────────────────────────────────────────────────────────
__title__        = "M5 Max Watcher"
__version__      = "2.5.0-warroom"
__release_date__ = "2026-05-05"
__codename__     = "War Room Edition"
__author__       = "Mattia Calastri"
__email__        = "mattia@digitalastra.it"
__company__      = "Astra Digital Marketing"
__website__      = "https://digitalastra.it"
__license__      = "Proprietary © 2026 Astra Digital Marketing — All Rights Reserved"
__copyright__    = "© 2026 Mattia Calastri · Astra Digital Marketing"
__status__       = "Production"
__pillar__       = "Astra OS · Polpo Cockpit Suite"
__forged_in__    = "sess.1488"   # Sentinel tab — Cyber Sentinel canary + alerts live view
__credits__      = ("Polpo Design System", "Textual", "psutil", "Apple Silicon M5 Max")

import argparse
import asyncio
import functools
import itertools
import json
import math
import os
import re
import subprocess
import time
from collections import deque
from colorsys import hsv_to_rgb
from pathlib import Path
from statistics import mean
from typing import Callable

# sess.1508 round 3: rimosso `import threading` (mai usato — concurrency via
# asyncio.to_thread). Aggiunto `argparse` per CLI flags.

import psutil
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual import events
from textual.widgets import DataTable, Footer, Static, TabbedContent, TabPane, Tabs as TextualTabs
from rich.text import Text as RichText

import data_sources as ds
import vault_parser
import graph_widget
import kpi_widget
import health_widget   # sess.1582: Apple Health vitals (snapshot /tmp/polpo_health_live.json)
import polpo_charts as pc_const   # sess.1508 round 3: shared constants (PRESSURE_*, ALERT_*)

# ── Telemetry spine (sess.1508 round 4) ──────────────────────────────────────
from metrics import (
    Metrics, setup_logger, JsonlWriter, render_debug_panel,
)

logger  = setup_logger()
metrics = Metrics()
jsonl   = JsonlWriter(flush_every=1)
logger.info("m5-watcher boot · pid=%d", os.getpid())

# ── Design tokens ──────────────────────────────────────────────────────────────
_TOKENS = json.loads((Path(__file__).parent / "polpo.tokens.json").read_text())
P = _TOKENS["palette"]
BG, BG_ALT           = P["polpo_bg"], P["polpo_bg_alt"]
TEAL, DIM            = P["polpo_teal"], P["polpo_dim"]
GREEN, YELLOW, RED   = P["polpo_green"], P["polpo_yellow"], P["polpo_red"]
FG, MAG, CYAN, SCAR  = P["polpo_fg"], P["polpo_magenta"], P["polpo_cyan"], P["polpo_scar"]

# ── WAR ROOM overrides ────────────────────────────────────────────────────────
# DNA visivo brand-aligned (sess.1568 color rebalance — psicologia operativa):
# - Chrome/bordi/struttura = teal Polpo (controllo, focus, identità brand)
# - Rosso RISERVATO a P0/danger semantici (sotto 5% superficie superficiale)
# - Principio: il rosso strutturale desensibilizza i veri allarmi
# - Brand canonico: dark #0a0f1a + teal #00d4aa (Tokyo Night Storm family)
DIM    = "#c4cedd"        # più luminoso per leggibilità a font-size 15+
BG_ALT = "#0f1623"        # sfondo pannelli brand-aligned (era #0d0812 viola-notte)
# WR_PRIMARY: chrome/bordi/UI struttura — teal Polpo (era #ff3333 rosso strutturale)
WR_PRIMARY = "#00d4aa"
# WR_ACCENT: heat/warm warning per titoli sezione — allineato a energy_palette.orange
WR_ACCENT  = "#ff8a3d"
# WR_DANGER: rosso semantico solo per P0/critical/breach — token esplicito
WR_DANGER  = "#ff3366"

# ── Energy boost palette (data viz hierarchy) ─────────────────────────────────
WHITE      = "#ffffff"   # critical headlines / super-bright peak
LIME       = "#a8ff60"   # alive/healthy/energy positive
ELEC_BLUE  = "#00e5ff"   # electric accent / live data flow
HOT_PINK   = "#ff2d92"   # attention magenta / spike alert
ORANGE     = "#ff8a3d"   # warm warning / heat
DEEP_PURPL = "#9d4dff"   # P-cluster signature (performance)
SOFT_GREEN = "#5dffaa"   # S-cluster signature (efficiency)

_UNIFEED_HDR = f"[bold {ORANGE}]⚡ UNIFEED[/]  [dim]· M5 Max events[/]"


def health_emoji(s: int) -> str:
    """Emoji semantica per health score: visuale immediata < 1ms cognitive load."""
    if s >= 80: return "💚"
    if s >= 60: return "💛"
    if s >= 40: return "🟧"
    return "❤️"


def _trend_signal(data: deque[float]) -> tuple[float, int] | None:
    """Compute slope + bucket: -2 strong↓, -1 mild↓, 0 flat, +1 mild↑, +2 strong↑.

    Single source of truth — trend_emoji + trend_arrow ora delegano qui
    (sess.1508 round 3 — prima erano due funzioni con stesse soglie ma
    glyph e color drift).
    """
    vals = list(data)[-TREND_WINDOW:]
    if len(vals) < 3:
        return None
    slope = (vals[-1] - vals[0]) / len(vals)
    if slope > 4:    return slope, +2
    if slope > 1.5:  return slope, +1
    if slope < -4:   return slope, -2
    if slope < -1.5: return slope, -1
    return slope, 0


def trend_emoji(data: deque[float]) -> str:
    """Trend arrow stilizzato (▲▲ / ▲ / ● / ▼ / ▼▼)."""
    sig = _trend_signal(data)
    if sig is None:
        return f'[{DIM}]─[/]'
    _, bucket = sig
    return {
        +2: f'[{HOT_PINK}]▲▲[/]',
        +1: f'[{ORANGE}]▲[/]',
         0: f'[{DIM}]●[/]',
        -1: f'[{SOFT_GREEN}]▼[/]',
        -2: f'[{LIME}]▼▼[/]',
    }[bucket]

# ── Visual primitives ──────────────────────────────────────────────────────────
BAR8  = ' ▏▎▍▌▋▊▉█'   # 9-step smooth fill
SPARK = ' ▁▂▃▄▅▆▇█'   # 9-step sparkline

HEAT_MAP = [           # (char, color) by intensity 0-7
    # 8 glyph percettivamente distinti — sess.1508 audit fix:
    # prima `▒` e `▓` apparivano due volte con colori diversi → ambiguità.
    ('·',  DIM),    ('░', DIM),
    ('▒',  CYAN),   ('▓', TEAL),
    ('▚',  YELLOW), ('▞', SCAR),
    ('▣',  RED),    ('█', MAG),
]

TREND_WINDOW = 6
N_CORES = ds.E_CORES + ds.P_CORES   # 18
JARVIS_DIR = Path.home() / ".local" / "run" / "jarvis"

# sess.1508 round 3: rimosso `_VOICE_NAMES` dict — duplicava la lista voci
# letta a runtime da `voices.json` in `voice_data()`. Single source of truth.


def _c(v: float) -> str:
    if v >= 85: return RED
    if v >= 65: return YELLOW
    if v >= 40: return TEAL
    return GREEN


def bar(v: float, w: int = 20) -> str:
    """Sub-pixel smooth bar (8-step block elements)."""
    v = max(0.0, min(100.0, v))
    eighths = round(v / 100 * w * 8)
    full, part = divmod(eighths, 8)
    empty = w - full - (1 if part else 0)
    return ('█' * full + (BAR8[part] if part else '') + ' ' * max(0, empty))[:w]


def stacked_bar(segments: list[tuple[int, str]], total: int, w: int = 38) -> str:
    """Proportional stacked bar — segments: [(bytes, color), ...]"""
    result, used = '', 0
    for val, color in segments:
        n = min(round(val / max(total, 1) * w), w - used)
        if n > 0:
            result += f'[{color}]{"█" * n}[/]'
        used += n
    if used < w:
        result += f'[{DIM}]{"░" * (w - used)}[/]'
    return result


def sparkline(data: deque[float], w: int = 50) -> str:
    """Sparkline normalize **min-max** (sess.1508 audit fix).

    Versione plain (no markup), back-compat con call site esistenti.
    Per la versione con color markup vedi polpo_charts.sparkline.
    """
    vals = list(data)[-w:]
    if not vals:
        return '░' * w
    vmin, vmax = min(vals), max(vals)
    rng = vmax - vmin
    n = len(SPARK) - 1   # 8
    if rng < 1e-9:
        return SPARK[n // 2] * len(vals)
    return ''.join(SPARK[min(n, int((v - vmin) / rng * n))] for v in vals)


def heat(v: float) -> tuple[str, str]:
    return HEAT_MAP[min(7, int(v / 100 * 8))]


def trend_arrow(data: deque[float]) -> str:
    """Trend ASCII arrow (↑ ↗ ─ ↘ ↓) — sess.1508 round 3 delega a _trend_signal."""
    sig = _trend_signal(data)
    if sig is None:
        return f'[{DIM}]─[/]'
    _, bucket = sig
    return {
        +2: f'[{RED}]↑[/]',
        +1: f'[{YELLOW}]↗[/]',
         0: f'[{DIM}]─[/]',
        -1: f'[{TEAL}]↘[/]',
        -2: f'[{GREEN}]↓[/]',
    }[bucket]


def p_pct(vals: list[float], p: float) -> float:
    if not vals: return 0.0
    s = sorted(vals)
    return s[max(0, min(len(s) - 1, int(len(s) * p)))]


def gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}G"


def trunc(s: str, n: int) -> str:
    """Tronca con ellipsis Unicode (…) — sess.1508 audit fix.

    Sostituisce gli `s[:N]` muti che producevano "supabase-postgres-mcp-ser".
    """
    if n <= 0:
        return ""
    if len(s) <= n:
        return s
    if n == 1:
        return "…"
    return s[: n - 1] + "…"


def health_score(cpu: float, ram: float, load: float) -> tuple[int, str]:
    """0-100 composite score (higher = healthier)."""
    s = int(max(0, 100 - cpu) * 0.35 +
            max(0, 100 - ram) * 0.45 +
            max(0, 100 - load / N_CORES * 100) * 0.20)
    color = GREEN if s >= 75 else TEAL if s >= 55 else YELLOW if s >= 35 else RED
    return s, color


# ── Panel renderers ────────────────────────────────────────────────────────────

def render_cpu(percents: list[float], history: deque[float],
               disk: dict, net: dict) -> str:
    if not percents:
        return f"[{DIM}]🔄 Probing 18 cores…[/]"

    e_vals = percents[:ds.E_CORES]
    p_vals = percents[ds.E_CORES: ds.E_CORES + ds.P_CORES]
    e_avg  = mean(e_vals)
    p_avg  = mean(p_vals)
    la1, la5, la15 = ds.load_avg()
    overall = mean(percents)
    hs, hc  = health_score(overall, 0, la1)   # mem not available here, pass 0
    h_emoji = health_emoji(hs)

    lines = [
        "",
        f"  {h_emoji} [bold {hc}]{hs}[/]   ⚖ [bold {TEAL}]{la1:.2f}[/] [{DIM}]{la5:.2f} {la15:.2f}[/]",
        f"  [{_c(overall)}]{bar(overall, 24)}[/]  [bold {_c(overall)}]{overall:4.1f}%[/] {trend_emoji(history)}",
        "",
        f"  [{SOFT_GREEN}]🍃 S[/]  [bold {_c(e_avg)}]{e_avg:4.1f}%[/]",
    ]
    for i, v in enumerate(e_vals):
        lines.append(f"   [{SOFT_GREEN}]S{i}[/] [{_c(v)}]{bar(v, 14)}[/] [bold {_c(v)}]{v:3.0f}%[/]")

    lines += [
        "",
        f"  [{DEEP_PURPL}]🚀 P[/]  [bold {_c(p_avg)}]{p_avg:4.1f}%[/]",
    ]
    for i, v in enumerate(p_vals):
        lines.append(f"   [{DEEP_PURPL}]P{i:02d}[/] [{_c(v)}]{bar(v, 14)}[/] [bold {_c(v)}]{v:3.0f}%[/]")

    lines += [
        "",
        f"  💾 [bold {CYAN}]↓{disk.get('read', 0):5.1f}[/] [bold {HOT_PINK}]↑{disk.get('write', 0):5.1f}[/]   "
        f"🌐 [bold {CYAN}]↓{net.get('recv', 0):5.2f}[/] [bold {HOT_PINK}]↑{net.get('sent', 0):5.2f}[/]",
    ]
    return "\n".join(lines)


def render_mem(m: dict, history: deque[float], cpu_avg: float, load: float) -> str:
    if not m:
        return f"[{DIM}]🔄 Reading unified memory…[/]"

    total = m['total']
    prs_label, prs_key = m['pressure']
    # sess.1508 round 3: dict pressure unificati in polpo_charts (3 copie eliminate).
    prs_color = pc_const.PRESSURE_COLOR.get(prs_key, DIM)
    prs_emoji = pc_const.PRESSURE_EMOJI.get(prs_key, '⚪')
    swap_color = HOT_PINK if m['swap'] > 0.5e9 else (ORANGE if m['swap'] > 0 else DIM)
    swap_emoji = '🔴' if m['swap'] > 0.5e9 else ('🟡' if m['swap'] > 0 else '⚫')
    hs, hc = health_score(cpu_avg, m['pct'], load)
    h_emoji = health_emoji(hs)

    # Stacked proportional bar
    seg_bar = stacked_bar([
        (m['wired'],      HOT_PINK),
        (m['active'],     _c(m['active'] / total * 100)),
        (m['inactive'],   DIM),
        (m['compressed'], ORANGE),
        (m['free'],       LIME),
    ], total, w=38)

    seg_labels = (
        f"[{HOT_PINK}]W[/][bold {HOT_PINK}]{gb(m['wired'])}[/] "
        f"[{TEAL}]A[/][bold {TEAL}]{gb(m['active'])}[/] "
        f"[{DIM}]I [/][bold]{gb(m['inactive'])}[/] "
        f"[{ORANGE}]Z[/][bold {ORANGE}]{gb(m['compressed'])}[/] "
        f"[{LIME}]F[/][bold {LIME}]{gb(m['free'])}[/]"
    )

    def seg(emoji: str, label: str, val: int, color: str) -> str:
        b = bar(val / total * 100, 14)
        return f"   {emoji} [{DIM}]{label:<10}[/] [bold {color}]{b}[/] [bold {color}]{gb(val):>7}[/]"

    return "\n".join([
        "",
        f"  {h_emoji} [bold {hc}]{hs}[/]   {prs_emoji} [bold {prs_color}]{prs_label}[/]   {swap_emoji} [bold {swap_color}]{gb(m['swap'])}[/]",
        "",
        f"  {seg_bar}  [bold {_c(m['pct'])}]{m['pct']:4.1f}%[/] {trend_emoji(history)}",
        f"  {seg_labels}",
        "",
        seg("🩷", "Wired",      m['wired'],      HOT_PINK),
        seg("🔷", "Active",     m['active'],     _c(m['active'] / total * 100)),
        seg("⚫", "Inactive",   m['inactive'],   DIM),
        seg("🟧", "Compressed", m['compressed'], ORANGE),
        seg("🟢", "Free",       m['free'],       LIME),
    ])


def _trim_deque(d: deque, n: int) -> list[float]:
    """Take last `n` items da deque senza materializzare l'intera lista.

    sess.1508 round 2 perf fix: heatmap chiamava `list(deque)[-cols:]` 18
    volte ogni 2s = 36 list allocation — ora `islice` lavora a O(min(len,n))
    e materializza solo i last-n.
    """
    if not d:
        return []
    skip = max(0, len(d) - n)
    return list(itertools.islice(d, skip, len(d)))


def render_heatmap(core_history: dict[int, deque[float]], cols: int = 44) -> str:
    """Temporal heatmap — M5 fingerprint with time axis.

    sess.1508 audit fix: rimosso codice morto duplicato (axis costruito due
    volte) e legenda allineata con i nuovi 8 glyph distinti.
    Round 2: deque slice via itertools.islice (no GC pressure).
    """
    tick_every = 10   # mark every 10 cols = 20 s
    total_secs = cols * 2

    # Time axis — single pass
    axis_chars = list(' ' * (cols + 6))
    for i in range(0, cols, tick_every):
        secs_ago = (cols - i) * 2
        label = f'{secs_ago}s'
        for j, c in enumerate(label):
            if i + 6 + j < len(axis_chars):
                axis_chars[i + 6 + j] = c
    axis_str = ''.join(axis_chars)

    lines = [
        "",
        f"[bold {ORANGE}]🔥 CPU HEATMAP[/]  [{DIM}]Δt=2s · window={total_secs}s[/]  "
        f"[{DIM}]·░[/]<25  [{CYAN}]▒[/][{TEAL}]▓[/]25-50  "
        f"[{YELLOW}]▚[/][{SCAR}]▞[/]50-75  [{RED}]▣[/][{MAG}]█[/]>75",
        f"[italic {DIM}]The memory of work, rendered as heat — time scrolls left, intensity blooms hot.[/]",
        "",
        f"[{DIM}]{axis_str}[/]  [{DIM}]avg[/]",
        f"  [{SOFT_GREEN}]🍃 S-CORES[/] [{DIM}](efficiency)[/]",
    ]

    for i in range(ds.E_CORES):
        vals = _trim_deque(core_history.get(i, deque()), cols)
        cells = ''.join(f'[{heat(v)[1]}]{heat(v)[0]}[/]' for v in vals)
        # Pad left if not enough data
        pad = cols - len(vals)
        pad_str = f'[{DIM}]{" " * pad}[/]' if pad > 0 else ''
        avg = mean(vals) if vals else 0
        lines.append(f"  [{SOFT_GREEN}]S{i}[/] {pad_str}{cells}  [bold {_c(avg)}]{avg:4.1f}%[/]")

    lines += ["", f"  [{DEEP_PURPL}]🚀 P-CORES[/] [{DIM}](performance)[/]"]

    for i in range(ds.P_CORES):
        idx  = ds.E_CORES + i
        vals = _trim_deque(core_history.get(idx, deque()), cols)
        cells = ''.join(f'[{heat(v)[1]}]{heat(v)[0]}[/]' for v in vals)
        pad = cols - len(vals)
        pad_str = f'[{DIM}]{" " * pad}[/]' if pad > 0 else ''
        avg = mean(vals) if vals else 0
        lines.append(f"  [{DEEP_PURPL}]P{i:02d}[/] {pad_str}{cells}  [bold {_c(avg)}]{avg:4.1f}%[/]")

    return "\n".join(lines)


def render_analytics(cpu_h: deque[float], mem_h: deque[float],
                     core_h: dict[int, deque[float]],
                     cpu_now: float, mem_now: float, load: float,
                     spark_w: int = 56) -> str:

    def stat_row(emoji: str, label: str, data: deque[float], unit: str = '%') -> str:
        vals = list(data)
        if not vals:
            return f"   {emoji} [{DIM}]{label:<14}[/]  [{DIM}]—[/]"
        mn = min(vals); mx = max(vals); avg = mean(vals)
        p95 = p_pct(vals, 0.95);  arr = trend_emoji(data)
        col = _c(avg)
        return (
            f"   {emoji} [{DIM}]{label:<14}[/]"
            f" [{LIME}]{mn:5.1f}{unit}[/]"
            f" [bold {col}]{avg:5.1f}{unit}[/]"
            f" [{ORANGE}]{p95:5.1f}{unit}[/]"
            f" [{HOT_PINK}]{mx:5.1f}{unit}[/]"
            f" {arr}"
        )

    # Cluster aggregates over time
    n = max((len(v) for v in core_h.values() if v), default=0)
    e_agg: list[float] = []
    p_agg: list[float] = []
    for si in range(n):
        ev = [list(core_h[ci])[si] for ci in range(ds.E_CORES)
              if len(core_h.get(ci, deque())) > si]
        pv = [list(core_h[ds.E_CORES + ci])[si] for ci in range(ds.P_CORES)
              if len(core_h.get(ds.E_CORES + ci, deque())) > si]
        if ev: e_agg.append(mean(ev))
        if pv: p_agg.append(mean(pv))
    e_dq = deque(e_agg, maxlen=60)
    p_dq = deque(p_agg, maxlen=60)

    # Health score + composite gauge
    hs, hc = health_score(cpu_now, mem_now, load)
    hs_bar = bar(hs, 24)
    h_emoji = health_emoji(hs)

    # P/S efficiency ratio
    ratio_txt = '—'
    ratio_bar = ''
    if e_agg and p_agg:
        ratio = mean(p_agg[-10:]) / max(mean(e_agg[-10:]), 0.1)
        ratio_bar = bar(min(ratio * 20, 100), 18)
        if ratio > 3:
            note_emoji = "🚀"; note = "P heavy"
        elif ratio > 0.8:
            note_emoji = "⚖"; note = "balanced"
        else:
            note_emoji = "🍃"; note = "S dominant"
        ratio_txt = f"{note_emoji} [bold {ELEC_BLUE}]{ratio:.2f}×[/] [{DIM}]({note})[/]  [{ELEC_BLUE}]{ratio_bar}[/]"

    lines = [
        "",
        f"[bold {ELEC_BLUE}]📊 SYSTEM ANALYTICS[/]  [{DIM}]{len(cpu_h)} samples · {len(cpu_h)*2}s window[/]",
        f"[italic {DIM}]Where averages reveal the truth that instants hide — the slow drift behind every spike.[/]",
        "",
        f"  [{hc}]{hs_bar}[/]  {h_emoji} [bold {hc}]HEALTH[/] [bold {WHITE}]{hs}[/][{DIM}]/100[/]",
        "",
        f"  [{DIM}]━━ STATISTICS     min    avg    p95    max   trend[/]",
        "",
        stat_row("⚡", "Overall CPU",     cpu_h),
    ]
    if e_dq: lines.append(stat_row("🍃", "S-cluster (6E)",  e_dq))
    if p_dq: lines.append(stat_row("🚀", "P-cluster (12P)", p_dq))
    lines.append(stat_row("🧠", "RAM used",        mem_h))
    lines += [
        "",
        f"  [{DEEP_PURPL}]━━ P/S EFFICIENCY RATIO[/]",
        "",
        f"   {ratio_txt}",
        "",
        f"  [{ORANGE}]━━ 2-MIN TIMELINE[/]",
        "",
        f"  [{_c(cpu_now)}]{sparkline(cpu_h, spark_w)}[/]  ⚡ [{DIM}]cpu[/]",
        f"  [{_c(mem_now)}]{sparkline(mem_h, spark_w)}[/]  🧠 [{DIM}]ram[/]",
        "",
    ]
    return "\n".join(lines)


# ── Voice data reader + renderer ─────────────────────────────────────────────

def _jread(name: str, default: str = "") -> str:
    """Read a jarvis runtime file safely."""
    try:
        return (JARVIS_DIR / name).read_text(errors="ignore").strip()
    except OSError:
        return default


def voice_data() -> dict:
    """Read live voice state from ~/.local/run/jarvis/ — all file I/O, no subprocess."""
    import struct as _struct

    state    = _jread("stt_state", "offline")
    engine   = _jread("stt_engine.txt", "—").upper()
    voice    = _jread("voice_selected", "—")
    autosend = _jread("jarvis_autosend_state", "off")
    loop     = _jread("jarvis_voice_loop_state", "off")

    diag: dict[str, str] = {}
    for part in _jread("stt_diag.txt").split():
        if "=" in part:
            k, v = part.split("=", 1)
            diag[k] = v

    history: list[dict] = []
    try:
        raw = (JARVIS_DIR / "stt_history.jsonl").read_text(errors="ignore")
        for line in raw.strip().splitlines()[-10:]:
            try:
                entry = json.loads(line)
                if "ts" in entry and "text" in entry:
                    history.append(entry)
            except (json.JSONDecodeError, ValueError):
                pass
    except OSError:
        pass

    # Audio levels waveform (float32 LE, last 60 samples)
    levels: list[float] = []
    try:
        raw_bytes = (JARVIS_DIR / "stt_levels.bin").read_bytes()
        n = len(raw_bytes) // 4
        if n > 0:
            levels = list(_struct.unpack(f"{n}f", raw_bytes[:n * 4]))
    except OSError:
        pass

    # Full voice name from voices.json (overrides hardcoded dict if available)
    voice_full = ""
    try:
        vj = json.loads((JARVIS_DIR / "voices.json").read_text(errors="ignore"))
        for v in vj.get("voices", []):
            if v.get("key", "").lower() == voice.lower():
                voice_full = v.get("name", "")
                break
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    return {
        "state":      state,
        "engine":     engine,
        "voice":      voice,
        "voice_full": voice_full,
        "autosend":   autosend,
        "loop":       loop,
        "threshold":  diag.get("threshold", "—"),
        "ambient":    diag.get("cal_mean", "—"),
        "history":    history,
        "levels":     levels,
    }


def _level_bar(levels: list[float], w: int = 40) -> str:
    """Render audio levels as colored sparkline waveform."""
    if not levels:
        return f'[{DIM}]{"─" * w}[/]'
    vals = levels[-w:]
    mx = max(max(vals), 1e-6)
    out = []
    for v in vals:
        norm = v / mx
        if norm > 0.75:   color = HOT_PINK
        elif norm > 0.45: color = ORANGE
        elif norm > 0.2:  color = TEAL
        else:             color = DIM
        ch = SPARK[min(8, int(norm * 8.99))]
        out.append(f"[{color}]{ch}[/]")
    pad = w - len(vals)
    if pad > 0:
        out = [f'[{DIM}]{" " * pad}[/]'] + out
    return "".join(out)


_LABEL_COLOR = {
    'LOOP APERTO':  ORANGE,
    'IN_PROGRESS':  ELEC_BLUE,
    'WAITING':      YELLOW,
    'PENDING':      YELLOW,
    'PUSH PENDING': HOT_PINK,
    'FIXED':        LIME,
    'READY':        LIME,
    'VERIFIED':     SOFT_GREEN,
    'CREATED':      SOFT_GREEN,
    'DONE':         DIM,
}

_P0_HIGHLIGHT_RE = re.compile(
    r'(Lunedi|Martedi|Mercoledi|Giovedi|Venerdi|Sabato|Domenica|\d{2}:\d{2})'
)


def render_focus(fd: dict) -> str:
    """Render 🎯 FOCUS + 🚨 RADAR panel — active task, P0 actions, blockers."""
    sess       = fd.get('session_str', '—')
    tesi       = fd.get('tesi', '')
    task       = fd.get('active_task', '')
    label      = fd.get('active_label', '')
    updated    = fd.get('updated_ts', '')
    p0_actions = fd.get('p0_actions', [])
    blocchi    = fd.get('blocchi', [])

    label_col = _LABEL_COLOR.get(label.upper(), CYAN)
    label_str = (f" [{label_col}][{label}][/]" if label else '')

    task_line = (
        f"  [{WHITE}]{task}[/]{label_str}"
        if task else
        f"  [{DIM}]nessun task attivo — vault non aggiornato[/]"
    )
    tesi_line = (
        f"  [{DIM}]Ultima tesi:[/] [{TEAL}]{tesi}[/]"
        if tesi else ''
    )
    updated_str = f"  [{DIM}]aggiornato {updated}[/]" if updated else ''

    lines = [
        f"[bold {LIME}]🎯 FOCUS[/]  [{DIM}]· sess.{sess} · cosa sta costruendo il Polpo[/]",
        task_line,
    ]
    if tesi_line:
        lines.append(tesi_line)
    if updated_str:
        lines.append(updated_str)

    # ── P0 Radar ─────────────────────────────────────────────────────────
    if p0_actions:
        lines.append("")
        lines.append(f"[bold {HOT_PINK}]🚨 P0 RADAR[/]  [{DIM}]· prossime azioni critiche[/]")
        for action in p0_actions:
            # Highlight time markers (Lunedi, Martedi, HH:MM patterns)
            highlighted = _P0_HIGHLIGHT_RE.sub(
                lambda m: f'[{YELLOW}]{m.group()}[/]',
                action
            )
            lines.append(f"  [{HOT_PINK}]▸[/] {highlighted}")

    # ── Blocchi ──────────────────────────────────────────────────────────
    if blocchi:
        lines.append("")
        lines.append(f"[bold {ORANGE}]⛔ BLOCCHI[/]  [{DIM}]· cosa rallenta[/]")
        for b in blocchi:
            lines.append(f"  [{ORANGE}]·[/] [{DIM}]{b}[/]")

    return "\n".join(lines)


def render_voice(vd: dict, level_w: int = 40) -> str:
    """Render voice panel — mirrors Polpo Voice app layout in Textual markup."""
    state    = vd["state"]
    voice    = vd["voice"]
    engine   = vd["engine"]
    autosend = vd["autosend"]
    loop_st  = vd["loop"]
    threshold = vd["threshold"]
    ambient  = vd["ambient"]
    history  = vd["history"]
    levels   = vd["levels"]

    # State → color mapping (stt_state values: transcribing/idle/speaking/offline)
    state_color = {
        "transcribing": LIME,
        "idle":         TEAL,
        "speaking":     HOT_PINK,
        "offline":      DIM,
    }.get(state, DIM)
    state_dot = f"[{state_color}]●[/]"

    # Pill colors
    out_color  = HOT_PINK if autosend == "on" else DIM
    out_label  = "Attivo"  if autosend == "on" else "Silente"
    in_color   = LIME if state == "transcribing" else (TEAL if state == "idle" else DIM)
    in_label   = "Attivo" if state == "transcribing" else ("In ascolto" if state == "idle" else "Spento")
    loop_color = ELEC_BLUE if loop_st not in ("off", "") else DIM
    loop_label = loop_st.title() if loop_st not in ("off", "") else "Manual"
    wave_line  = _level_bar(levels, level_w)

    voice_display = vd.get("voice_full") or voice.title()
    voice_star = f"⭐ [bold {ELEC_BLUE}]{voice_display}[/]"

    # Transcriptions with relative timestamps
    now = time.time()
    trans_lines: list[str] = []
    for entry in reversed(history[-5:]):
        age = now - entry["ts"]
        if age < 8:
            ts_str = f"[{HOT_PINK}]ora[/]"
            bg = BG_ALT
        elif age < 60:
            ts_str = f"[{DIM}]{int(age)}s fa[/]"
            bg = BG
        elif age < 3600:
            ts_str = f"[{DIM}]{int(age/60)}m fa[/]"
            bg = BG
        else:
            ts_str = f"[{DIM}]{int(age/3600)}h fa[/]"
            bg = BG
        trans_lines.append(f"  {ts_str}  [{FG}]{trunc(entry['text'], 52)}[/]")

    n_total = len(history)

    lines = [
        # ── Header
        f"  [bold {HOT_PINK}]🐙 Polpo[/] [{DIM}]·[/] [bold {WHITE}]Voice[/]  "
        f"{state_dot} [{DIM}]{state}[/]",
        f"  [{DIM}]🎙 Microfono MacBook Pro  ·  🔊 Altoparlanti[/]",
        "",
        # ── State pills
        f"  [{DIM}][[/][bold {out_color}]🔇 OUT[/] [{DIM}]][/] {out_label}"
        f"   [{DIM}][[/][bold {in_color}]🎤 IN[/] [{DIM}]][/] {in_label}"
        f"   [{DIM}][[/][bold {loop_color}]🔄 LOOP[/] [{DIM}]][/] {loop_label}"
        f"   [{DIM}][[/][bold {TEAL}]💬 DIALOG[/] [{DIM}]][/] Solo testo",
        "",
        # ── Waveform
        f"  {wave_line}",
        "",
        # ── Voice selection
        f"  [{DIM}]VOCE DEL POLPO[/]",
        f"  {voice_star}  [{HOT_PINK}]▶[/]",
        "",
        # ── Transcriptions
        f"  [{DIM}]TRASCRIZIONI RECENTI[/]  [{DIM}]{n_total} totali[/]",
    ]
    if trans_lines:
        lines.extend(trans_lines)
    else:
        lines.append(f"  [{DIM}]nessuna trascrizione recente[/]")

    lines += [
        "",
        # ── Diagnostics
        f"  [{DIM}]DIAGNOSTICA STT[/]",
        f"  [{ELEC_BLUE}]ENGINE[/] [bold {FG}]{engine}[/]   "
        f"[{DEEP_PURPL}]THRESHOLD[/] [bold {FG}]{threshold}[/]   "
        f"[{TEAL}]AMBIENT[/] [bold {FG}]{ambient}[/]",
        "",
        # ── Footer
        f"  [{DIM}]dev@digitalastra.it[/]",
        f"  [bold {HOT_PINK}]🐙[/] [{DIM}]tentacolo vocale[/]",
    ]
    return "\n".join(lines)


def render_feed(feed: deque) -> str:
    """Unified event feed — timestamped system transitions.

    sess.1508 audit fix: empty state standardizzato (icona + msg) — coerente
    con kpi_widget / graph_widget.
    """
    if not feed:
        return f"\n  [{DIM}]🔄 in attesa di eventi…[/]\n  [{DIM}]   transizioni: pressure · swap · CPU spike · voice · MRR[/]"
    lines = [""]
    for line in feed:
        lines.append(f"  {line}")
    return "\n".join(lines)


def _fmt_age(sec: float | None) -> str:
    """Compact age formatting — '<5s', '12s', '3m', '1h', '2d'."""
    if sec is None:
        return '—'
    if sec < 5:
        return '<5s'
    if sec < 60:
        return f"{int(sec)}s"
    if sec < 3600:
        return f"{int(sec / 60)}m"
    if sec < 86400:
        return f"{int(sec / 3600)}h"
    return f"{int(sec / 86400)}d"


# sess.1534 round 4: lazy import dei 5 moduli roadmap per non penalizzare
# il startup se uno dei moduli ha errori. Cached al primo successo.
_ROADMAP_MODULES_CACHE: dict = {}


def _log_render_error(section: str, exc: Exception) -> None:
    """Log render error to ~/.local/share/polpo/m5_render_errors.log + stderr.

    Antifragile pattern (sess.1534 round 10): silent failures attraversano
    3+ silenziatori. Qui catturiamo type+repr in un log persistente cosi'
    Mattia li puo' grep dopo session-end, e il caller mostra un badge rosso
    visibile invece di una stringa vuota.
    """
    try:
        import sys
        import traceback
        log_dir = Path.home() / ".local/share/polpo"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "m5_render_errors.log"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        tb = traceback.format_exception_only(type(exc), exc)[0].strip()
        line = f"[{ts}] {section}: {tb}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
        # Echo a stderr per visibilita' immediata in dev mode
        try:
            sys.stderr.write(f"[m5.render_error] {section}: {tb}\n")
        except Exception:
            pass
    except Exception:
        # Logging itself failed — silent fallback OK (mai bloccare il render).
        pass


def _render_error_badge(section: str, exc: Exception) -> str:
    """Visible red badge per failure di una sezione cockpit.

    Sostituisce la stringa vuota: il badge attraversa il rendering Rich e
    arriva all'occhio di Mattia (UX antifragile sess.1534 round 10).
    """
    return f"[bold #ff3366]⚠ {section} render failed: {type(exc).__name__}[/]"


def _lazy_roadmap_module(name: str):
    if name in _ROADMAP_MODULES_CACHE:
        return _ROADMAP_MODULES_CACHE[name]
    try:
        mod = __import__(name)
        _ROADMAP_MODULES_CACHE[name] = mod
        return mod
    except Exception as e:
        _log_render_error(f"import:{name}", e)
        _ROADMAP_MODULES_CACHE[name] = None
        return None


def _safe_render_polestar() -> str:
    m = _lazy_roadmap_module("roadmap_polestar")
    if m is None:
        return ""
    try:
        return m.render_polestar_strip()
    except Exception as e:
        _log_render_error("polestar", e)
        return _render_error_badge("polestar", e)


def _safe_render_vectors() -> str:
    m = _lazy_roadmap_module("roadmap_vectors")
    if m is None:
        return ""
    try:
        return m.render_vectors_strip()
    except Exception as e:
        _log_render_error("vectors", e)
        return _render_error_badge("vectors", e)


def _safe_render_traps() -> str:
    m = _lazy_roadmap_module("roadmap_traps")
    if m is None:
        return ""
    try:
        return m.render_traps_banner() or ""
    except Exception as e:
        _log_render_error("traps", e)
        return _render_error_badge("traps", e)


def _safe_render_filaments() -> str:
    m = _lazy_roadmap_module("roadmap_filaments")
    if m is None:
        return ""
    try:
        return m.render_filaments_section()
    except Exception as e:
        _log_render_error("filaments", e)
        return _render_error_badge("filaments", e)


def _safe_render_blocks() -> str:
    m = _lazy_roadmap_module("roadmap_blocks")
    if m is None:
        return ""
    try:
        return m.render_blocks_section()
    except Exception as e:
        _log_render_error("blocks", e)
        return _render_error_badge("blocks", e)


def _safe_render_outstanding() -> str:
    """Round 6 — outstanding aging dynamic per cliente."""
    m = _lazy_roadmap_module("roadmap_outstanding")
    if m is None:
        return ""
    try:
        return m.render_outstanding_section()
    except Exception as e:
        _log_render_error("outstanding", e)
        return _render_error_badge("outstanding", e)


def _render_activity_header(meta: dict) -> str:
    """Dynamic ACTIVITY STREAM header — sess.1534.

    Replaces the static "Every signal from every tentacolo" subtitle with
    operational telemetry: sources alive, last event age, P0/P1 counts,
    drift labels. The intent is that one glance at the box answers
    "is my cockpit working?" without scrolling the entries.
    """
    total   = meta.get('sources_total', 0)
    live    = meta.get('sources_live', 0)
    stale   = meta.get('sources_stale', 0)
    dead    = meta.get('sources_dead', 0)
    p0      = meta.get('p0_count', 0)
    p1      = meta.get('p1_count', 0)
    age     = meta.get('last_age_sec')
    drift   = meta.get('drift_labels', []) or []
    entries = meta.get('total_entries', 0)

    # Sources health bar — colorato in base al ratio live/total
    if total == 0:
        ratio_col = DIM
    elif live / total >= 0.75:
        ratio_col = LIME
    elif live / total >= 0.50:
        ratio_col = ORANGE
    else:
        ratio_col = RED
    sources_str = f"[{ratio_col}]{live}/{total} live[/]"
    if stale:
        sources_str += f" [{ORANGE}]· {stale} stale[/]"
    if dead:
        sources_str += f" [{RED}]· {dead} dead[/]"

    # P0/P1 indicators — silenziosi quando 0, urlanti quando attivi
    sev_str = ''
    if p0:
        sev_str += f" · [bold {RED}]{p0} P0[/]"
    if p1:
        sev_str += f" · [bold {ORANGE}]{p1} P1[/]"
    if not (p0 or p1):
        sev_str = f" · [{DIM}]{entries} signals · all info[/]"

    age_str = f"[{DIM}]last {_fmt_age(age)} ago[/]" if age is not None else f"[{DIM}]no events yet[/]"

    # Drift labels riga 2 — solo se >=1 dead source, altrimenti italic poetic line
    if drift:
        drift_str = f"[{RED}]⚠ drift:[/] [{DIM}]{', '.join(drift[:5])}[/]"
        if len(drift) > 5:
            drift_str += f" [{DIM}]+{len(drift) - 5}[/]"
        line2 = drift_str
    else:
        line2 = f"[italic {DIM}]Every signal from every tentacolo — leads, payments, calls, voice, security.[/]"

    line1 = (
        f"[bold {ORANGE}]📋 ACTIVITY STREAM[/]  "
        f"[{DIM}]· cross-system log cascade[/]  "
        f"[{DIM}]·[/] {sources_str} [{DIM}]·[/] {age_str}{sev_str}"
    )
    return f"{line1}\n{line2}"


# ── Rainbow title (HSV flow, light pastel + wave luminosity modulation) ───────
RAINBOW_SAT     = 0.55   # 0=grey, 1=neon. 0.55 = leggero/pastel
RAINBOW_VAL     = 1.00   # luminosità piena di base
RAINBOW_SPREAD  = 0.07   # offset di hue tra una lettera e la successiva
RAINBOW_SPEED   = 0.020  # incremento di phase per tick → ~12.5s ciclo a 4fps
RAINBOW_FPS_DT  = 0.25   # 250ms tick = 4fps (sess.1494 fix CPU runaway: era 0.10/10fps → 100% core)
RAINBOW_PHASE_Q = 3      # quantizza phase a 3 decimali → cache hit su _rainbow_hex

# Wave modulation — onda di luce che scorre lungo il titolo
WAVE_AMP        = 0.45   # ampiezza dim oscillation (0=no wave, 1=max dim a metà ciclo)
WAVE_FREQ       = 0.32   # frequenza spaziale: ~3 lettere per ciclo, onda visibile
WAVE_SPEED      = 2.0    # velocità di scorrimento dell'onda (× phase)

_ASCII_POLPO = (
    " ██████╗  ██████╗ ██╗      ██████╗  ██████╗ ",
    " ██╔══██╗██╔═══██╗██║     ██╔══██╗██╔═══██╗",
    " ██████╔╝██║   ██║██║     ██████╔╝██║   ██║",
    " ██╔═══╝ ██║   ██║██║     ██╔═══╝ ██║   ██║",
    " ██║     ╚██████╔╝███████╗██║     ╚██████╔╝",
    " ╚═╝      ╚═════╝ ╚══════╝╚═╝      ╚═════╝ ",
)

@functools.lru_cache(maxsize=16384)
def _rainbow_hex(idx: int, phase: float, wave: bool = True) -> str:
    """HSV rainbow + optional wave luminosity modulation per letter.

    Hue cambia spazialmente (idx) + temporalmente (phase) → flusso arcobaleno.
    Quando wave=True, V (luminosità) oscilla con sin(2π·(idx·WAVE_FREQ - phase·WAVE_SPEED))
    creando un'onda di luce che scorre lungo il testo.

    sess.1494: lru_cache su (idx, phase_quantized, wave) — phase arriva già
    quantizzato dal watcher → hit ratio ~99% dopo warmup (1 ciclo = ~125 chiavi).
    """
    h = (idx * RAINBOW_SPREAD + phase) % 1.0
    if wave:
        wave_y = math.sin(2 * math.pi * (idx * WAVE_FREQ - phase * WAVE_SPEED))
        v = RAINBOW_VAL * (1.0 - WAVE_AMP * (1.0 - wave_y) * 0.5)
    else:
        v = RAINBOW_VAL
    r, g, b = hsv_to_rgb(h, RAINBOW_SAT, v)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def rainbow_text(text: str, phase: float, wave: bool = True) -> str:
    out = []
    for i, ch in enumerate(text):
        if ch.isspace():
            out.append(ch)
        else:
            out.append(f"[{_rainbow_hex(i, phase, wave)}]{ch}[/]")
    return ''.join(out)


def _format_uptime(secs: int) -> str:
    if secs >= 86400:
        d, rem = divmod(secs, 86400)
        h = rem // 3600
        return f"{d}d{h:02d}h"
    if secs >= 3600:
        h, rem = divmod(secs, 3600)
        m = rem // 60
        return f"{h}h{m:02d}m"
    m, s = divmod(secs, 60)
    return f"{m}m{s:02d}s"


def _count_claude_mcp() -> dict[str, int]:
    """Conta processi Claude (CLI sessions) e MCP server unique attivi.
    Pattern detection robusto: name può essere version-renamed (cicatrice sess.1192:
    psutil legge name='2.1.123' invece di 'claude' per CC).
    MCP count = server unique distinti (NON instance × sessione, che farebbe N×M).
    """
    mcp_needles = (
        '@modelcontextprotocol',
        'whatsapp-mcp-ts',
        'hostinger-api-mcp',
        '@upstash/context7-mcp',
        'youtube-transcript-mcp',
        'mcp-servers/ghl',
        'telegram-mcp',
        'stripe-mcp',
        'firecrawl-mcp',
        'sentry-mcp',
    )
    claude_count = 0
    mcp_unique: set[str] = set()
    for p in psutil.process_iter(['name', 'cmdline']):
        try:
            name = (p.info.get('name') or '').lower()
            cmdline_list = p.info.get('cmdline') or []
            cmdline = ' '.join(cmdline_list).lower()

            is_claude = (
                name == 'claude'
                or '/bin/claude' in cmdline
                or '/.claude/local/claude' in cmdline
                or (name and name.startswith('2.') and 'claude' in cmdline)
            )
            if is_claude:
                claude_count += 1
                continue

            for needle in mcp_needles:
                if needle in cmdline:
                    mcp_unique.add(needle)
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {'claude': claude_count, 'mcp': len(mcp_unique)}


def _claude_session_number() -> str:
    """Best-effort lettura sessione corrente.
    Probe paths in ordine: env CLAUDE_SESSION_N, active_claims.json (più recente),
    session_current.md (multipli candidate path), session_history.md fallback.
    """
    env = os.environ.get("CLAUDE_SESSION_N")
    if env:
        return env

    home = Path.home()
    candidates = [
        # active_claims preferito: live state, max claim = sessione corrente più recente
        home / ".claude/active_claims.json",
        home / "scripts/state/active_claims.json",
        # session_current.md probable paths in ordine di freshness atteso
        home / "projects/claude-memory/session_current.md",
        home / "graphify-polpo-core/session_current.md",
    ]

    for path in candidates:
        try:
            if not path.exists():
                continue
            text = path.read_text(errors="ignore")
            # active_claims.json (~/.claude/active_claims.json): array di dict
            # con campo "claim" (numero sessione) o "session"
            if path.suffix == ".json":
                try:
                    data = json.loads(text)
                    items = data.get("claims") if isinstance(data, dict) else data
                    if isinstance(items, list) and items:
                        nums: list[int] = []
                        for it in items:
                            if not isinstance(it, dict):
                                continue
                            for key in ("claim", "session", "session_n", "n"):
                                val = it.get(key)
                                if val is not None and str(val).isdigit():
                                    nums.append(int(val))
                                    break
                        if nums:
                            return str(max(nums))
                except (json.JSONDecodeError, ValueError, AttributeError):
                    pass
                continue
            # markdown: cerca "sess.NNNN" o "Sessione #NNNN" o "# Session NNNN"
            for line in text.splitlines()[:30]:
                low = line.lower()
                for token in low.replace(":", " ").replace("#", " ").replace(",", " ").split():
                    if token.startswith("sess.") and token[5:].isdigit():
                        return token[5:]
                    if token.isdigit() and 1000 <= int(token) <= 9999:
                        return token
        except OSError:
            continue
    return "—"


def _read_sentinel_data() -> dict:
    """Legge canary_state.json + ultimi 30 eventi da security_audit.jsonl."""
    home = Path.home()
    canary_path = home / ".claude/canary_state.json"
    audit_path  = home / ".claude/security_audit.jsonl"

    canaries: dict = {}
    try:
        data = json.loads(canary_path.read_text())
        canaries = data.get("canaries", {})
    except Exception:
        pass

    alerts: list[dict] = []
    try:
        lines = audit_path.read_text().splitlines()
        for line in reversed(lines[-60:]):
            try:
                alerts.append(json.loads(line))
            except Exception:
                pass
            if len(alerts) >= 30:
                break
    except Exception:
        pass

    return {"canaries": canaries, "alerts": alerts}


def render_sentinel(data: dict) -> tuple[str, str]:
    """Renders (canary_box, alerts_box) as Rich markup strings."""
    canaries = data.get("canaries", {})
    alerts   = data.get("alerts", [])

    # ── Canary box ───────────────────────────────────────────────────────────
    now = time.time()
    lines = [
        f"[bold {HOT_PINK}]🛡 CANARY STATUS[/]  [{DIM}]· immune layer 3[/]\n"
        f"[italic {DIM}]Honeypot files — any atime/hash change = intrusion signal.[/]\n",
    ]
    canary_order = ["file_honeypot", "backup_tokens", "old_stripe", "admin_keys", "polpo_core"]
    for key in canary_order:
        c = canaries.get(key)
        if not c:
            lines.append(f"  [{DIM}]{'─'*36}[/]")
            lines.append(f"  [{DIM}]{key:20s}[/]  [{ORANGE}]NOT DEPLOYED[/]")
            continue
        path_short = Path(c.get("path", "")).name
        deployed_ago = now - (c.get("initial_atime", now))
        age_h = deployed_ago / 3600
        age_str = f"{age_h:.1f}h ago" if age_h < 24 else f"{deployed_ago/86400:.1f}d ago"
        lines.append(f"  [{DIM}]{'─'*36}[/]")
        lines.append(
            f"  [{ELEC_BLUE}]{key:20s}[/]  [{LIME}]CLEAN[/]  [{DIM}]{path_short} · checked {age_str}[/]"
        )
    canary_str = "\n".join(lines)

    # ── Alerts box ───────────────────────────────────────────────────────────
    # sess.1508 round 3: hoisted in polpo_charts.ALERT_LEVEL_COLOR.
    level_color = pc_const.ALERT_LEVEL_COLOR
    alines = [
        f"[bold {HOT_PINK}]🚨 RECENT ALERTS[/]  [{DIM}]· security_audit.jsonl (last 30)[/]\n"
        f"[italic {DIM}]Injection · rate-limit · canary trips — immune system event stream.[/]\n",
    ]
    if not alerts:
        alines.append(f"  [{LIME}]No recent alerts — clean.[/]")
    for ev in alerts:
        ts_raw = ev.get("ts", "")
        ts = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw[:19]
        tl = ev.get("threat_level", 0)
        col = level_color.get(tl, DIM)
        atype = trunc(ev.get("alert_type", "?"), 14)
        desc  = trunc(ev.get("desc", ""), 62)
        tool  = trunc(ev.get("tool", ""), 18)
        alines.append(
            f"  [{DIM}]{ts}[/] [{col}]L{tl}[/] [{DIM}]{atype:14s}[/] [{col}]{desc}[/]\n"
            f"  [{DIM}]{'':>9}  tool:{tool}[/]"
        )
    alert_str = "\n".join(alines)

    return canary_str, alert_str


class TitleBar(Static):
    """Fascia titolo centrata (presenza identitaria costante in TUTTI i tab):
    riga 1: emoji + titolo rainbow ad onda (centrato)
    riga 2: subtitle hardware identity (centrato)
    riga 3: rich info — sessione, uptime, # Claude, ora locale (centrato)
    riga 4: status live — bat/cpu/load/ram pressure/disk/net (centrato)
    riga 5: business KPI — MRR / Outstanding / Pipeline / Lead / Cold (centrato, sess.1539)
    """

    DEFAULT_CSS = f"""
    TitleBar {{
        height: auto;
        min-height: 10;
        background: {BG};
        padding: 2 5;
        color: {FG};
        border-bottom: double {WR_PRIMARY};
        text-align: center;
    }}
    """
    # WAR ROOM: padding 1 3→2 5, min-height 7→10, border double per peso visivo.

    TITLE_TEXT = "M5 WAR ROOM"
    EMOJI      = "⚔"

    phase:       reactive[float] = reactive(0.0)
    status:      reactive[str]   = reactive("")
    rich_info:   reactive[dict]  = reactive(dict)
    # sess.1508 round 2 hierarchy fix: ASCII banner OFF di default — recupera
    # 7 righe di viewport per info reali (sess/MRR/CPU). Si attiva via
    # on_resize quando il terminale è abbastanza grande (cols>=52, rows>=35)
    # E l'utente non l'ha esplicitamente nascosto.
    show_ascii:  reactive[bool]  = reactive(False)
    # sess.1508 round 3 motion: quando True, _tick non avanza phase →
    # rainbow congelato (sistema idle = app calma).
    idle_frozen: reactive[bool]  = reactive(False)

    def on_mount(self) -> None:
        # sess.1508 round 2 — diff cache reale: tupla (ascii_banner, line1..4)
        # Confronto in _repaint, skip self.update() se identica.
        self._last_paint: tuple | None = None
        self.set_interval(RAINBOW_FPS_DT, self._tick)
        self._repaint()

    def _tick(self) -> None:
        # sess.1494: quantizza phase → reactive watch dedup + lru_cache hit
        # sess.1508 round 3: skip tick se --no-rainbow attivo (env M5W_NO_RAINBOW)
        # sess.1508 round 3 motion: skip tick se idle_frozen (sistema calmo).
        if os.environ.get("M5W_NO_RAINBOW") == "1" or self.idle_frozen:
            return
        new_phase = round((self.phase + RAINBOW_SPEED) % 1.0, RAINBOW_PHASE_Q)
        if new_phase != self.phase:
            self.phase = new_phase

    def watch_phase(self, _new: float) -> None:
        self._repaint()

    def watch_status(self, _new: str) -> None:
        self._repaint()

    def watch_rich_info(self, _new: dict) -> None:
        self._repaint()

    def watch_show_ascii(self, _new: bool) -> None:
        self._last_paint = None   # forza repaint quando ASCII viene mostrato/nascosto
        self._repaint()

    def _repaint(self) -> None:
        # sess.1508 round 3 motion: ASCII banner ora `wave=False` di default —
        # il movimento simultaneo hue + luminosity oscillation distraeva
        # l'occhio dalle line2/3/4 dove vivono i dati reali. Il rainbow-hue
        # da solo conserva l'identità visiva senza essere invadente.
        # Wave-on opzionale via env M5W_BANNER_WAVE=1 per chi la ama.
        wave_on = os.environ.get("M5W_BANNER_WAVE") == "1"
        if self.show_ascii:
            ascii_rows = [
                rainbow_text(row, (self.phase + i * 0.12) % 1.0, wave=wave_on)
                for i, row in enumerate(_ASCII_POLPO)
            ]
            ascii_banner = "\n".join(ascii_rows) + "\n"
        else:
            ascii_banner = ""

        rainbow = rainbow_text(self.TITLE_TEXT, self.phase, wave=True)
        line1 = (
            f"{self.EMOJI}  "
            f"[bold]{rainbow}[/]  "
            f"{self.EMOJI}"
        )

        line2 = (
            f"🍎 [{DIM}]Apple[/] [bold {ELEC_BLUE}]M5 Max[/]  "
            f"[{DIM}]·[/]  💎 [bold {DEEP_PURPL}]18C[/] [{DIM}](6S+12P)[/]  "
            f"[{DIM}]·[/]  🧠 [bold {LIME}]36GB[/] [{DIM}]Unified[/]"
        )

        info = self.rich_info or {}
        sess = info.get('session', '—')
        uptime = info.get('uptime', '—')
        claude_n = info.get('claude_count', 0)
        mcp_n = info.get('mcp_count', 0)
        time_str = info.get('time', time.strftime("%H:%M:%S"))
        cols = info.get('cols', 120)
        sep = "   "
        if cols >= 100:
            line3 = (
                f"🎯 [bold {WHITE}]{sess}[/]"
                f"{sep}⏱ [bold {ELEC_BLUE}]{uptime}[/]"
                f"{sep}🐙 [bold {HOT_PINK}]{claude_n}[/]"
                f"{sep}🔌 [bold {SOFT_GREEN}]{mcp_n}[/]"
                f"{sep}[bold {TEAL}]{time_str}[/]"
            )
        elif cols >= 80:
            line3 = (
                f"[bold {WHITE}]{sess}[/]  "
                f"🐙[bold {HOT_PINK}]{claude_n}[/] 🔌[bold {SOFT_GREEN}]{mcp_n}[/]  "
                f"[bold {TEAL}]{time_str}[/]"
            )
        else:
            line3 = (
                f"[bold {WHITE}]{sess}[/]  "
                f"[bold {HOT_PINK}]{claude_n}[/]  "
                f"[bold {TEAL}]{time_str}[/]"
            )

        line4 = self.status if self.status else f"[{DIM}]🔄 Probing system…[/]"

        # sess.1539 round 2: line5 BUSINESS KPI — Nome · Dato · Unità +
        # sparkline trend live (MRR/Out/Pipeline). Visibile in TUTTI i tab.
        # Tier responsive: full(≥100) · compact(≥80) · tiny(<80) — pattern
        # condiviso, sparkline incluse anche al tier compact (5 char + bar).
        kpi = info.get('kpi') or {}
        if kpi:
            mrr        = kpi.get('mrr',         0.0)
            mrr_d      = kpi.get('mrr_delta',   0.0)
            outstand   = kpi.get('outstanding', 0.0)
            pipe       = kpi.get('pipeline',    0.0)
            leads      = int(kpi.get('leads',   0))
            cold_avg   = kpi.get('cold_avg',    0.0)
            spark_mrr  = kpi.get('spark_mrr',   '')
            spark_out  = kpi.get('spark_out',   '')
            spark_pipe = kpi.get('spark_pipe',  '')
            d_color    = LIME if mrr_d >= 0 else HOT_PINK
            d_sign     = '+' if mrr_d >= 0 else ''
            # Sparkline wrapper: appendi solo se non vuota (≥2 punti storici)
            sp_mrr  = f"[{LIME}]{spark_mrr}[/] "      if spark_mrr  else ""
            sp_out  = f"[{HOT_PINK}]{spark_out}[/] "  if spark_out  else ""
            sp_pipe = f"[{ELEC_BLUE}]{spark_pipe}[/] " if spark_pipe else ""
            if cols >= 100:
                line5 = (
                    f"💰 {sp_mrr}[bold {LIME}]€{mrr:,.0f}[/] [{d_color}]{d_sign}{mrr_d:,.0f}[/]"
                    f"{sep}📌 {sp_out}[bold {HOT_PINK}]€{outstand:,.0f}[/]"
                    f"{sep}🎯 {sp_pipe}[bold {ELEC_BLUE}]€{pipe:,.0f}[/]"
                    f"{sep}🔥 [bold {ORANGE}]{leads}[/]"
                    f"{sep}🕐 [bold {YELLOW}]{cold_avg:.1f}gg[/]"
                )
            elif cols >= 80:
                line5 = (
                    f"💰{sp_mrr}[bold {LIME}]€{mrr:,.0f}[/] [{d_color}]{d_sign}{mrr_d:,.0f}[/]  "
                    f"📌{sp_out}[bold {HOT_PINK}]€{outstand:,.0f}[/]  "
                    f"🎯{sp_pipe}[bold {ELEC_BLUE}]€{pipe:,.0f}[/]  "
                    f"🔥[bold {ORANGE}]{leads}[/]  "
                    f"🕐[bold {YELLOW}]{cold_avg:.1f}gg[/]"
                )
            else:
                # Tier tiny: K-notation + Nome+Unità preservati.
                line5 = (
                    f"💰 [bold {LIME}]€{mrr/1000:.1f}K[/]  "
                    f"📌 [bold {HOT_PINK}]€{outstand/1000:.1f}K[/]  "
                    f"🎯 [bold {ELEC_BLUE}]€{pipe/1000:.1f}K[/]  "
                    f"🔥 [bold {ORANGE}]{leads}[/]"
                )
        else:
            line5 = f"[{DIM}]🔄 Loading KPI from vault…[/]"

        # sess.1508 round 2: diff cache — skip self.update() se identico.
        # A 4fps phase ricalcola, ma se quantizzata = uguale → no paint.
        paint = (ascii_banner, line1, line2, line3, line4, line5)
        if paint == self._last_paint:
            return
        self._last_paint = paint
        self.update(f"{ascii_banner}{line1}\n{line2}\n{line3}\n{line4}\n{line5}")


# ── Triage Screen ─────────────────────────────────────────────────────────────
class TriageScreen(ModalScreen):
    """Overlay advisor: classifica processi Polpo in KILL_SAFE / CAUTIOUS / KEEP."""

    DEFAULT_CSS = f"""
    TriageScreen {{
        align: center middle;
        background: rgba(10, 15, 26, 0.75);
    }}
    #triage-outer {{
        width: 96%;
        height: 88%;
        border: double {WR_PRIMARY};
        background: {BG_ALT};
        padding: 2 4;
    }}
    #triage-title {{
        text-align: center;
        margin-bottom: 2;
    }}
    #triage-hint {{
        height: auto;
        color: {DIM};
        text-align: center;
        margin-top: 2;
    }}
    """
    # sess.1508 audit fix:
    # - $accent/$surface/$text-muted (Textual vars) → hex tokens Polpo (palette uniforme)
    # - height: 1 → auto (hint wrappa quando lungo)
    # - aggiunto background semi-trasparente al modal screen = dim overlay backdrop

    BINDINGS = [
        Binding("escape,q", "dismiss",      "Chiudi",   show=True),
        Binding("k",        "kill_selected","Kill",     show=True),
        Binding("r",        "do_refresh",   "Refresh",  show=True),
    ]

    # Semantic color allineata a UX standard (rosso = stop/non toccare, verde = ok)
    # sess.1508 audit fix: prima rosso = SAFE-to-kill, anti-intuitivo.
    _BUCKET_COLOR = {
        ds.BUCKET_SAFE:     f"bold {LIME}",        # ✓ uccidibile in sicurezza → verde
        ds.BUCKET_CAUTIOUS: f"bold {YELLOW}",      # ⚠ attenzione → giallo
        ds.BUCKET_KEEP:     f"bold {RED}",         # ⛔ non toccare → rosso
    }
    _BUCKET_SHORT = {
        ds.BUCKET_SAFE:     "SAFE",
        ds.BUCKET_CAUTIOUS: "CAUTIOUS",
        ds.BUCKET_KEEP:     "KEEP",
    }
    # sess.1508 round 3 a11y: glyph SHAPE-based (color-blind safe).
    # Prima 🟢🟡🔴 = stessa forma cerchio → deutan/protan vede 3 dischi gialli
    # indistinguibili. Ora ✓◐✕ — distinguibili anche senza colore.
    _BUCKET_EMOJI = {
        ds.BUCKET_SAFE:     "✓",
        ds.BUCKET_CAUTIOUS: "◐",
        ds.BUCKET_KEEP:     "✕",
    }

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="triage-outer"):
            yield Static("", id="triage-title")
            yield DataTable(id="triage-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="triage-hint")

    def on_mount(self) -> None:
        t = self.query_one("#triage-table", DataTable)
        t.add_columns(" ", "PID", "Label", "RAM MB", "CPU%", "Ragione")
        self.query_one("#triage-title").update("[dim]⟳ Analizzando processi…[/]")
        self.run_worker(self._load, exclusive=True)

    async def _load(self) -> None:
        self._procs = await asyncio.to_thread(ds.triage_processes)
        self._render()

    def _render(self) -> None:
        procs = getattr(self, '_procs', [])
        t = self.query_one("#triage-table", DataTable)
        t.clear()

        n_safe = sum(1 for p in procs if p['bucket'] == ds.BUCKET_SAFE)
        n_caut = sum(1 for p in procs if p['bucket'] == ds.BUCKET_CAUTIOUS)
        n_keep = sum(1 for p in procs if p['bucket'] == ds.BUCKET_KEEP)

        self.query_one("#triage-title").update(
            f"[bold {LIME}]✓ SAFE ×{n_safe}[/]   "
            f"[bold {YELLOW}]◐ CAUTIOUS ×{n_caut}[/]   "
            f"[bold {RED}]✕ KEEP ×{n_keep}[/]   "
            f"[{DIM}]· {len(procs)} proc Polpo[/]"
        )
        self.query_one("#triage-hint").update(
            f"[{DIM}]k[/] kill  ·  [{DIM}]r[/] refresh  ·  [{DIM}]esc[/] chiudi  "
            f"·  [{LIME}]SAFE[/] = orfani/zombie  ·  [{YELLOW}]CAUTIOUS[/] = attenzione  "
            f"·  [{RED}]KEEP[/] = non toccare"
        )

        for proc in procs:
            color = self._BUCKET_COLOR.get(proc['bucket'], DIM)
            short = self._BUCKET_SHORT.get(proc['bucket'], proc['bucket'])
            emoji = self._BUCKET_EMOJI.get(proc['bucket'], '·')
            t.add_row(
                f"[{color}]{emoji} {short}[/]",
                str(proc['pid']),
                trunc(proc['label'], 24),
                f"{proc['mem_mb']:.0f}",
                f"{proc['cpu']:.1f}",
                trunc(proc['reason'], 56),
                key=str(proc['pid']),
            )

    def action_kill_selected(self) -> None:
        procs = getattr(self, '_procs', [])
        t = self.query_one("#triage-table", DataTable)
        if t.cursor_row is None or not procs:
            return

        try:
            row = t.get_row_at(t.cursor_row)
            pid = int(str(row[1]))
        except (ValueError, IndexError):
            return

        proc = next((p for p in procs if p['pid'] == pid), None)
        if not proc:
            return

        if proc['bucket'] == ds.BUCKET_KEEP:
            self.notify(f"⛔ PID {pid} in KEEP — non killabile", severity="error", timeout=3)
            return

        import signal as _sig
        try:
            os.kill(pid, _sig.SIGTERM)
            self.notify(
                f"✓ SIGTERM → {proc['label']} (PID {pid})",
                severity="information", timeout=2,
            )
        except ProcessLookupError:
            self.notify(f"PID {pid} già morto", severity="warning", timeout=2)
        except PermissionError:
            self.notify(f"⛔ Permission denied su PID {pid}", severity="error", timeout=3)

        self.run_worker(self._load, exclusive=True)

    async def action_do_refresh(self) -> None:
        self.query_one("#triage-title").update("[dim]⟳ Refreshing…[/]")
        self.run_worker(self._load, exclusive=True)


# ── Polpo Pulse — predictive + cross-domain + soul ────────────────────────────
# sess.1568 strategic upgrade: il watcher diventa cockpit Polpo intero, non solo Mac.
# Tre layer fusi in un pannello:
#   1. PREDICTIVE — slope forecasting RAM/CPU (deque history) → ETA-to-threshold
#   2. BUSINESS   — overlay MRR/Outstanding/Pipeline da KPI.md (vault)
#   3. SOUL       — session_current.md head (sess#, status, task in corso, blocchi)

def forecast_eta(history: deque[float], threshold: float = 85.0,
                 sample_dt_s: float = 5.0) -> dict | None:
    """Slope forecasting su deque history → ETA al raggiungimento threshold.

    Algoritmo:
      - slope = (last - first) / N samples
      - se slope <= 0 o stato già sopra threshold → no forecast (None payload)
      - eta_samples = (threshold - current) / slope_per_sample
      - eta_seconds = eta_samples * sample_dt_s

    Output:
      {"slope": float (% per sample),
       "current": float,
       "eta_seconds": int,
       "severity": "ok" | "watch" | "warn" | "alert"}
    """
    if not history or len(history) < 6:
        return None
    vals = list(history)[-30:]
    current = vals[-1]
    if current >= threshold:
        return {"slope": 0.0, "current": current, "eta_seconds": 0, "severity": "alert"}
    slope = (vals[-1] - vals[0]) / max(1, len(vals) - 1)
    if slope <= 0.05:
        return None
    eta_samples = (threshold - current) / slope
    eta_seconds = int(eta_samples * sample_dt_s)
    if eta_seconds < 60:
        sev = "alert"
    elif eta_seconds < 300:
        sev = "warn"
    elif eta_seconds < 1200:
        sev = "watch"
    else:
        sev = "ok"
    return {"slope": slope, "current": current, "eta_seconds": eta_seconds, "severity": sev}


def _fmt_eta(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def read_session_current_summary() -> dict:
    """Leggi session_current.md (graphify-polpo-core) → snapshot operativo.

    Ritorna dict: {session, status, updated, tasks_in_corso[], blocks[]}
    Fallback graceful: return {} se file assente o parsing fail.
    """
    paths = [
        Path.home() / "graphify-polpo-core" / "session_current.md",
        Path.home() / "projects" / "claude-memory" / "session_current.md",
    ]
    src = next((p for p in paths if p.exists()), None)
    if not src:
        return {}
    try:
        text = src.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}
    out: dict = {"session": "—", "status": "—", "updated": "—",
                 "tasks": [], "blocks": []}
    # Frontmatter scan
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            for ln in text[3:end].splitlines():
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k == "session":  out["session"] = v
                    if k == "status":   out["status"]  = v
                    if k == "updated":  out["updated"] = v
            text = text[end + 3:]
    # Section scan
    cur = None
    for ln in text.splitlines():
        s = ln.strip()
        if s.lower().startswith("## task in corso"):
            cur = "tasks";  continue
        if s.lower().startswith("## blocchi"):
            cur = "blocks"; continue
        if s.startswith("## "):
            cur = None
        if cur and s.startswith("- "):
            out[cur].append(s[2:].strip())
    return out


def render_pulse(kpi: dict, mem_history: deque, cpu_history: deque,
                 mem_now: dict, soul: dict) -> str:
    """Rende il pannello Polpo Pulse — 3 layer in 1 viewport."""
    sev_color = {"ok": LIME, "watch": YELLOW, "warn": ORANGE, "alert": WR_DANGER}

    # ── Layer 1: PREDICTIVE ────────────────────────────────────────────────
    ram_pct = mem_now.get('pct', 0)
    fc_ram = forecast_eta(mem_history, threshold=85.0, sample_dt_s=5.0)
    fc_cpu = forecast_eta(cpu_history, threshold=80.0, sample_dt_s=2.0)

    pred_lines = [
        f"[bold {DEEP_PURPL}]🔮 PREDICTIVE FORECAST[/]  [{DIM}]· slope-based ETA-to-threshold[/]",
        "",
    ]
    def _fc_line(label: str, fc: dict | None, current: float, threshold: float, color: str) -> str:
        if fc is None:
            return f"  [{LIME}]●[/] [bold]{label:<5}[/] [{DIM}]{current:5.1f}% · stable, no rise[/]"
        sev = fc["severity"]
        c = sev_color[sev]
        slope = fc["slope"]
        eta = _fmt_eta(fc["eta_seconds"])
        bar_str = bar(current, 18)
        return (
            f"  [{c}]●[/] [bold]{label:<5}[/] [{c}]{bar_str}[/] "
            f"[bold {c}]{current:5.1f}%[/] [{DIM}]→{threshold:.0f}%[/] "
            f"[bold {c}]ETA {eta}[/] [{DIM}]· slope +{slope:.2f}%/s[/]"
        )
    pred_lines.append(_fc_line("RAM",  fc_ram, ram_pct, 85, LIME))
    pred_lines.append(_fc_line("CPU",  fc_cpu, cpu_history[-1] if cpu_history else 0, 80, ELEC_BLUE))

    # ── Layer 2: BUSINESS ──────────────────────────────────────────────────
    mrr      = int(kpi.get('mrr') or 0)
    out_eur  = int(kpi.get('outstanding') or 0)
    pipe     = int(kpi.get('pipeline_weighted') or 0)
    leads    = int(kpi.get('setter_active') or 0)
    sess_kpi = kpi.get('sessione_corrente') or "—"

    biz_lines = [
        "",
        f"[bold {WR_ACCENT}]💎 BUSINESS PULSE[/]  [{DIM}]· Astra Digital · vault KPI.md[/]",
        "",
        f"  [{LIME}]💰 MRR[/]            [bold {WHITE}]€{mrr:>6,}[/]".replace(',', '.'),
        f"  [{ORANGE}]⚠ Outstanding[/]   [bold {WR_DANGER if out_eur > 3000 else ORANGE}]€{out_eur:>6,}[/]".replace(',', '.'),
        f"  [{ELEC_BLUE}]🎯 Pipeline[/]      [bold {ELEC_BLUE}]€{pipe:>6,}[/] [{DIM}]weighted[/]".replace(',', '.'),
        f"  [{HOT_PINK}]🔗 Leads attivi[/]  [bold {HOT_PINK}]{leads:>6}[/] [{DIM}]setter ecosystem[/]",
    ]

    # ── Layer 3: SOUL ──────────────────────────────────────────────────────
    sess  = soul.get('session', '—')
    stat  = soul.get('status', '—')
    upd   = soul.get('updated', '—')
    tasks = soul.get('tasks', [])
    blocks = soul.get('blocks', [])

    soul_lines = [
        "",
        f"[bold {WR_PRIMARY}]🐙 POLPO SOUL[/]  [{DIM}]· session_current · vault head[/]",
        "",
        f"  [bold {WHITE}]sess.{sess}[/]  "
        f"[{DIM}]·[/] status [bold {LIME if stat == 'open' else ORANGE}]{stat}[/]  "
        f"[{DIM}]·[/] [{DIM}]updated {upd}[/]",
        "",
        f"  [bold {WR_ACCENT}]Task in corso ({len(tasks)})[/]:",
    ]
    for t in tasks[:5]:
        soul_lines.append(f"    [{DIM}]›[/] {t[:88]}")
    if len(tasks) > 5:
        soul_lines.append(f"    [{DIM}]…+{len(tasks) - 5} altri[/]")
    if blocks:
        soul_lines += ["", f"  [bold {WR_DANGER}]🛑 Blocchi attivi ({len(blocks)})[/]:"]
        for b in blocks[:4]:
            soul_lines.append(f"    [{WR_DANGER}]✕[/] {b[:88]}")

    return "\n".join(pred_lines + biz_lines + soul_lines)


# ── Tentacle Bridge — un tentacolo per tab ────────────────────────────────────
# sess.1568 strategic upgrade #4: ogni TabPane è collegata a un tentacolo
# specializzato. Hotkey:
#   t → insight DATA in <50ms dai dati già caricati in self._* + deep prompt clipboard
#   T → audit DESIGN/viz della tab corrente, proposte miglioramento UI/grafici
# Pattern: human-in-the-loop come `/polpo-call` — clipboard ready per Antigravity.

def _tentacle_heat(app) -> tuple[str, str, str]:
    """🌡 Heatmap → top burning cores."""
    if not app._cpu_percents:
        return "🌡 no samples yet", "", ""
    pcts = sorted(enumerate(app._cpu_percents), key=lambda x: -x[1])[:3]
    avg = sum(app._cpu_percents) / len(app._cpu_percents)
    insight = "🌡  Top: " + " · ".join(f"C{i:02d}={v:.0f}%" for i, v in pcts) + f"  avg {avg:.0f}%"
    data_prompt = (
        f"M5 Heatmap forensics — overall {avg:.1f}%, top burning cores: "
        + ", ".join(f"core {i:02d}={v:.0f}%" for i, v in pcts) +
        f". History depth {len(app._cpu_history)} samples. "
        "Identify: (1) which process hammers these cores (sample top procs), "
        "(2) sustained vs transient (slope), "
        "(3) propose throttle/kill action if sustained >2min on P-core."
    )
    design_prompt = (
        "M5 Heatmap design audit — currently 18-core grid with HEAT_MAP glyph palette "
        "(·░▒▓▚▞▣█). Propose: (1) cluster S/P visual separation upgrade, "
        "(2) per-core sparkline overlay (60s history), (3) thermal zone color drift "
        "(cold→hot gradient), (4) burning-core attention pulse (animation throttled to 8fps)."
    )
    return insight, data_prompt, design_prompt


def _tentacle_stats(app) -> tuple[str, str, str]:
    """📈 Analytics → forecast riepilogato."""
    fc_ram = forecast_eta(app._mem_history, threshold=85.0, sample_dt_s=5.0)
    fc_str = f"RAM ETA→85% {_fmt_eta(fc_ram['eta_seconds'])}" if fc_ram else "RAM stable"
    insight = f"📈  {fc_str}"
    data_prompt = (
        f"M5 trend analysis — current RAM {app._mem.get('pct',0):.1f}%, "
        f"history {len(app._mem_history)} samples. "
        + (f"Forecast: {fc_str}, slope +{fc_ram['slope']:.2f}%/sample. "
           if fc_ram else "")
        + "Identify probable memory hog from procs tab and validate forecast tightness."
    )
    design_prompt = (
        "Analytics tab design audit — currently sparkline + bar charts. Propose: "
        "(1) confidence band overlay sul forecast, (2) anomaly markers (Z-score >2), "
        "(3) multi-metric correlation (CPU×RAM×swap), "
        "(4) zoom temporale interattivo (1m/5m/30m windows)."
    )
    return insight, data_prompt, design_prompt


def _tentacle_procs(app) -> tuple[str, str, str]:
    """🔝 Processes → triage top hog."""
    insight = "🔝  query proc table for triage"
    data_prompt = (
        "M5 process triage — list top 10 processes by RSS+CPU. For each: owner "
        "(user/agent/daemon), parent PID, estimated leak vs healthy load. "
        "Suggest one safe-kill candidate + 2 daemon throttle ottimizzabili."
    )
    design_prompt = (
        "Processes tab design audit — DataTable plain. Propose: (1) RSS bar inline "
        "per riga (sparkline), (2) growth-rate column (Δ ultime 30s), "
        "(3) parent process tree visual (indent), (4) 'kill safe' hint colonna."
    )
    return insight, data_prompt, design_prompt


def _tentacle_tent(app) -> tuple[str, str, str]:
    """🐙 Tentacoli → Claude/MCP orchestrator audit."""
    n_cl = app._proc_counts.get('claude', 0)
    n_mc = app._proc_counts.get('mcp', 0)
    insight = f"🐙  {n_cl} Claude · {n_mc} MCP unique"
    data_prompt = (
        f"Polpo tentacle audit — {n_cl} Claude sessions, {n_mc} MCP servers unique. "
        "Verify: (1) MCP orfani senza Claude parent, (2) PID/PPID drift, "
        "(3) memory pressure cluster MCP, (4) consolidation se N>10."
    )
    design_prompt = (
        "Tentacoli tab design audit — propose: (1) gerarchia visiva Claude → MCP children "
        "(tree con └─), (2) heartbeat indicator per Claude session (last activity), "
        "(3) MCP grouping per tipo (modelcontextprotocol/whatsapp/etc), "
        "(4) restart-button-action per MCP frozen."
    )
    return insight, data_prompt, design_prompt


def _tentacle_graph(app) -> tuple[str, str, str]:
    """🕸 Graph → vault audit."""
    g = app._graph_data or {}
    n_nodes = len(g.get('nodes', []))
    insight = f"🕸  vault: {n_nodes} nodi"
    data_prompt = (
        f"Vault Obsidian audit — {n_nodes} note totali. Run vault-gardener: "
        "(1) note orfane, (2) link rotti, (3) note operative senza ancora identitaria, "
        "(4) topic con drift cross-cluster, (5) hub recenti potenzialmente attractor."
    )
    design_prompt = (
        "Graph tab design audit — propose: (1) cluster coloring per cartella vault, "
        "(2) node size = degree centrality, (3) hover preview titolo nota, "
        "(4) filter timeline (note touched ultimi 7gg)."
    )
    return insight, data_prompt, design_prompt


def _tentacle_kpi(app) -> tuple[str, str, str]:
    """📊 KPI → MRR/Outstanding actionable."""
    k = app._kpi_data or {}
    mrr = int(k.get('mrr') or 0)
    out = int(k.get('outstanding') or 0)
    pipe = int(k.get('pipeline_weighted') or 0)
    insight = f"📊  MRR €{mrr:,} · Out €{out:,} · Pipe €{pipe:,}".replace(',', '.')
    data_prompt = (
        f"Astra Digital recovery — MRR €{mrr:,}, outstanding €{out:,}, "
        f"pipeline €{pipe:,}. (1) Lista clienti outstanding ordinati per D+ giorni, "
        "(2) per ognuno: ultimo touch + email/WA, (3) bozza sollecito top-1, "
        "(4) churn risk top-2, (5) verifica drift KPI.md vs Stripe ground truth."
    ).replace(',', '.')
    design_prompt = (
        "KPI tab design audit — propose: (1) MRR sparkline 12 mesi, "
        "(2) outstanding waterfall per cliente, (3) pipeline funnel viz, "
        "(4) churn risk heatmap (cliente × D+giorni), (5) cash forecast 30/60/90gg."
    )
    return insight, data_prompt, design_prompt


def _tentacle_health(app) -> tuple[str, str, str]:
    """🩺 Health → body vitals."""
    h = app._health_data or {}
    insight = f"🩺  body: {len(h)} metriche"
    data_prompt = (
        "Apple Health audit — leggi /tmp/polpo_health_live.json. Surface: "
        "peso trend 7gg, sonno qualità ultime 3 notti, HRV trend, alert clinici "
        "psoriasi-aware (cortisolo×sleep×load). Propose 1 nudge action."
    )
    design_prompt = (
        "Health tab design audit — propose: (1) body silhouette con hot zones, "
        "(2) sleep stage stacked bar 7gg, (3) HRV trend con baseline personale, "
        "(4) correlation overlay (sleep × HRV × workload Polpo)."
    )
    return insight, data_prompt, design_prompt


def _tentacle_logs(app) -> tuple[str, str, str]:
    """📋 Logs → activity stream summary."""
    insight = "📋  audit activity stream"
    data_prompt = (
        "Activity Stream audit — leggi log table corrente. Cluster by source. "
        "Surface: (1) anomalie ultime 24h, (2) silent senders (canale fermo >24h "
        "che dovrebbe parlare), (3) top P0 sev events da raggruppare in incident."
    )
    design_prompt = (
        "Logs tab design audit — propose: (1) timeline scroll-zoom (15min/1h/24h), "
        "(2) source-color legend persistente, (3) sev clustering (P0/P1/P2 zones), "
        "(4) search/filter inline, (5) collapsible incident threads."
    )
    return insight, data_prompt, design_prompt


def _tentacle_sent(app) -> tuple[str, str, str]:
    """🛡 Sentinel → security audit."""
    s = app._sentinel_data or {}
    n_alerts = len(s.get('alerts', []))
    n_canary = len(s.get('canaries', {}))
    insight = f"🛡  {n_canary} canary · {n_alerts} alerts"
    data_prompt = (
        f"Cyber Sentinel audit — {n_canary} canary trips, {n_alerts} alerts. "
        "(1) classifica alerts per severità P0/P1/P2, (2) FP ratio, "
        "(3) integrity check hooks, (4) credential rotation due, "
        "(5) DMARC/DNS/firewall delta vs baseline."
    )
    design_prompt = (
        "Sentinel tab design audit — propose: (1) threat-state badge dominante "
        "(GREEN/YELLOW/ORANGE/RED), (2) canary timeline con last-trip markers, "
        "(3) alert severity stacked bar, (4) attack-chain graph se >2 alerts correlati."
    )
    return insight, data_prompt, design_prompt


def _tentacle_pulse(app) -> tuple[str, str, str]:
    """🔮 Pulse → strategic cross-domain."""
    fc = forecast_eta(app._mem_history, threshold=85.0, sample_dt_s=5.0)
    k = app._kpi_data or {}
    out = int(k.get('outstanding') or 0)
    eta_str = _fmt_eta(fc['eta_seconds']) if fc else "stable"
    insight = f"🔮  ETA→85% {eta_str}  ·  out €{out:,}".replace(',', '.')
    data_prompt = (
        "Polpo strategic pulse — fuse system + business + soul. "
        f"System: RAM forecast {eta_str}. Business: outstanding €{out:,}. "
        "Soul: leggi session_current task in corso. "
        "Identify: cosa è il singolo prossimo move che muove di più la macchina "
        "(tech+biz+soul). Restituisci un'azione + il razionale + il costo."
    ).replace(',', '.')
    design_prompt = (
        "Pulse tab design audit — già 3-layer (predictive/business/soul). Propose: "
        "(1) cross-correlation heatmap layer × tempo, (2) action panel inline "
        "(top 3 mosse con button copy-prompt), (3) drift indicators live "
        "(KPI.md vs Stripe, session_current vs reality), (4) trend sparkline per ogni KPI."
    )
    return insight, data_prompt, design_prompt


def _tentacle_debug(app) -> tuple[str, str, str]:
    """🔬 Debug → telemetry spine."""
    summary = app._metrics.summary()
    p95 = summary.get('frame_ms', {}).get('p95', 0)
    cache = summary.get('cache', {}).get('ratio', 0) * 100
    insight = f"🔬  frame p95 {p95:.0f}ms · cache {cache:.1f}%"
    data_prompt = (
        f"M5 watcher self-telemetry — frame p95 {p95:.0f}ms (budget 33ms), "
        f"cache hit {cache:.1f}%. Identify: render bottleneck, suggested cache TTL "
        "tuning, ridge widgets to memoize, render path da split asyncio."
    )
    design_prompt = (
        "Debug tab design audit — propose: (1) flame chart per frame, "
        "(2) cache hit rate sparkline rolling 60s, (3) memory leak detector "
        "(deque growth tracking), (4) per-widget render time breakdown."
    )
    return insight, data_prompt, design_prompt


_TENTACLES: dict[str, Callable] = {
    "tab-heat":   _tentacle_heat,
    "tab-stats":  _tentacle_stats,
    "tab-procs":  _tentacle_procs,
    "tab-tent":   _tentacle_tent,
    "tab-graph":  _tentacle_graph,
    "tab-kpi":    _tentacle_kpi,
    "tab-health": _tentacle_health,
    "tab-logs":   _tentacle_logs,
    "tab-sent":   _tentacle_sent,
    "tab-pulse":  _tentacle_pulse,
    "tab-debug":  _tentacle_debug,
}


def _copy_clipboard(text: str) -> bool:
    """pbcopy text → True if success. Macros best-effort, never raises."""
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"),
                       check=False, timeout=2.0)
        return True
    except Exception:
        return False


# ── App ────────────────────────────────────────────────────────────────────────
class M5Watcher(App):
    """🐙 M5 MAX WATCHER · Astra Digital Marketing · Polpo Cockpit Suite

    Real-time analytics TUI for Apple M5 Max. See module docstring for full spec.
    Forged sess.1238 · v2.0.0 · 2026-05-02 · Polpo Data Viz Edition.
    """

    TITLE     = f"🐙 {__title__}"
    SUB_TITLE = f"v{__version__} · Apple M5 Max · 18C (6S+12P) · 36GB Unified · {__company__}"

    CSS = f"""
    Screen {{
        background: {BG};
        color: {FG};
    }}
    #graph-scroll {{
        background: {BG_ALT};
        border: double {WR_PRIMARY};
        border-title-color: {WR_ACCENT};
        border-title-style: bold;
        padding: 2 4;
        height: 1fr;
        overflow-y: auto;
    }}
    #graph-static {{
        width: 1fr;
    }}
    #top-row {{
        height: auto;
        min-height: 36;
        max-height: 52;
    }}
    #cpu-panel, #mem-panel {{
        width: 1fr;
        border: double {WR_PRIMARY};
        padding: 2 4;
        margin: 0;
        background: {BG_ALT};
    }}
    #cpu-panel {{
        border-title-color: {WR_ACCENT};
        border-title-style: bold;
    }}
    #mem-panel {{
        border-title-color: {WR_ACCENT};
        border-title-style: bold;
        height: 1fr;
    }}
    #mem-col {{
        width: 1fr;
        layout: vertical;
    }}
    #feed-panel {{
        height: 1fr;
        padding: 2 4;
        margin: 0;
        background: {BG_ALT};
        border: double {ORANGE};
        border-title-color: {ORANGE};
        border-title-style: bold;
        overflow-y: auto;
    }}
    #tab-area {{
        height: 1fr;
        min-height: 36;
    }}
    TabbedContent {{
        background: {BG};
    }}
    ContentSwitcher {{
        border-top: double {WR_PRIMARY};
    }}
    Tabs {{
        background: {BG};
        height: 4;
        width: 100%;
        margin: 0;
        padding: 0;
    }}
    Tabs #tabs-list {{
        min-width: 0;
    }}
    Tabs #tabs-list-bar {{
        min-width: 0;
        align-horizontal: center;
    }}
    Tab {{
        color: {DIM};
        background: {BG};
        padding: 1 5;
        height: 1fr;
    }}
    Tab:hover {{
        color: {FG};
        background: {BG_ALT};
    }}
    Tab.-active {{
        color: {WR_PRIMARY};
        background: {BG_ALT};
        text-style: bold underline;
    }}
    Tab.-active:hover {{
        color: {WR_PRIMARY};
        background: {BG_ALT};
    }}
    TabPane {{
        background: {BG};
        padding: 0 2;
    }}
    #heat-row {{
        height: 1fr;
    }}
    #heat-static {{
        width: 1fr;
        background: {BG_ALT};
        border: double {WR_PRIMARY};
        padding: 2 4;
        height: 1fr;
        overflow-x: hidden;
        overflow-y: hidden;
    }}
    #analytics-scroll {{
        background: {BG_ALT};
        border: double {WR_PRIMARY};
        padding: 2 4;
        height: 1fr;
    }}
    #analytics-static {{
        background: {BG_ALT};
        height: auto;
    }}
    #voice-static {{
        width: 1fr;
        background: {BG_ALT};
        border: double {HOT_PINK};
        border-title-color: {HOT_PINK};
        padding: 2 4;
        height: 1fr;
    }}
    DataTable {{
        background: {BG_ALT};
        height: 1fr;
    }}
    DataTable > .datatable--header {{
        background: {BG};
        color: {WR_ACCENT};
        text-style: bold underline;
    }}
    DataTable > .datatable--cursor {{
        background: {WR_PRIMARY};
        color: {BG};
    }}
    DataTable > .datatable--even-row {{
        background: {BG};
    }}
    #kpi-scroll {{
        background: {BG_ALT};
        border: double {LIME};
        border-title-color: {LIME};
        padding: 2 4;
        height: 1fr;
    }}
    #kpi-static {{
        background: {BG_ALT};
        height: auto;
    }}
    #health-scroll {{
        background: {BG_ALT};
        border: double {LIME};
        border-title-color: {LIME};
        padding: 2 4;
        height: 1fr;
    }}
    #health-static {{
        background: {BG_ALT};
        height: auto;
    }}
    #pulse-scroll {{
        background: {BG_ALT};
        border: double {DEEP_PURPL};
        border-title-color: {DEEP_PURPL};
        padding: 2 4;
        height: 1fr;
    }}
    #pulse-static {{
        background: {BG_ALT};
        height: auto;
    }}
    #logs-scroll {{
        background: {BG_ALT};
        border: double {ORANGE};
        border-title-color: {ORANGE};
        padding: 2 4;
        height: 1fr;
    }}
    #sentinel-row {{
        height: 1fr;
    }}
    #canary-panel {{
        background: {BG_ALT};
        border: double {HOT_PINK};
        padding: 2 3;
        width: 1fr;
        height: 1fr;
    }}
    #alerts-panel {{
        background: {BG_ALT};
        border: double {ELEC_BLUE};
        padding: 2 3;
        width: 2fr;
        height: 1fr;
    }}
    /* WAR ROOM: header uniforme cross-tab con più respiro. */
    #heat-header,
    #analytics-header,
    #procs-header,
    #tent-header,
    #graph-header,
    #kpi-header,
    #logs-header,
    #sent-header,
    #debug-header {{
        height: auto;
        padding: 0 0 2 0;
    }}
    #log-table {{
        background: {BG_ALT};
        height: 1fr;
    }}
    #procs-box {{
        background: {BG_ALT};
        border: double {ELEC_BLUE};
        padding: 2 4;
        height: 1fr;
        overflow-x: hidden;
        overflow-y: hidden;
    }}
    #tent-box {{
        background: {BG_ALT};
        border: double {HOT_PINK};
        padding: 2 4;
        height: 1fr;
    }}
    #focus-static {{
        height: auto;
        max-height: 38;
        margin-bottom: 2;
    }}
    #tent-table {{
        height: auto;
    }}
    #debug-scroll {{
        background: {BG_ALT};
        border: double {DEEP_PURPL};
        padding: 2 3;
        height: 1fr;
    }}
    """

    # sess.1508 audit fix: keybinding numerici/k/f ora visibili nel Footer
    # (prima `show=False` rendeva il sistema invisibile a chi non aveva
    # memoria muscolare). Tab key labels accorciate (1-8 con label inline).
    # sess.1525: footer raggruppato in 4 cluster con separatore │
    # cluster control · nav tabs · actions · help → cervello legge cluster, non sequenze.
    BINDINGS = [
        # Control cluster
        Binding("q",   "quit",           "Quit"),
        Binding("r",   "force_refresh",  "↻"),
        Binding("p",   "toggle_pause",   "⏸ Pause"),
        # Nav cluster (tabs 1-9) — sess.1558: rimosso numero da description
        # (Textual prependeva key_display causando "1 1🌡" duplicato in footer)
        Binding("1",   "show_tab_heat",  "│ 🌡",      show=True),
        Binding("2",   "show_tab_stats", "📈",       show=True),
        Binding("3",   "show_tab_procs", "🔝",       show=True),
        Binding("4",   "show_tab_tent",  "🐙",       show=True),
        Binding("5",   "show_tab_graph", "🕸",       show=True),
        Binding("6",   "show_tab_kpi",   "📊",       show=True),
        Binding("7",   "show_tab_logs",  "📋",       show=True),
        Binding("8",   "show_tab_sent",  "🛡",       show=True),
        Binding("9",   "show_tab_pulse", "🔮",       show=True),
        Binding("d",   "show_tab_debug", "🔬",       show=True),
        # Actions cluster
        Binding("f",   "cycle_graph_filter", "│ Filter", show=True),
        Binding("c",   "triage",             "Triage",   show=True),
        Binding("k",   "kill_tent_selected", "Kill",     show=True),
        Binding("s",   "snapshot",           "📸 Snap",  show=True),
        # Tentacle bridge (sess.1568 strategic upgrade #4)
        Binding("t",   "tentacle_data",      "🐙 t",     show=True),
        Binding("T",   "tentacle_design",    "🎨 T",     show=True),
        # Help cluster
        Binding("?",   "show_help",          "│ Help",   show=True),
    ]

    def __init__(self):
        super().__init__()
        self._cpu_percents: list[float]               = []
        self._mem:          dict                       = {}
        self._bat:          dict                       = {}
        self._disk:         dict                       = {}
        self._net:          dict                       = {}
        self._cpu_history:  deque[float]               = deque(maxlen=100)
        self._mem_history:  deque[float]               = deque(maxlen=100)
        self._core_history: dict[int, deque[float]]    = {
            i: deque(maxlen=100) for i in range(N_CORES)
        }
        self._paused = False
        self._tick   = 0
        self._graph_data:   dict = {}
        self._graph_filter: str  = "all"
        # Header rich-info cache
        self._boot_time   = psutil.boot_time()
        self._sess_n      = _claude_session_number()
        self._proc_counts = {'claude': 0, 'mcp': 0}
        self._kpi_data:     dict                       = {}
        self._health_data:  dict                       = {}   # sess.1582: Apple Health snapshot
        self._focus_data:   dict                       = {}
        # Unified feed — state transition log (cross-clock: fast=CPU/voice, slow=mem/KPI)
        self._event_feed:      deque[str]              = deque(maxlen=15)
        self._prev_pressure:   str                     = ''
        self._prev_swap_active: bool                   = False
        self._cpu_spike_ticks:  int                    = 0
        self._prev_voice_state: str                    = ''
        self._prev_mrr:         float                  = 0.0
        self._prev_pipeline:    float                  = 0.0
        self._sentinel_data:    dict                   = {}
        # Responsive layout — updated on terminal resize
        self._cols: int = 120
        self._rows: int = 40
        # sess.1508 round 3 motion premium:
        # - Idle freeze: quando overall<5% per N tick consecutivi, freeze
        #   rainbow per coerenza dato↔motion (sistema calmo → app calma).
        # - Critical flash: pressure='error' o CPU spike sostenuto → border
        #   bottom HOT_PINK per 5s, poi revert.
        self._idle_ticks: int = 0
        self._critical_until: float = 0.0
        self._critical_flash_active: bool = False
        # sess.1508 round 4 telemetry spine (claim verifiability)
        self._metrics      = metrics
        self._jsonl_writer = jsonl
        self._py_logger       = logger
        self._prev_idle_frozen: bool = False
        # Webhook URL hidratato da main() via class attr (sess.1508 round 4)
        self._webhook_url: str | None = getattr(self.__class__, "_webhook_url", None)
        # sess.1508 audit fix: cache last-rendered widget content per evitare
        # Static.update() inutili quando il contenuto non cambia (era il caso
        # del feed-static aggiornato ogni 2s anche con deque immutata).
        self._feed_last_render: str = ""
        # sess.1508 round 2: diff cache esteso a tutti i widget heavy-render.
        # Risparmio atteso: ~70% Static.update() su CPU/RAM stabile.
        self._render_cache: dict[str, str] = {}
        # Resize debounce timer (sess.1508 round 2)
        self._resize_timer = None

    # ── Layout ────────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield TitleBar(id="title-bar")
        with TabbedContent(id="tab-area"):
            with TabPane("🌡 Heatmap", id="tab-heat"):
                # sess.1539: header uniforme cross-tab (Nome · tagline · poetic line)
                yield Static(
                    f"[bold {HOT_PINK}]🌡 CORE HEATMAP[/]  [{DIM}]· 18 cores live · M5 Max[/]\n"
                    f"[italic {DIM}]Eighteen heartbeats in parallel — see where the silicon burns and where it breathes.[/]",
                    id="heat-header")
                with Horizontal(id="heat-row"):
                    yield Static(f"[{DIM}]Accumulating core samples…[/]", id="heat-static")
                    yield Static(f"[{DIM}]🔄 Loading voice…[/]", id="voice-static")
            with TabPane("📈 Analytics", id="tab-stats"):
                with ScrollableContainer(id="analytics-scroll"):
                    yield Static(
                        f"[bold {LIME}]📈 SYSTEM ANALYTICS[/]  [{DIM}]· CPU/RAM trend · forecasts[/]\n"
                        f"[italic {DIM}]The shape of time — sparklines, deltas, predictions of where this system is heading.[/]",
                        id="analytics-header")
                    yield Static(f"[{DIM}]Building statistics…[/]", id="analytics-static")
            with TabPane("🔝 Processes", id="tab-procs"):
                with ScrollableContainer(id="procs-box"):
                    yield Static(
                        f"[bold {ELEC_BLUE}]🔝 TOP PROCESSES[/]  [{DIM}]· ranked by CPU + RAM[/]\n"
                        f"[italic {DIM}]The hungriest first — when something feels wrong, the answer is usually here.[/]",
                        id="procs-header")
                    yield DataTable(id="proc-table", cursor_type="row", zebra_stripes=True)
            with TabPane("🐙 Tentacoli", id="tab-tent"):
                with ScrollableContainer(id="tent-box"):
                    yield Static(
                        f"[bold {HOT_PINK}]🐙 POLPO TENTACOLI[/]  [{DIM}]· background workers[/]\n"
                        f"[italic {DIM}]The autonomic nervous system of the Polpo — Claude, MCP, daemons, watchdogs, alive.[/]",
                        id="tent-header")
                    yield Static(
                        render_focus({}),
                        id="focus-static")
                    yield DataTable(id="tent-table", cursor_type="row", zebra_stripes=True)
            with TabPane("🕸 Graph", id="tab-graph"):
                with ScrollableContainer(id="graph-scroll"):
                    yield Static(
                        f"[bold {TEAL}]🕸 OBSIDIAN GRAPH[/]  [{DIM}]· vault knowledge map[/]\n"
                        f"[italic {DIM}]Notes are nodes, links are synapses — the second brain seen from above.[/]",
                        id="graph-header")
                    yield Static(
                        f"[{DIM}]🔄 Parsing vault Obsidian…[/]",
                        id="graph-static")
            with TabPane("📊 KPI", id="tab-kpi"):
                with ScrollableContainer(id="kpi-scroll"):
                    yield Static(
                        f"[bold {WHITE}]📊 BUSINESS KPI[/]  [{DIM}]· Astra Digital · MRR · Outstanding · Pipeline[/]\n"
                        f"[italic {DIM}]The numbers that decide the month — every euro accounted, every lead tracked.[/]",
                        id="kpi-header")
                    yield Static(f"[{DIM}]🔄 Leggendo KPI.md dal vault…[/]", id="kpi-static")
            with TabPane("🩺 Health", id="tab-health"):
                with ScrollableContainer(id="health-scroll"):
                    yield Static(
                        f"[bold {LIME}]🩺 BODY VITALS[/]  [{DIM}]· Apple Health · iPhone+Watch sync[/]\n"
                        f"[italic {DIM}]Il corpo è il primo cliente — peso, sonno, HRV, alert clinici psoriasi-aware.[/]",
                        id="health-header")
                    yield Static(f"[{DIM}]🔄 Leggendo /tmp/polpo_health_live.json…[/]", id="health-static")
            with TabPane("📋 Logs", id="tab-logs"):
                with ScrollableContainer(id="logs-scroll"):
                    yield Static(
                        f"[bold {ORANGE}]📋 ACTIVITY STREAM[/]  [{DIM}]· cross-system log cascade[/]\n"
                        f"[italic {DIM}]Every signal from every tentacolo — leads, payments, calls, voice, security.[/]",
                        id="logs-header")
                    # sess.1586: log-table primary — Activity Stream è la natura
                    # del tab. 6 strip roadmap come contesto sotto, non sopra.
                    yield DataTable(id="log-table", cursor_type="row", zebra_stripes=True)
                    yield Static("", id="polestar-strip",      markup=True)
                    yield Static("", id="outstanding-section", markup=True)
                    yield Static("", id="vectors-strip",       markup=True)
                    yield Static("", id="traps-banner",        markup=True)
                    yield Static("", id="filaments-section",   markup=True)
                    yield Static("", id="blocks-section",      markup=True)
            with TabPane("🛡 Sentinel", id="tab-sent"):
                yield Static(
                    f"[bold {RED}]🛡 CYBER SENTINEL[/]  [{DIM}]· auth · canaries · alerts[/]\n"
                    f"[italic {DIM}]The immune system watching the watchers — credentials, breaches, drift.[/]",
                    id="sent-header")
                with Horizontal(id="sentinel-row"):
                    with ScrollableContainer(id="canary-panel"):
                        yield Static("", id="canary-static")
                    with ScrollableContainer(id="alerts-panel"):
                        yield Static("", id="alerts-static")
            # sess.1568 strategic upgrade: Polpo Pulse — predictive + business + soul fused.
            with TabPane("🔮 Pulse", id="tab-pulse"):
                with ScrollableContainer(id="pulse-scroll"):
                    yield Static(
                        f"[bold {DEEP_PURPL}]🔮 POLPO PULSE[/]  [{DIM}]· system × business × soul · live forecast[/]\n"
                        f"[italic {DIM}]Three rivers, one delta — what the silicon predicts, what the business owes, what the soul carries.[/]",
                        id="pulse-header")
                    yield Static(f"[{DIM}]🔄 Synthesizing pulse…[/]", id="pulse-static")
            # sess.1508 round 4: telemetry spine — claim verifiability live.
            with TabPane("🔬 Debug", id="tab-debug"):
                with ScrollableContainer(id="debug-scroll"):
                    yield Static(
                        f"[bold {ELEC_BLUE}]🔬 TELEMETRY SPINE[/]  "
                        f"[{DIM}]· where the app dissects itself · claim → numeri[/]",
                        id="debug-header")
                    yield Static("", id="debug-static")
        with Horizontal(id="top-row"):
            with ScrollableContainer(id="cpu-panel"):
                yield Static(
                    f"[bold {ELEC_BLUE}]⚡ CPU[/]  [{DIM}]· M5 Max 18C[/]\n"
                    f"[italic {DIM}]Where silicon thinks — six leaves of efficiency, twelve rockets of performance.[/]\n"
                    f"[{DIM}]🔄 Probing…[/]",
                    id="cpu-content")
            with Vertical(id="mem-col"):
                with ScrollableContainer(id="mem-panel"):
                    yield Static(
                        f"[bold {LIME}]🧠 UNIFIED MEMORY[/]  [{DIM}]· 36GB[/]\n"
                        f"[italic {DIM}]One pool, no walls — Apple unified architecture observed as a single organism.[/]\n"
                        f"[{DIM}]🔄 Reading…[/]",
                        id="mem-content")
                with ScrollableContainer(id="feed-panel"):
                    yield Static(
                        f"[bold {ORANGE}]⚡ UNIFEED[/]  [{DIM}]· M5 Max events[/]\n"
                        f"[{DIM}]no events yet…[/]",
                        id="feed-static")
        yield Footer()

    # ── Responsive helpers ────────────────────────────────────────────────────
    def _heatmap_cols(self) -> int:
        # heat-static = 1fr (50% in flex 2-figli) — sess.1508 audit fix:
        # border=2 + padding=6 = 8 char chrome; row overhead "  P00 " (6) + "  100%" (6) = 12.
        # Subtract 22 totali per safety contro Berkeley Mono cell-width drift su emoji.
        return max(20, int(self._cols * 0.50) - 22)

    def _spark_width(self) -> int:
        """Sparkline width for analytics tab — full-width panel minus labels."""
        return max(20, self._cols - 24)

    def _voice_width(self) -> int:
        """Level bar width for voice panel — 1fr (50%) panel minus padding/prefix."""
        return max(20, int(self._cols * 0.50) - 14)

    def _center_tabs(self) -> None:
        """Center the tab bar by computing padding-left from terminal width and tab content width.

        Textual's Tabs widget defaults to min-width: 100% on #tabs-list, which
        forces tabs to start at the left edge. CSS workarounds (align-horizontal,
        min-width: 0) are unreliable across Textual versions. The robust fix is
        to apply a programmatic padding-left to the Tabs widget, recomputed on
        every resize and on mount.
        """
        try:
            tabs = self.query_one(TextualTabs)
        except Exception:
            return
        tab_widgets = list(tabs.query("Tab"))
        if not tab_widgets:
            return
        # Sum content widths of all tabs (label length + padding 1 3 = +6 chars per tab)
        total_w = 0
        for t in tab_widgets:
            try:
                label = t.label_text if hasattr(t, "label_text") else str(t.label)
            except Exception:
                label = ""
            # +6 = padding 0 3 each side; +1 for safety/separator
            total_w += len(label) + 7
        avail = max(self._cols, 0)
        pad = max(0, (avail - total_w) // 2)
        tabs.styles.padding = (0, 0, 0, pad)

    def on_resize(self, event: events.Resize) -> None:
        """Capture terminal size and adapt layout + panels immediately.

        sess.1508 round 2: width-sensitive panels (heatmap) ridraw deferito
        via debounce 150ms — Textual emette burst di event durante drag e il
        rebuild sincrono creava race con _refresh_fast (entrambi su #heat-static).
        Layout-level adjustments (height TitleBar, top-row min_height,
        center_tabs) restano immediati perché low-cost.
        """
        self._cols = event.size.width
        self._rows = event.size.height
        # WAR ROOM: top-row scala con lo schermo (40% rows), min più alto
        new_min = max(18, min(32, self._rows * 40 // 100))
        top_row = self.query_one("#top-row")
        top_row.styles.min_height = new_min
        top_row.styles.max_height = max(new_min + 28, 54)
        title_bar = self.query_one("#title-bar", TitleBar)
        if os.environ.get("M5W_NO_ASCII") == "1":
            show = False
        elif os.environ.get("M5W_FORCE_ASCII") == "1":
            show = True
        else:
            show = self._rows >= 40 and self._cols >= 52
        title_bar.show_ascii = show
        title_bar.styles.height = 9 if self._rows < 40 else (18 if show else 11)
        self._center_tabs()
        # sess.1525: layout refresh immediato per cancellare Footer/body ghost
        # durante il delta debounce — Textual diffa solo le celle cambiate,
        # quindi è O(viewport) e non collide col rebuild heatmap deferito.
        self.refresh(layout=True)
        # Debounce heatmap rebuild
        if self._resize_timer is not None:
            try:
                self._resize_timer.stop()
            except Exception:
                pass
        self._resize_timer = self.set_timer(0.15, self._on_resize_settled)

    def _on_resize_settled(self) -> None:
        """Heatmap rebuild dopo che il resize si è stabilizzato (sess.1508 round 2).

        sess.1525: full layout refresh in coda — fix Footer ghost / body
        doppio dopo split-screen o fullscreen toggle su macOS Terminal.app.
        """
        heat_markup = render_heatmap(self._core_history, cols=self._heatmap_cols())
        self._render_cache["heat-static"] = heat_markup
        try:
            _heat_text = RichText.from_markup(heat_markup)
            _heat_text.no_wrap = True
            self.query_one("#heat-static", Static).update(_heat_text)
        except Exception:
            pass
        self.refresh(layout=True)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        fullscreen_tabs = {"tab-logs", "tab-sent", "tab-procs", "tab-tent", "tab-debug"}
        hide = event.pane is not None and event.pane.id in fullscreen_tabs
        self.query_one("#top-row").display = not hide
        # sess.1541: lazy render → la nuova tab era saltata dall'ultimo
        # _refresh_slow. Schedula refresh immediato (non bloccante) così
        # l'utente vede dati freschi subito invece di aspettare il prossimo
        # tick (max 5s). Data fetch usa cache TTL upstream se disponibile.
        try:
            self.run_worker(self._refresh_slow(), exclusive=False, group="slow_refresh")
        except Exception:
            pass
        # sess.1539: TitleBar UNIFORME cross-tab — rimosso lo shrink per-tab
        # (prima collassava a 6 righe su Heatmap/Logs/Procs/Tent/Debug).
        # Sizing ora deciso solo da on_resize (cols/rows-based): l'header è
        # presenza identitaria costante che non saltella tra tab.
        try:
            title_bar = self.query_one("#title-bar", TitleBar)
            if os.environ.get("M5W_NO_ASCII") == "1":
                show = False
            elif os.environ.get("M5W_FORCE_ASCII") == "1":
                show = True
            else:
                show = self._rows >= 35 and self._cols >= 52
            title_bar.show_ascii = show
            title_bar.styles.height = 9 if self._rows < 40 else (18 if show else 11)
        except Exception:
            pass

    async def on_mount(self) -> None:
        self._init_tables()
        self._center_tabs()
        await asyncio.to_thread(psutil.cpu_percent, percpu=True, interval=None)
        # Seed disk/net deltas
        await asyncio.to_thread(ds.disk_io_rate)
        await asyncio.to_thread(ds.net_io_rate)
        # sess.1508 round 2 hierarchy fix: KPI è il dato decision-driving
        # (MRR, Outstanding, Pipeline) → default tab al posto di Heatmap
        # decorativa. Top-of-fold finanziario.
        try:
            self.query_one(TabbedContent).active = "tab-kpi"
        except Exception:
            pass
        # Sfasamento set_interval per evitare allineamento ogni 10s che
        # bursta tutti i widget update insieme (audit perf round 2).
        self.set_interval(2.0, self._refresh_fast)
        await asyncio.sleep(0.3)
        self.set_interval(5.0, self._refresh_slow)
        # sess.1508 round 4: telemetry spine intervals
        self.set_interval(2.0, self._refresh_debug_panel)
        self.set_interval(60.0, self._flush_metrics)
        self._metrics.record_rss()  # baseline
        # Immediate initial load — all panels (incl. Graph + KPI) visible on startup
        await self._refresh_fast()
        await self._refresh_slow()
        # sess.1534 round 4: pre-warm cache dei 5 moduli roadmap in background.
        # vector + trap fanno I/O caro (~500ms) — facciamoli mentre il KPI tab
        # è visibile, così non bloccano il primo switch a tab Logs.
        asyncio.create_task(self._prewarm_roadmap_cache())

    async def _prewarm_roadmap_cache(self) -> None:
        """Background prewarm — riempie le cache TTL dei 6 moduli roadmap."""
        await asyncio.gather(
            asyncio.to_thread(_safe_render_polestar),
            asyncio.to_thread(_safe_render_outstanding),
            asyncio.to_thread(_safe_render_filaments),
            asyncio.to_thread(_safe_render_blocks),
            asyncio.to_thread(_safe_render_vectors),
            asyncio.to_thread(_safe_render_traps),
            return_exceptions=True,
        )

    def _flush_metrics(self) -> None:
        """Append snapshot JSONL ogni 60s — sess.1508 round 4."""
        try:
            self._jsonl_writer.flush_metrics(self._metrics)
        except Exception as e:
            self._py_logger.exception("flush_metrics fail: %s", e)

    def _init_tables(self) -> None:
        pt = self.query_one("#proc-table", DataTable)
        pt.add_columns("PID", "Process", "CPU %", "RAM MB")
        tt = self.query_one("#tent-table", DataTable)
        tt.add_columns(" ", "Process", "PID", "CPU %", "RAM MB", "Command", "🗑")
        lt = self.query_one("#log-table", DataTable)
        lt.add_columns("Time", " ", "Event", "Source", "Detail")

    # ── Critical flash (sess.1508 round 3 motion + round 4 webhook) ──────────
    def _trigger_critical_flash(self, reason: str = "") -> None:
        """Border bottom HOT_PINK per 5s — l'app urla quando lo deve.

        Triggerato da: pressure='error', CPU spike sostenuto.
        Auto-revert via _refresh_fast tick.
        Round 4: metric + log + webhook fire-and-forget se config TOML
        ha [webhook] url.
        """
        if self._paused or self._critical_flash_active:
            return
        try:
            tb = self.query_one("#title-bar", TitleBar)
            tb.styles.border_bottom = ("heavy", HOT_PINK)
            self._critical_flash_active = True
            self._critical_until = time.time() + 5.0
            self._metrics.flash(reason or "unspecified")
            self._py_logger.info("critical flash: %s", reason)
            if reason:
                self.notify(f"⚠ CRITICAL: {reason}", severity="error", timeout=4.0)
        except Exception as e:
            self._py_logger.exception("trigger_critical_flash fail: %s", e)
        # Webhook fire-and-forget (sess.1508 round 4)
        if self._webhook_url:
            try:
                import cli_commands
                cli_commands.webhook_post(
                    self._webhook_url,
                    {
                        "event":     "critical_flash",
                        "reason":    reason,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "cpu_avg":   self._cpu_avg() if self._cpu_percents else 0.0,
                        "mem_pct":   self._mem.get("pct", 0.0),
                        "pressure":  str(self._mem.get("pressure", ("—", "ok"))[0]),
                    },
                )
            except Exception as e:
                self._py_logger.exception("webhook_post fail: %s", e)

    def _end_critical_flash(self) -> None:
        """Revert border al colore default (TEAL o ORANGE se paused)."""
        try:
            tb = self.query_one("#title-bar", TitleBar)
            target_color = ORANGE if self._paused else TEAL
            tb.styles.border_bottom = ("heavy", target_color)
            self._critical_flash_active = False
        except Exception:
            pass

    # ── Render cache helper (sess.1508 round 2 + round 4 telemetry) ──────────
    def _update_if_changed(self, widget_id: str, content: str) -> None:
        """Static.update solo se il contenuto è diverso dal cache.

        Risolve audit perf round 2: render_*() chiamato N volte/s anche su
        dati invariati → forced repaint. Skip update se hash uguale.
        Round 4: registra cache hit/miss in self._metrics per claim
        verifiability.
        """
        if self._render_cache.get(widget_id) == content:
            self._metrics.cache_hit()
            return
        self._metrics.cache_miss()
        self._render_cache[widget_id] = content
        try:
            self.query_one(f"#{widget_id}", Static).update(content)
        except Exception as e:
            self._py_logger.exception("_update_if_changed widget=%s fail: %s", widget_id, e)

    # ── Refresh ───────────────────────────────────────────────────────────────
    async def _refresh_fast(self) -> None:
        if self._paused:
            return
        # sess.1508 round 4 telemetry: misura frame_ms (claim verifiability).
        _t_start = time.perf_counter()
        self._tick += 1

        self._cpu_percents, self._disk, self._net = await asyncio.gather(
            ds.cpu_per_core(),
            asyncio.to_thread(ds.disk_io_rate),
            asyncio.to_thread(ds.net_io_rate),
        )
        overall = mean(self._cpu_percents) if self._cpu_percents else 0
        self._cpu_history.append(overall)
        for i, v in enumerate(self._cpu_percents):
            if i in self._core_history:
                self._core_history[i].append(v)

        # ── Feed: sustained CPU spike (>80% avg for 3 consecutive ticks = 6s)
        # Append only — widget updated once at end of _refresh_fast to avoid double-render
        if overall >= 80:
            self._cpu_spike_ticks += 1
            if self._cpu_spike_ticks == 3:
                ts = time.strftime("%H:%M:%S")
                self._event_feed.appendleft(
                    f"[{DIM}]{ts}[/] 🔥 [{HOT_PINK}]CPU spike[/] [{DIM}]{overall:.0f}% avg[/]"
                )
                self._trigger_critical_flash(reason="CPU spike sustained")
        else:
            self._cpu_spike_ticks = 0

        # ── Idle freeze rainbow (sess.1508 round 3): system quiet → motion quiet.
        # Soglia 5% per 30 tick (~60s a 2s/tick). Auto-revive su prossimo spike.
        # NB: il toggle vero (con edge counters) è eseguito in fondo a _refresh_fast.
        if overall < 5.0:
            self._idle_ticks += 1
        else:
            self._idle_ticks = 0

        # ── Critical flash auto-revert (sess.1508 round 3) ────────────────────
        if self._critical_flash_active and time.time() >= self._critical_until:
            self._end_critical_flash()

        mem_now = self._mem.get('pct', 0)
        la1, _, _ = ds.load_avg()

        # sess.1508 round 2: tutti i Static.update via _update_if_changed →
        # skip paint inutili quando il contenuto è identico (CPU stabile).
        self._update_if_changed(
            "cpu-content",
            f"[bold {ELEC_BLUE}]⚡ CPU[/]  [{DIM}]· M5 Max 18C[/]\n" +
            render_cpu(self._cpu_percents, self._cpu_history, self._disk, self._net),
        )
        heat_markup = render_heatmap(self._core_history, cols=self._heatmap_cols())
        if self._render_cache.get("heat-static") != heat_markup:
            self._render_cache["heat-static"] = heat_markup
            _heat_text = RichText.from_markup(heat_markup)
            _heat_text.no_wrap = True
            self.query_one("#heat-static", Static).update(_heat_text)
        self._update_if_changed(
            "analytics-static",
            render_analytics(self._cpu_history, self._mem_history,
                             self._core_history, overall, mem_now, la1,
                             spark_w=self._spark_width()),
        )
        vd = await asyncio.to_thread(voice_data)
        self._update_if_changed(
            "voice-static",
            render_voice(vd, level_w=self._voice_width()),
        )
        # ── Feed: Jarvis voice state transitions (offline ↔ live)
        new_voice = vd.get('state', 'offline')
        if self._prev_voice_state and new_voice != self._prev_voice_state:
            ts = time.strftime("%H:%M:%S")
            if new_voice == 'offline':
                self._event_feed.appendleft(f"[{DIM}]{ts}[/] 🔇 [{DIM}]Jarvis offline[/]")
            else:
                self._event_feed.appendleft(f"[{DIM}]{ts}[/] 🎙 [{LIME}]Jarvis live[/] [{DIM}]{new_voice}[/]")
        self._prev_voice_state = new_voice
        # ── Feed widget: skip update se invariato (sess.1508 audit fix).
        self._update_if_changed("feed-static", _UNIFEED_HDR + render_feed(self._event_feed))
        self._update_subtitle(overall, la1)
        # ── Idle freeze edge-detect (sess.1508 round 4 telemetry counters) ──
        try:
            tb = self.query_one("#title-bar", TitleBar)
            new_frozen = (self._idle_ticks >= 30)
            if new_frozen and not self._prev_idle_frozen:
                self._metrics.idle_enter()
            elif not new_frozen and self._prev_idle_frozen:
                self._metrics.idle_exit()
            self._prev_idle_frozen = new_frozen
            tb.idle_frozen = new_frozen
        except Exception as e:
            self._py_logger.exception("idle_frozen toggle fail: %s", e)
        # ── Frame timing record (sess.1508 round 4)
        _ms = (time.perf_counter() - _t_start) * 1000.0
        self._metrics.record_frame(_ms)
        if _ms > 500:
            self._py_logger.warning("slow _refresh_fast: %.1fms (tick #%d)", _ms, self._tick)

    async def _refresh_slow(self) -> None:
        if self._paused:
            return
        _t_slow = time.perf_counter()
        (self._mem, self._bat, self._proc_counts, self._graph_data,
         self._kpi_data, self._focus_data, self._log_entries,
         self._sentinel_data, self._health_data) = await asyncio.gather(
            asyncio.to_thread(ds.unified_memory),
            asyncio.to_thread(ds.battery),
            asyncio.to_thread(_count_claude_mcp),
            asyncio.to_thread(vault_parser.vault_graph_data),
            asyncio.to_thread(kpi_widget.read_kpi_data),
            asyncio.to_thread(ds.current_focus),
            asyncio.to_thread(ds.log_feed),
            asyncio.to_thread(_read_sentinel_data),
            asyncio.to_thread(health_widget.read_health_data),
        )
        self._mem_history.append(self._mem.get('pct', 0))

        # ── Feed: detect memory pressure transitions
        ts = time.strftime("%H:%M:%S")
        new_pressure = self._mem.get('pressure', ('', 'ok'))[1]
        if self._prev_pressure and new_pressure != self._prev_pressure:
            # sess.1508 round 3: dict pressure unificati in polpo_charts.
            c = pc_const.PRESSURE_COLOR.get(new_pressure, DIM)
            e = pc_const.PRESSURE_EMOJI.get(new_pressure, '⚫')
            _label = pc_const.PRESSURE_LABEL
            self._event_feed.appendleft(
                f"[{DIM}]{ts}[/] {e} [{c}]pressure[/] "
                f"[{DIM}]{_label.get(self._prev_pressure, self._prev_pressure)}"
                f" → {_label.get(new_pressure, new_pressure)}[/]"
            )
            # sess.1508 round 3 motion: pressure='error' → critical flash.
            if new_pressure == "error":
                self._trigger_critical_flash(reason="memory pressure CRITICAL")
        self._prev_pressure = new_pressure

        # ── Feed: detect swap activation / deactivation
        swap_active = self._mem.get('swap', 0) > 0.5e9
        if swap_active != self._prev_swap_active:
            if swap_active:
                swap_gb = self._mem.get('swap', 0) / 1024**3
                self._event_feed.appendleft(
                    f"[{DIM}]{ts}[/] 🟠 [{ORANGE}]swap activated[/] [{DIM}]+{swap_gb:.1f}GB[/]"
                )
            else:
                self._event_feed.appendleft(
                    f"[{DIM}]{ts}[/] 🟢 [{LIME}]swap cleared[/]"
                )
        self._prev_swap_active = swap_active

        # ── Feed: KPI layer synapse — MRR and pipeline deltas (strategic → operational)
        new_mrr      = self._kpi_data.get('mrr', 0)
        new_pipeline = self._kpi_data.get('pipeline_weighted', 0)
        if self._prev_mrr and new_mrr != self._prev_mrr:
            delta = new_mrr - self._prev_mrr
            sign  = "+" if delta > 0 else ""
            col   = LIME if delta > 0 else HOT_PINK
            self._event_feed.appendleft(
                f"[{DIM}]{ts}[/] 💰 [{col}]MRR {sign}€{abs(int(delta)):,}[/]".replace(',', '.')
            )
        if self._prev_pipeline and abs(new_pipeline - self._prev_pipeline) > 500:
            delta = new_pipeline - self._prev_pipeline
            sign  = "+" if delta > 0 else ""
            col   = LIME if delta > 0 else ORANGE
            self._event_feed.appendleft(
                f"[{DIM}]{ts}[/] 📊 [{col}]pipeline {sign}€{abs(int(delta)):,}[/]".replace(',', '.')
            )
        self._prev_mrr      = new_mrr
        self._prev_pipeline = new_pipeline

        # sess.1508 round 2: tutti via _update_if_changed → skip render
        # quando data è invariato (TTL cache 30-60s a monte).
        self._update_if_changed("feed-static", _UNIFEED_HDR + render_feed(self._event_feed))

        cpu_avg = mean(self._cpu_percents) if self._cpu_percents else 0
        la1, _, _ = ds.load_avg()
        _mem_total_gb = self._mem.get('total', 0) / 1024 ** 3
        _mem_gb_str = f"{_mem_total_gb:.0f}GB" if _mem_total_gb > 0 else "—GB"

        # sess.1541: lazy render per tab attiva — frame p95 era 531ms (vs 33ms
        # budget) perché ogni _refresh_slow rendeva tutti i widget, anche quelli
        # in tab non visibili. I 6 widget roadmap (cold ~310ms vector+trap) +
        # graph_widget + sentinel domanvano il frame. Data fetch resta intero
        # (parallelo, alimenta UNIFEED events). Solo i render sono guardati.
        try:
            _active_tab = self.query_one("#tab-area", TabbedContent).active
        except Exception:
            _active_tab = None
        _fullscreen_tabs = {"tab-logs", "tab-sent", "tab-procs", "tab-tent", "tab-debug", "tab-pulse"}
        _top_row_visible = _active_tab not in _fullscreen_tabs

        # mem-content vive in #top-row → render solo se top-row visibile.
        if _top_row_visible:
            self._update_if_changed(
                "mem-content",
                f"[bold {LIME}]🧠 UNIFIED MEMORY[/]  [{DIM}]· {_mem_gb_str}[/]\n" +
                render_mem(self._mem, self._mem_history, cpu_avg, la1),
            )
        # proc-table (tab-procs) e tent-table (tab-tent) condividono il fetch.
        if _active_tab in ("tab-procs", "tab-tent"):
            await self._update_processes()
        if _active_tab == "tab-graph":
            self._update_if_changed(
                "graph-static",
                graph_widget.render_graph(
                    self._graph_data,
                    filter_mode=self._graph_filter,
                    cpu_percents=self._cpu_percents,
                    cpu_history=self._cpu_history,
                    mem=self._mem,
                    mem_history=self._mem_history,
                ),
            )
        if _active_tab == "tab-kpi":
            self._update_if_changed("kpi-static", kpi_widget.render_kpi(self._kpi_data))
        if _active_tab == "tab-health":
            self._update_if_changed("health-static", health_widget.render_health(self._health_data))
        if _active_tab == "tab-pulse":
            # sess.1568: read soul snapshot lazy (TTL via cache attr)
            now_ts = time.time()
            if not hasattr(self, "_soul_data") or now_ts - getattr(self, "_soul_ts", 0) > 30.0:
                self._soul_data = await asyncio.to_thread(read_session_current_summary)
                self._soul_ts = now_ts
            self._update_if_changed(
                "pulse-static",
                render_pulse(self._kpi_data, self._mem_history, self._cpu_history,
                             self._mem, self._soul_data),
            )
        if _active_tab == "tab-sent":
            self._render_sentinel(self._sentinel_data)

        # sess.1534 round 4-6: roadmap-aware strip refresh (6 moduli).
        # Cold timings: vector ~240ms, trap ~310ms, others <10ms.
        # sess.1541: rendered SOLO quando tab-logs è attivo (cache TTL upstream
        # tiene comunque caldi i dati per quando l'utente switcha).
        if _active_tab == "tab-logs":
            self._render_logs(self._log_entries)
            roadmap_renders = await asyncio.gather(
                asyncio.to_thread(_safe_render_polestar),
                asyncio.to_thread(_safe_render_outstanding),
                asyncio.to_thread(_safe_render_vectors),
                asyncio.to_thread(_safe_render_traps),
                asyncio.to_thread(_safe_render_filaments),
                asyncio.to_thread(_safe_render_blocks),
                return_exceptions=True,
            )
            for widget_id, result in zip(
                ("polestar-strip", "outstanding-section", "vectors-strip",
                 "traps-banner", "filaments-section", "blocks-section"),
                roadmap_renders,
            ):
                if isinstance(result, Exception):
                    continue
                self._update_if_changed(widget_id, result or "")
        # sess.1508 round 4 telemetry: slow_ms + RSS poll
        self._metrics.record_slow((time.perf_counter() - _t_slow) * 1000.0)
        self._metrics.record_rss()

    def _render_sentinel(self, data: dict) -> None:
        canary_str, alert_str = render_sentinel(data)
        # sess.1508 round 2: diff cache via helper.
        self._update_if_changed("canary-static", canary_str)
        self._update_if_changed("alerts-static", alert_str)

    def _render_logs(self, entries: list) -> None:
        lt = self.query_one("#log-table", DataTable)
        lt.clear()
        _SRC_COLOR = {
            "GHL Leads":  ELEC_BLUE,  "CRM Alert": TEAL,
            "Setter":     HOT_PINK,   "WhatsApp":  SOFT_GREEN,
            "Jarvis":     DEEP_PURPL, "Voice":     DEEP_PURPL,
            "Security":   RED,        "Health Bot": LIME,
            "Outreach":   ORANGE,     "Memory Guard": CYAN,
            "Sites Health": TEAL,     "Claude":    HOT_PINK,
            "Session Sync": DIM,      "Vault RAG": TEAL,
            "Notes Sync": DIM,        "Outreach Err": ORANGE,
        }
        # sess.1534: severity badge — P0 rosso solido, P1 giallo, info dot dim
        _SEV_BADGE = {
            'P0':  f"[bold {RED}]●[/]",
            'P1':  f"[bold {ORANGE}]●[/]",
            'info': f"[{DIM}]·[/]",
        }
        # sess.1534 round 2: NEW = stella teal accanto al severity badge.
        _NEW_STAR  = f"[bold {TEAL}]★[/]"
        _NEW_EMPTY = " "

        # sess.1534 round 3: priority-sticky sections.
        # L'occhio non deve cercare il P0 nel feed cronologico — sta in cima fisso.
        # Pattern Bloomberg/Slack: alert prima, news dopo. 3 sezioni con header
        # divider colorato. Within-section ordering: chronological desc.
        bucket_p0:   list = [e for e in entries if e.get('severity') == 'P0']
        bucket_p1:   list = [e for e in entries if e.get('severity') == 'P1']
        bucket_info: list = [e for e in entries if e.get('severity') not in ('P0', 'P1')]

        def _row(e: dict):
            src_col = _SRC_COLOR.get(e['source'], DIM)
            sev = e.get('severity', 'info')
            sev_badge = _SEV_BADGE.get(sev, _SEV_BADGE['info'])
            title_col = RED if sev == 'P0' else (ORANGE if sev == 'P1' else None)
            title_str = trunc(e['title'], 36)
            if title_col:
                title_str = f"[{title_col}]{title_str}[/]"
            new_marker = _NEW_STAR if e.get('is_new') else _NEW_EMPTY
            lt.add_row(
                f"[{DIM}]{e['ts']}[/]",
                f"{new_marker}{sev_badge} {e['emoji']}",
                title_str,
                f"[{src_col}]{e['source']}[/]",
                f"[{DIM}]{trunc(e['desc'], 60)}[/]",
            )

        def _section_header(label: str, count: int, color: str) -> None:
            """Inietta una row che agisce da divider semantico."""
            # Riga full-width: colonne svuotate tranne la 3a che porta il banner.
            banner = f"[bold {color}]━━━ {label} ({count}) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]"
            lt.add_row("", "", banner, "", "")

        if bucket_p0:
            _section_header("🔴 P0 ALERT", len(bucket_p0), RED)
            for e in bucket_p0:
                _row(e)
        if bucket_p1:
            _section_header("🟡 P1 ATTENZIONE", len(bucket_p1), ORANGE)
            for e in bucket_p1:
                _row(e)
        if bucket_info:
            _section_header("📋 RECENT ACTIVITY", len(bucket_info), DIM)
            for e in bucket_info:
                _row(e)

        # sess.1534: dynamic header — meta refresh ad ogni render
        try:
            meta = ds.log_feed_meta()
        except Exception:
            meta = None
        if meta is not None:
            self._update_if_changed("logs-header", _render_activity_header(meta))

    async def _update_processes(self) -> None:
        procs = await asyncio.to_thread(ds.top_processes, 16)
        pt = self.query_one("#proc-table", DataTable)
        pt.clear()
        for p in procs:
            pt.add_row(str(p['pid']), p['name'],
                       f"[{_c(p['cpu'])}]{p['cpu']:5.1f}[/]",
                       f"[{CYAN}]{p['mem_mb']:7.0f}[/]")

        tents = await asyncio.to_thread(ds.tentacoli)
        tt = self.query_one("#tent-table", DataTable)
        tt.clear()
        for t in tents:
            tt.add_row(t['emoji'], t['name'], str(t['pid']),
                       f"[{_c(t['cpu'])}]{t['cpu']:5.1f}[/]",
                       f"[{MAG}]{t['mem_mb']:7.0f}[/]",
                       t['cmd'],
                       f"[bold {HOT_PINK}]🗑[/]")
        self.query_one("#focus-static", Static).update(render_focus(self._focus_data))

    def _update_subtitle(self, cpu: float, load: float) -> None:
        bat  = self._bat
        pct  = bat.get('pct', 100)
        bat_emoji = '⚡' if bat.get('charging') else ('🔋' if pct > 20 else '🪫')
        prs  = self._mem.get('pressure', ('—', 'ok'))
        # sess.1508 round 3: dict pressure unificati in polpo_charts.
        pc   = pc_const.PRESSURE_COLOR.get(prs[1], DIM)
        prs_emoji = pc_const.PRESSURE_EMOJI.get(prs[1], '⚪')
        d, n = self._disk, self._net
        live = f'[bold {LIME}]🟢 LIVE[/]' if not self._paused else f'[bold {ORANGE}]⏸ PAUSE[/]'

        free_gb = self._mem.get('free', 0) / 1024**3
        swap_gb = self._mem.get('swap', 0) / 1024**3
        comp_gb = self._mem.get('compressed', 0) / 1024**3
        swap_color = HOT_PINK if swap_gb > 0.5 else (ORANGE if swap_gb > 0 else DIM)

        sep = f"  [{DIM}]┃[/]  "
        # Core metrics — always visible
        status_parts = [
            live,
            f"{bat_emoji} [bold {LIME}]{pct}%[/]",
            f"⚡ [bold {_c(cpu)}]{cpu:4.0f}%[/]",
            f"⚖ [bold {_c(load / N_CORES * 100)}]{load:4.1f}[/]",
        ]
        # Add memory pressure section at >= 90 cols
        if self._cols >= 90:
            status_parts.append(
                f"{prs_emoji} [bold {pc}]{prs[0]}[/] "
                f"[{LIME}]{free_gb:.1f}G[/][{DIM}]free[/] "
                f"[{ORANGE}]{comp_gb:.1f}G[/][{DIM}]z[/] "
                f"[{swap_color}]{swap_gb:.1f}G[/][{DIM}]swp[/]"
            )
        # Add disk + net I/O at >= 130 cols
        if self._cols >= 130:
            status_parts.append(
                f"💾 [{CYAN}]↓{d.get('read', 0):4.1f}[/] [{HOT_PINK}]↑{d.get('write', 0):4.1f}[/]"
            )
            status_parts.append(
                f"🌐 [{CYAN}]↓{n.get('recv', 0):4.2f}[/] [{HOT_PINK}]↑{n.get('sent', 0):4.2f}[/]"
            )
        status = sep.join(status_parts)

        # Rich-info header (refresh ogni tick fast)
        uptime_s = int(time.time() - self._boot_time)
        # sess.1539 round 2: payload KPI via single-source-of-truth in kpi_widget.
        # Sparkline trend incluse (mrr/out/pipe) — leggono lo stesso _HISTORY_*
        # popolato da render_kpi → zero divergenza tra panel KPI e TitleBar line5.
        kpi_payload = kpi_widget.kpi_for_titlebar(self._kpi_data or {})

        rich = {
            'session':      self._sess_n,
            'uptime':       _format_uptime(uptime_s),
            'claude_count': self._proc_counts.get('claude', 0),
            'mcp_count':    self._proc_counts.get('mcp', 0),
            'time':         time.strftime("%H:%M:%S"),
            'cols':         self._cols,
            'kpi':          kpi_payload,
        }

        title_bar = self.query_one("#title-bar", TitleBar)
        title_bar.status = status
        title_bar.rich_info = rich

    # ── Actions ───────────────────────────────────────────────────────────────
    def _cpu_avg(self) -> float:
        return mean(self._cpu_percents) if self._cpu_percents else 0.0

    async def action_force_refresh(self) -> None:
        await self._refresh_fast()
        await self._refresh_slow()
        cpu  = self._cpu_avg()
        free = self._mem.get('free', 0) / 1024 ** 3
        bat  = self._bat.get('pct', 100)
        n_cl = self._proc_counts.get('claude', 0)
        n_mc = self._proc_counts.get('mcp', 0)
        self.notify(
            f"⟳  ⚡ {cpu:.0f}%  ·  🧠 {free:.1f}G free"
            f"  ·  🔋 {bat}%  ·  🐙 ×{n_cl}  🔌 ×{n_mc}",
            severity="information",
            timeout=2.5,
        )

    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        # sess.1508 audit fix: feedback visivo prominente — border TitleBar
        # passa a ORANGE quando paused (prima il segnale era solo nel testo
        # status, troncabile a 80 cols).
        try:
            tb = self.query_one("#title-bar", TitleBar)
            tb.styles.border_bottom = ("heavy", ORANGE if self._paused else TEAL)
        except Exception:
            pass
        if self._paused:
            self.notify(
                f"⏸  Paused at {time.strftime('%H:%M:%S')}  ·  tick #{self._tick}",
                severity="warning",
                timeout=2.0,
            )
        else:
            self.notify(
                f"▶  Live  ·  ⚡ {self._cpu_avg():.0f}%  ·  tick #{self._tick}",
                severity="information",
                timeout=1.5,
            )

    def action_show_tab_heat(self) -> None:
        self.query_one(TabbedContent).active = "tab-heat"
        window_s = 60 * 2
        self.notify(f"🌡  Heatmap  ·  {window_s}s window  ·  {N_CORES} cores", timeout=1.5)

    def action_show_tab_stats(self) -> None:
        self.query_one(TabbedContent).active = "tab-stats"
        mem_pct = self._mem.get('pct', 0)
        self.notify(
            f"📈  Analytics  ·  CPU avg {self._cpu_avg():.0f}%  ·  RAM {mem_pct:.0f}%",
            timeout=1.5,
        )

    def action_show_tab_procs(self) -> None:
        self.query_one(TabbedContent).active = "tab-procs"
        self.notify("🔝  Processes  ·  top 16 ranked by CPU + RAM", timeout=1.5)

    def action_show_tab_tent(self) -> None:
        self.query_one(TabbedContent).active = "tab-tent"
        n_cl = self._proc_counts.get('claude', 0)
        n_mc = self._proc_counts.get('mcp', 0)
        self.notify(
            f"🐙  Tentacoli  ·  {n_cl} Claude  ·  {n_mc} MCP  ·  background workers",
            timeout=1.5,
        )

    def action_show_tab_graph(self) -> None:
        self.query_one(TabbedContent).active = "tab-graph"
        self.notify(f"🕸  Graph  ·  filter: {self._graph_filter}", timeout=1.5)

    def action_show_tab_kpi(self) -> None:
        self.query_one(TabbedContent).active = "tab-kpi"
        mrr = self._kpi_data.get('mrr', 0)
        self.notify(f"📊  KPI  ·  MRR €{int(mrr):,}".replace(',', '.'), timeout=1.5)

    def action_show_tab_logs(self) -> None:
        self.query_one(TabbedContent).active = "tab-logs"
        lt = self.query_one("#log-table", DataTable)
        self.notify(f"📋  Activity Stream  ·  {lt.row_count} events", timeout=1.5)

    def action_show_tab_sent(self) -> None:
        self.query_one(TabbedContent).active = "tab-sent"
        n_alerts = len(self._sentinel_data.get("alerts", []))
        n_canaries = len(self._sentinel_data.get("canaries", {}))
        self.notify(f"🛡  Sentinel  ·  {n_canaries} canary  ·  {n_alerts} alerts", timeout=1.5)

    def _dispatch_tentacle(self, mode: str) -> None:
        """sess.1568 strategic upgrade #4: tentacle dispatcher.

        mode:
          - "data"   → insight live + data_prompt clipboard (audit dati tab)
          - "design" → insight live + design_prompt clipboard (audit viz/UX)
        """
        try:
            tab_id = self.query_one(TabbedContent).active
        except Exception:
            self.notify("🐙 nessuna tab attiva", timeout=2.0); return
        fn = _TENTACLES.get(tab_id)
        if not fn:
            self.notify(f"🐙 nessun tentacolo per {tab_id}", timeout=2.0); return
        try:
            insight, data_prompt, design_prompt = fn(self)
        except Exception as e:
            self.notify(f"🐙 tentacle fail: {type(e).__name__}",
                        severity="error", timeout=3.0); return
        prompt = data_prompt if mode == "data" else design_prompt
        prefix = "🐙 DATA" if mode == "data" else "🎨 DESIGN"
        if prompt and _copy_clipboard(prompt):
            self.notify(f"{prefix}  ·  {insight}\n✓ deep prompt → clipboard "
                        f"(Cmd+V in Antigravity)", timeout=6.0,
                        severity="information")
        elif prompt:
            self.notify(f"{prefix}  ·  {insight}\n⚠ pbcopy fail",
                        timeout=4.0, severity="warning")
        else:
            self.notify(f"{prefix}  ·  {insight}", timeout=4.0)

    def action_tentacle_data(self) -> None:
        """🐙 t — tentacle DATA insight per tab corrente."""
        self._dispatch_tentacle("data")

    def action_tentacle_design(self) -> None:
        """🎨 T — tentacle DESIGN audit per tab corrente."""
        self._dispatch_tentacle("design")

    def action_show_tab_pulse(self) -> None:
        """Tab Polpo Pulse — predictive + business + soul (sess.1568 strategic upgrade)."""
        self.query_one(TabbedContent).active = "tab-pulse"
        mrr = int(self._kpi_data.get('mrr') or 0)
        fc = forecast_eta(self._mem_history, threshold=85.0, sample_dt_s=5.0)
        fc_str = f"ETA→85% {_fmt_eta(fc['eta_seconds'])}" if fc else "RAM stable"
        self.notify(
            f"🔮  Pulse  ·  MRR €{mrr:,}  ·  {fc_str}".replace(',', '.'),
            timeout=2.0,
        )

    def action_show_tab_debug(self) -> None:
        """Tab telemetry spine — sess.1508 round 4."""
        self.query_one(TabbedContent).active = "tab-debug"
        s = self._metrics.summary()
        self.notify(
            f"🔬  Debug  ·  frame p95 {s['frame_ms']['p95']:.0f}ms  ·  "
            f"cache {s['cache']['ratio']*100:.1f}%  ·  flash {s['flash']['count']}",
            timeout=2.0,
        )

    def _refresh_debug_panel(self) -> None:
        """Aggiorna #debug-static. Chiamato da set_interval(2.0, ...) in on_mount."""
        try:
            content = render_debug_panel(self._metrics, lru_funcs=[_rainbow_hex])
            self._update_if_changed("debug-static", content)
        except Exception as e:
            self._py_logger.exception("refresh_debug_panel fail: %s", e)

    async def action_snapshot(self) -> None:
        """Snapshot JSON in ~/.local/run/m5-watcher/snapshot_<ts>.json — sess.1508 round 4."""
        import cli_commands
        ts = time.strftime("%Y%m%dT%H%M%S")
        snap_dir = Path.home() / ".local" / "run" / "m5-watcher"
        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.notify(f"📸 mkdir fail: {e}", severity="error", timeout=3.0)
            return
        dest = snap_dir / f"snapshot_{ts}.json"
        snap_args = argparse.Namespace(output=str(dest), pretty=True)
        try:
            rc = await asyncio.to_thread(cli_commands.cmd_snapshot, snap_args)
            if rc == 0:
                self.notify(f"📸 snapshot → {dest.name}", severity="information", timeout=3.0)
                self._py_logger.info("snapshot saved: %s", dest)
            else:
                self.notify("📸 snapshot fallita — vedi log", severity="error", timeout=3.0)
        except Exception as e:
            self._py_logger.exception("action_snapshot fail: %s", e)
            self.notify(f"📸 errore snapshot: {e}", severity="error", timeout=3.0)

    def action_triage(self) -> None:
        self.push_screen(TriageScreen())

    def action_show_help(self) -> None:
        """Cheat sheet keybinding (sess.1508 audit fix: discoverability)."""
        self.notify(
            "🐙 KEYS · q quit · r refresh · p pause · 1-8 tabs · "
            "f filter · c triage · k kill · s snapshot · d debug",
            severity="information",
            timeout=8.0,
        )

    def action_kill_tent_selected(self) -> None:
        import signal as _sig
        try:
            if self.query_one("#tab-area", TabbedContent).active != "tab-tent":
                return
        except Exception:
            return
        tt = self.query_one("#tent-table", DataTable)
        if tt.row_count == 0 or tt.cursor_row is None:
            return
        try:
            row = tt.get_row_at(tt.cursor_row)
            pid = int(str(row[2]).strip())
            name = str(row[1])
        except (ValueError, IndexError):
            return
        try:
            os.kill(pid, _sig.SIGTERM)
            self.notify(f"🗑 SIGTERM → {name} (PID {pid})", severity="warning", timeout=3)
        except ProcessLookupError:
            self.notify(f"PID {pid} già morto", severity="warning", timeout=2)
        except PermissionError:
            self.notify(f"⛔ Permission denied PID {pid}", severity="error", timeout=3)

    def action_cycle_graph_filter(self) -> None:
        modes = graph_widget.FILTER_MODES
        idx = modes.index(self._graph_filter)
        self._graph_filter = modes[(idx + 1) % len(modes)]
        self.query_one("#graph-static", Static).update(
            graph_widget.render_graph(
                self._graph_data,
                filter_mode=self._graph_filter,
                cpu_percents=self._cpu_percents,
                cpu_history=self._cpu_history,
                mem=self._mem,
                mem_history=self._mem_history,
            )
        )
        self.notify(f"🕸  Filter → [{self._graph_filter}]", severity="information", timeout=1.5)


def _load_user_config(path: Path | None) -> dict:
    """Load ~/.m5-watcher.toml — sess.1508 round 3 a11y/configurability fix.

    Schema atteso:
        [paths]
        kpi  = "~/Library/.../KPI.md"
        jarvis_dir = "~/.local/run/jarvis"

        [targets]
        mrr      = 10000
        cash     = 10000
        pipeline = 20000
        cold_goal  = 30
        cold_limit = 90

        [theme]
        high_contrast = false
        rainbow       = true
        ascii_banner  = "auto"   # "auto" | "off" | "on"

        [refresh]
        fast = 2.0
        slow = 5.0

    Tutte le chiavi sono opzionali; mancanze cadono su default hardcoded.
    Non crasha se TOML manca, è invalido, o tomllib non disponibile.
    """
    target = path or (Path.home() / ".m5-watcher.toml")
    if not target.exists():
        return {}
    try:
        try:
            import tomllib              # py 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                return {}
        with open(target, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _apply_user_config(cfg: dict) -> None:
    """Applica overrides da config utente — sess.1508 round 3."""
    if not cfg:
        return
    paths_cfg = cfg.get("paths", {})
    if "kpi" in paths_cfg:
        kpi_widget._KPI_PATH = Path(os.path.expanduser(str(paths_cfg["kpi"])))
    if "jarvis_dir" in paths_cfg:
        global JARVIS_DIR
        JARVIS_DIR = Path(os.path.expanduser(str(paths_cfg["jarvis_dir"])))
    targets = cfg.get("targets", {})
    if "mrr" in targets:
        kpi_widget._TARGET_MRR = float(targets["mrr"])
    if "cash" in targets:
        kpi_widget._TARGET_CASH = float(targets["cash"])
    if "pipeline" in targets:
        kpi_widget._TARGET_PIPE = float(targets["pipeline"])
    if "cold_goal" in targets:
        kpi_widget._COLD_GOAL = float(targets["cold_goal"])
    if "cold_limit" in targets:
        kpi_widget._COLD_LIMIT = float(targets["cold_limit"])


def main() -> None:
    """CLI entrypoint con flag a11y + configurability + subcommands (round 4)."""
    import cli_commands

    parser = argparse.ArgumentParser(
        prog="m5-watcher",
        description="🐙 M5 Max Watcher — Visual Analytics TUI for Apple Silicon.",
    )
    parser.add_argument("--config", type=Path, default=None,
                        help="Path al config TOML (default: ~/.m5-watcher.toml)")
    parser.add_argument("--no-rainbow", action="store_true",
                        help="Disabilita animazione rainbow ASCII (utile su SSH/epilepsy).")
    parser.add_argument("--no-ascii", action="store_true",
                        help="Banner ASCII permanentemente nascosto (recupera 7 righe).")
    parser.add_argument("--ascii", action="store_true",
                        help="Forza banner ASCII anche su window piccolo.")
    parser.add_argument("--high-contrast", action="store_true",
                        help="Color-blind safe ramp (viridis-style) + DIM più chiaro.")
    parser.add_argument("--debug", action="store_true",
                        help="Verbose logging (futuro: log file).")
    parser.add_argument("--version", action="version",
                        version=f"m5-watcher {__version__} ({__codename__})")

    # sess.1508 round 4: subcommands snapshot/tail-feed/export-kpi/health
    cli_commands.add_subparsers(parser)

    args = parser.parse_args()

    # Subcommand dispatch — exit before launching TUI
    if getattr(args, "cmd", None) is not None:
        handler = getattr(args, "func", None)
        if handler is not None:
            raise SystemExit(handler(args))
        parser.print_help()
        raise SystemExit(1)

    # ── TUI path (no subcommand) ──────────────────────────────────────────────
    cfg = _load_user_config(args.config)
    _apply_user_config(cfg)

    if args.high_contrast:
        os.environ["M5W_HIGH_CONTRAST"] = "1"
    if args.no_rainbow:
        os.environ["M5W_NO_RAINBOW"] = "1"
    if args.no_ascii:
        os.environ["M5W_NO_ASCII"] = "1"
    if args.ascii:
        os.environ["M5W_FORCE_ASCII"] = "1"

    # sess.1508 round 4: hydrate webhook URL da config [webhook] url
    M5Watcher._webhook_url = cfg.get("webhook", {}).get("url") or None

    M5Watcher().run()


if __name__ == "__main__":
    main()


# ── End of file ───────────────────────────────────────────────────────────────
# {__title__} v{__version__} — {__codename__}
# Forged in {__forged_in__} · 2026-05-02 · {__company__}
# {__copyright__}
# ──────────────────────────────────────────────────────────────────────────────
