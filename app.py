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

Keybindings: q quit · r refresh · p pause · 1-7 tab switch · f cycle graph filter
Zoom: bottom-right + / − buttons (delegate Cmd+/− to Ghostty)

================================================================================
"""
from __future__ import annotations

# ── Metadata ──────────────────────────────────────────────────────────────────
__title__        = "M5 Max Watcher"
__version__      = "2.5.0"
__release_date__ = "2026-05-03"
__codename__     = "Activity Stream Edition"
__author__       = "Mattia Calastri"
__email__        = "mattia@digitalastra.it"
__company__      = "Astra Digital Marketing"
__website__      = "https://digitalastra.it"
__license__      = "Proprietary © 2026 Astra Digital Marketing — All Rights Reserved"
__copyright__    = "© 2026 Mattia Calastri · Astra Digital Marketing"
__status__       = "Production"
__pillar__       = "Astra OS · Polpo Cockpit Suite"
__forged_in__    = "sess.1472"   # Activity Stream tab — cross-system log cascade
__credits__      = ("Polpo Design System", "Textual", "psutil", "Apple Silicon M5 Max")

import asyncio
import json
import math
import os
import re
import threading
import time
from collections import deque
from colorsys import hsv_to_rgb
from pathlib import Path
from statistics import mean

import psutil
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual import events
from textual.widgets import DataTable, Footer, Static, TabbedContent, TabPane
from rich.text import Text as RichText

import data_sources as ds
import vault_parser
import graph_widget
import kpi_widget

# ── Design tokens ──────────────────────────────────────────────────────────────
_TOKENS = json.loads((Path(__file__).parent / "polpo.tokens.json").read_text())
P = _TOKENS["palette"]
BG, BG_ALT           = P["polpo_bg"], P["polpo_bg_alt"]
TEAL, DIM            = P["polpo_teal"], P["polpo_dim"]
GREEN, YELLOW, RED   = P["polpo_green"], P["polpo_yellow"], P["polpo_red"]
FG, MAG, CYAN, SCAR  = P["polpo_fg"], P["polpo_magenta"], P["polpo_cyan"], P["polpo_scar"]

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


def trend_emoji(data: deque[float]) -> str:
    """Trend arrow as emoji-glyph for higher visibility."""
    vals = list(data)[-TREND_WINDOW:]
    if len(vals) < 3:
        return f'[{DIM}]─[/]'
    slope = (vals[-1] - vals[0]) / len(vals)
    if slope > 4:    return f'[{HOT_PINK}]▲▲[/]'
    if slope > 1.5:  return f'[{ORANGE}]▲[/]'
    if slope < -4:   return f'[{LIME}]▼▼[/]'
    if slope < -1.5: return f'[{SOFT_GREEN}]▼[/]'
    return f'[{DIM}]●[/]'

# ── Visual primitives ──────────────────────────────────────────────────────────
BAR8  = ' ▏▎▍▌▋▊▉█'   # 9-step smooth fill
SPARK = ' ▁▂▃▄▅▆▇█'   # 9-step sparkline

HEAT_MAP = [           # (char, color) by intensity 0-7
    ('·',  DIM),   ('░', DIM),
    ('▒',  CYAN),  ('▒', TEAL),
    ('▓',  YELLOW),('▓', SCAR),
    ('█',  RED),   ('█', MAG),
]

TREND_WINDOW = 6
N_CORES = ds.E_CORES + ds.P_CORES   # 18
JARVIS_DIR = Path.home() / ".local" / "run" / "jarvis"

_VOICE_NAMES: dict[str, str] = {
    "andy":     "Andy M – Italian Male Warm",
    "bill":     "Bill – Wise, Mature, Balanced",
    "callum":   "Callum – Husky Trickster",
    "carlotta": "Carlotta – Fairy Princess",
    "daniela":  "Daniela – Giovane ed elegante",
    "george":   "George – Warm, Captivating",
    "laura":    "Laura – Enthusiast, Quirky",
    "mary":     "Mary – Confident, Roman",
    "mimmi":    "Mimmi – Playful Cartoonish",
    "roger":    "Roger – Laid-Back, Resonant",
}


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
    vals = list(data)[-w:]
    if not vals:
        return '░' * w
    mx = max(max(vals), 0.01)
    return ''.join(SPARK[min(8, int(v / mx * 8.99))] for v in vals)


def heat(v: float) -> tuple[str, str]:
    return HEAT_MAP[min(7, int(v / 100 * 8))]


def trend_arrow(data: deque[float]) -> str:
    vals = list(data)[-TREND_WINDOW:]
    if len(vals) < 3:
        return f'[{DIM}]─[/]'
    slope = (vals[-1] - vals[0]) / len(vals)
    if slope > 4:    return f'[{RED}]↑[/]'
    if slope > 1.5:  return f'[{YELLOW}]↗[/]'
    if slope < -4:   return f'[{GREEN}]↓[/]'
    if slope < -1.5: return f'[{TEAL}]↘[/]'
    return f'[{DIM}]─[/]'


def p_pct(vals: list[float], p: float) -> float:
    if not vals: return 0.0
    s = sorted(vals)
    return s[max(0, min(len(s) - 1, int(len(s) * p)))]


def gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}G"


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
        # ── Health badge + overall bar
        f"  {h_emoji} [{hc}]HEALTH[/] [bold {WHITE}]{hs:3d}[/][{DIM}]/100[/]   "
        f"[{DIM}]⚖ load[/] [bold {TEAL}]{la1:.2f}[/] [{DIM}]{la5:.2f}  {la15:.2f}[/]",
        f"  [{_c(overall)}]{bar(overall, 24)}[/]  [bold {_c(overall)}]{overall:4.1f}%[/] {trend_emoji(history)}",
        "",
        f"  [{SOFT_GREEN}]🍃 S-CORES[/]  [{DIM}]avg[/] [bold {_c(e_avg)}]{e_avg:4.1f}%[/]  [{DIM}]· 6 efficiency[/]",
    ]
    for i, v in enumerate(e_vals):
        lines.append(f"   [{SOFT_GREEN}]S{i}[/] [{_c(v)}]{bar(v, 14)}[/] [{_c(v)}]{v:3.0f}%[/]")

    lines += [
        "",
        f"  [{DEEP_PURPL}]🚀 P-CORES[/]  [{DIM}]avg[/] [bold {_c(p_avg)}]{p_avg:4.1f}%[/]  [{DIM}]· 12 performance[/]",
    ]
    for i, v in enumerate(p_vals):
        lines.append(f"   [{DEEP_PURPL}]P{i:02d}[/] [{_c(v)}]{bar(v, 14)}[/] [{_c(v)}]{v:3.0f}%[/]")

    # ── I/O footer
    lines += [
        "",
        f"  [{ELEC_BLUE}]💾 disk[/] [{CYAN}]↓{disk.get('read', 0):5.1f}[/] [{HOT_PINK}]↑{disk.get('write', 0):5.1f}[/] [{DIM}]MB/s[/]   "
        f"[{ELEC_BLUE}]🌐 net[/] [{CYAN}]↓{net.get('recv', 0):5.2f}[/] [{HOT_PINK}]↑{net.get('sent', 0):5.2f}[/] [{DIM}]MB/s[/]",
    ]
    return "\n".join(lines)


def render_mem(m: dict, history: deque[float], cpu_avg: float, load: float) -> str:
    if not m:
        return f"[{DIM}]🔄 Reading unified memory…[/]"

    total = m['total']
    prs_label, prs_key = m['pressure']
    prs_color = {'ok': LIME, 'info': ELEC_BLUE, 'warning': ORANGE, 'error': HOT_PINK}[prs_key]
    prs_emoji = {'ok': '🟢', 'info': '🔵', 'warning': '🟡', 'error': '🔴'}[prs_key]
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
        f"[{HOT_PINK}]W[/][{DIM}]{gb(m['wired'])}[/] "
        f"[{TEAL}]A[/][{DIM}]{gb(m['active'])}[/] "
        f"[{DIM}]I {gb(m['inactive'])}[/] "
        f"[{ORANGE}]Z[/][{DIM}]{gb(m['compressed'])}[/] "
        f"[{LIME}]F[/][{DIM}]{gb(m['free'])}[/]"
    )

    def seg(emoji: str, label: str, val: int, color: str) -> str:
        b = bar(val / total * 100, 14)
        return f"   {emoji} [{DIM}]{label:<10}[/] [{color}]{b}[/] [bold {color}]{gb(val):>7}[/]"

    return "\n".join([
        "",
        f"  {h_emoji} [{hc}]HEALTH[/] [bold {WHITE}]{hs:3d}[/][{DIM}]/100[/]   "
        f"{prs_emoji} [{DIM}]pressure[/] [bold {prs_color}]{prs_label}[/]  "
        f"{swap_emoji} [{DIM}]swap[/] [bold {swap_color}]{gb(m['swap'])}[/]",
        "",
        # Stacked bar — the crown jewel
        f"  {seg_bar}  [bold {_c(m['pct'])}]{m['pct']:4.1f}%[/] {trend_emoji(history)}",
        f"  [{DIM}]W=wired · A=active · I=inactive · Z=compressed · F=free[/]",
        f"  {seg_labels}",
        "",
        f"  [{ELEC_BLUE}]🧠 BREAKDOWN[/]  [{DIM}]total[/] [bold {TEAL}]{gb(total)}[/]",
        "",
        seg("🩷", "Wired",      m['wired'],      HOT_PINK),
        seg("🔷", "Active",     m['active'],     _c(m['active'] / total * 100)),
        seg("⚫", "Inactive",   m['inactive'],   DIM),
        seg("🟧", "Compressed", m['compressed'], ORANGE),
        seg("🟢", "Free",       m['free'],       LIME),
    ])


def render_heatmap(core_history: dict[int, deque[float]], cols: int = 44) -> str:
    """Temporal heatmap — M5 fingerprint with time axis."""
    tick_every = 10   # mark every 10 cols = 20 s
    total_secs = cols * 2

    # Build time axis
    axis = ' ' * 6
    for i in range(cols):
        secs_ago = (cols - i) * 2
        if secs_ago % 20 == 0 and secs_ago != 0:
            label = f'{secs_ago}s'
            axis += label + ' ' * (tick_every - len(label))
        elif i % tick_every == 0:
            axis += '│' + ' ' * (tick_every - 1)
        else:
            pass  # handled above
    # Simpler: just mark every 10 cols
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
        f"[{DIM}]░[/]<25  [{CYAN}]▒[/]25-50  [{YELLOW}]▓[/]50-75  [{HOT_PINK}]█[/]>75",
        f"[italic {DIM}]The memory of work, rendered as heat — time scrolls left, intensity blooms hot.[/]",
        "",
        f"[{DIM}]{axis_str}[/]  [{DIM}]avg[/]",
        f"  [{SOFT_GREEN}]🍃 S-CORES[/] [{DIM}](efficiency)[/]",
    ]

    for i in range(ds.E_CORES):
        vals = list(core_history.get(i, deque()))[-cols:]
        cells = ''.join(f'[{heat(v)[1]}]{heat(v)[0]}[/]' for v in vals)
        # Pad left if not enough data
        pad = cols - len(vals)
        pad_str = f'[{DIM}]{" " * pad}[/]' if pad > 0 else ''
        avg = mean(vals) if vals else 0
        lines.append(f"  [{SOFT_GREEN}]S{i}[/] {pad_str}{cells}  [bold {_c(avg)}]{avg:3.0f}%[/]")

    lines += ["", f"  [{DEEP_PURPL}]🚀 P-CORES[/] [{DIM}](performance)[/]"]

    for i in range(ds.P_CORES):
        idx  = ds.E_CORES + i
        vals = list(core_history.get(idx, deque()))[-cols:]
        cells = ''.join(f'[{heat(v)[1]}]{heat(v)[0]}[/]' for v in vals)
        pad = cols - len(vals)
        pad_str = f'[{DIM}]{" " * pad}[/]' if pad > 0 else ''
        avg = mean(vals) if vals else 0
        lines.append(f"  [{DEEP_PURPL}]P{i:02d}[/] {pad_str}{cells}  [bold {_c(avg)}]{avg:3.0f}%[/]")

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

    voice_display = vd.get("voice_full") or _VOICE_NAMES.get(voice.lower(), voice.title())
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
        text = entry["text"]
        if len(text) > 52:
            text = text[:50] + "…"
        trans_lines.append(f"  {ts_str}  [{FG}]{text}[/]")

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
    """Unified event feed — timestamped system transitions."""
    if not feed:
        return f"\n  [{DIM}]no events yet…[/]"
    lines = [""]
    for line in feed:
        lines.append(f"  {line}")
    return "\n".join(lines)


# ── Rainbow title (HSV flow, light pastel + wave luminosity modulation) ───────
RAINBOW_SAT     = 0.55   # 0=grey, 1=neon. 0.55 = leggero/pastel
RAINBOW_VAL     = 1.00   # luminosità piena di base
RAINBOW_SPREAD  = 0.07   # offset di hue tra una lettera e la successiva
RAINBOW_SPEED   = 0.008  # incremento di phase per tick → 12.5s ciclo a 10fps
RAINBOW_FPS_DT  = 0.10   # 100ms tick = 10fps (dentro polpo.tokens fps_target)

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

def _rainbow_hex(idx: int, phase: float, wave: bool = True) -> str:
    """HSV rainbow + optional wave luminosity modulation per letter.

    Hue cambia spazialmente (idx) + temporalmente (phase) → flusso arcobaleno.
    Quando wave=True, V (luminosità) oscilla con sin(2π·(idx·WAVE_FREQ - phase·WAVE_SPEED))
    creando un'onda di luce che scorre lungo il testo.
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


class TitleBar(Static):
    """Fascia titolo centrata a 5 righe:
    riga 1: emoji + titolo rainbow ad onda (centrato)
    riga 2: subtitle hardware identity (centrato)
    riga 3: rich info — sessione, uptime, # Claude, ora locale (centrato)
    riga 4: status live — bat/cpu/load/ram pressure/disk/net (centrato)
    riga 5: spacer
    """

    DEFAULT_CSS = f"""
    TitleBar {{
        height: 15;
        background: {BG};
        padding: 1 3;
        color: {FG};
        border-bottom: heavy {TEAL};
        text-align: center;
        content-align: center middle;
    }}
    """

    TITLE_TEXT = "M5 MAX WATCHER"
    EMOJI      = "🐙"

    phase:      reactive[float] = reactive(0.0)
    status:     reactive[str]   = reactive("")
    rich_info:  reactive[dict]  = reactive(dict)
    show_ascii: reactive[bool]  = reactive(True)

    def on_mount(self) -> None:
        self.set_interval(RAINBOW_FPS_DT, self._tick)
        self._repaint()

    def _tick(self) -> None:
        self.phase = (self.phase + RAINBOW_SPEED) % 1.0

    def watch_phase(self, _new: float) -> None:
        self._repaint()

    def watch_status(self, _new: str) -> None:
        self._repaint()

    def watch_rich_info(self, _new: dict) -> None:
        self._repaint()

    def watch_show_ascii(self, _new: bool) -> None:
        self._repaint()

    def _repaint(self) -> None:
        if self.show_ascii:
            ascii_rows = [
                rainbow_text(row, (self.phase + i * 0.12) % 1.0, wave=True)
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
        sep = f"  [{DIM}]┃[/]  "
        if cols >= 100:
            line3 = (
                f"🎯 [{DIM}]sess[/] [bold {WHITE}]{sess}[/]"
                f"{sep}⏱ [{DIM}]up[/] [bold {ELEC_BLUE}]{uptime}[/]"
                f"{sep}🐙 [bold {HOT_PINK}]×{claude_n}[/]"
                f"{sep}🔌 [bold {SOFT_GREEN}]×{mcp_n}[/]"
                f"{sep}🕐 [bold {TEAL}]{time_str}[/]"
            )
        elif cols >= 80:
            line3 = (
                f"[{DIM}]sess[/] [bold {WHITE}]{sess}[/]  "
                f"🐙[bold {HOT_PINK}]×{claude_n}[/] 🔌[bold {SOFT_GREEN}]×{mcp_n}[/]  "
                f"[bold {TEAL}]{time_str}[/]"
            )
        else:
            line3 = (
                f"[bold {WHITE}]{sess}[/]  "
                f"[bold {HOT_PINK}]×{claude_n}[/]  "
                f"[{TEAL}]{time_str}[/]"
            )

        line4 = self.status if self.status else f"[{DIM}]🔄 Probing system…[/]"
        self.update(f"{ascii_banner}{line1}\n{line2}\n{line3}\n{line4}")


# ── Triage Screen ─────────────────────────────────────────────────────────────
class TriageScreen(ModalScreen):
    """Overlay advisor: classifica processi Polpo in KILL_SAFE / CAUTIOUS / KEEP."""

    DEFAULT_CSS = """
    TriageScreen {
        align: center middle;
    }
    #triage-outer {
        width: 96%;
        height: 88%;
        border: thick $accent;
        background: $surface;
        padding: 1 3;
    }
    #triage-title {
        text-align: center;
        margin-bottom: 1;
    }
    #triage-hint {
        height: 1;
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape,q", "dismiss",      "Chiudi",   show=True),
        Binding("k",        "kill_selected","Kill",     show=True),
        Binding("r",        "do_refresh",   "Refresh",  show=True),
    ]

    _BUCKET_COLOR = {
        ds.BUCKET_SAFE:     "bold red",
        ds.BUCKET_CAUTIOUS: "bold yellow",
        ds.BUCKET_KEEP:     "bold green",
    }
    _BUCKET_SHORT = {
        ds.BUCKET_SAFE:     "SAFE",
        ds.BUCKET_CAUTIOUS: "CAUTIOUS",
        ds.BUCKET_KEEP:     "KEEP",
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
            f"[bold]🔴 SAFE ×{n_safe}[/]   "
            f"[bold yellow]🟡 CAUTIOUS ×{n_caut}[/]   "
            f"[bold green]🟢 KEEP ×{n_keep}[/]   "
            f"[dim]· {len(procs)} proc Polpo[/]"
        )
        self.query_one("#triage-hint").update(
            "[dim]k[/] kill  ·  [dim]r[/] refresh  ·  [dim]esc[/] chiudi  "
            "·  SAFE = orfani/zombie  ·  CAUTIOUS = attenzione  ·  KEEP = non toccare"
        )

        for proc in procs:
            color = self._BUCKET_COLOR.get(proc['bucket'], 'dim')
            short = self._BUCKET_SHORT.get(proc['bucket'], proc['bucket'])
            t.add_row(
                f"[{color}]{short}[/]",
                str(proc['pid']),
                proc['label'][:24],
                f"{proc['mem_mb']:.0f}",
                f"{proc['cpu']:.1f}",
                proc['reason'][:56],
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
        border: heavy {TEAL};
        border-title-color: {TEAL};
        border-title-style: bold;
        padding: 1 3;
        height: 1fr;
        overflow-y: auto;
    }}
    #graph-static {{
        width: 1fr;
    }}
    #top-row {{
        height: auto;
        min-height: 16;
        max-height: 44;
    }}
    #cpu-panel, #mem-panel {{
        width: 1fr;
        border: heavy {TEAL};
        padding: 1 3;
        margin: 0;
        background: {BG_ALT};
    }}
    #cpu-panel {{
        border-title-color: {CYAN};
        border-title-style: bold;
    }}
    #mem-panel {{
        border-title-color: {MAG};
        border-title-style: bold;
        height: 1fr;
    }}
    #mem-col {{
        width: 1fr;
        layout: vertical;
    }}
    #feed-panel {{
        height: 3fr;
        padding: 1 3;
        margin: 0;
        background: {BG_ALT};
        border: heavy {ORANGE};
        border-title-color: {ORANGE};
        border-title-style: bold;
        overflow-y: auto;
    }}
    #tab-area {{
        height: 1fr;
        min-height: 20;
    }}
    TabbedContent {{
        background: {BG};
    }}
    ContentSwitcher {{
        border-top: heavy {TEAL};
    }}
    Tabs {{
        background: {BG};
        height: 3;
        width: 100%;
        margin: 0;
        padding: 0;
    }}
    Tab {{
        color: {DIM};
        background: {BG};
        padding: 1 3;
        height: 1fr;
    }}
    Tab:hover {{
        color: {FG};
        background: {BG_ALT};
    }}
    Tab.-active {{
        color: {TEAL};
        background: {BG_ALT};
        text-style: bold;
    }}
    Tab.-active:hover {{
        color: {TEAL};
        background: {BG_ALT};
    }}
    TabPane {{
        background: {BG};
        padding: 0;
    }}
    #heat-row {{
        height: 1fr;
    }}
    #heat-static {{
        width: 53%;
        background: {BG_ALT};
        border: heavy {TEAL};
        padding: 1 3;
        height: 1fr;
        overflow: hidden hidden;
    }}
    #analytics-static {{
        background: {BG_ALT};
        border: heavy {TEAL};
        padding: 1 3;
        height: 1fr;
    }}
    #voice-static {{
        width: 47%;
        background: {BG_ALT};
        border: heavy {HOT_PINK};
        border-title-color: {HOT_PINK};
        padding: 1 3;
        height: 1fr;
    }}
    DataTable {{
        background: {BG_ALT};
        height: 1fr;
    }}
    DataTable > .datatable--header {{
        background: {BG};
        color: {TEAL};
        text-style: bold;
    }}
    DataTable > .datatable--cursor {{
        background: {TEAL};
        color: {BG};
    }}
    DataTable > .datatable--even-row {{
        background: {BG};
    }}
    #kpi-static {{
        background: {BG_ALT};
        border: heavy {LIME};
        border-title-color: {LIME};
        padding: 1 3;
        height: 1fr;
    }}
    #logs-scroll {{
        background: {BG_ALT};
        border: heavy {ORANGE};
        border-title-color: {ORANGE};
        padding: 1 3;
        height: 1fr;
    }}
    #logs-header {{
        padding: 0 0 1 0;
    }}
    #log-table {{
        background: {BG_ALT};
        height: 1fr;
    }}
    #procs-box {{
        background: {BG_ALT};
        border: heavy {ELEC_BLUE};
        padding: 1 3;
        height: 1fr;
        overflow: hidden hidden;
    }}
    #tent-box {{
        background: {BG_ALT};
        border: heavy {HOT_PINK};
        padding: 1 3;
        height: 1fr;
    }}
    #focus-static {{
        height: auto;
        max-height: 18;
        margin-bottom: 1;
    }}
    #tent-table {{
        height: auto;
    }}
    """

    BINDINGS = [
        Binding("q",   "quit",           "Quit"),
        Binding("r",   "force_refresh",  "Refresh"),
        Binding("p",   "toggle_pause",   "Pause"),
        Binding("1",   "show_tab_heat",  "Heatmap",   show=False),
        Binding("2",   "show_tab_stats", "Analytics", show=False),
        Binding("3",   "show_tab_procs", "Processes", show=False),
        Binding("4",   "show_tab_tent",  "Tentacoli", show=False),
        Binding("5",   "show_tab_graph", "Graph",     show=False),
        Binding("6",   "show_tab_kpi",   "KPI",       show=False),
        Binding("7",   "show_tab_logs",  "Logs",      show=False),
        Binding("f",   "cycle_graph_filter", "Filter",  show=False),
        Binding("c",   "triage",             "Triage",  show=True),
        Binding("k",   "kill_tent_selected", "🗑 Kill",  show=False),
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
        self._focus_data:   dict                       = {}
        # Unified feed — state transition log (cross-clock: fast=CPU/voice, slow=mem/KPI)
        self._event_feed:      deque[str]              = deque(maxlen=15)
        self._prev_pressure:   str                     = ''
        self._prev_swap_active: bool                   = False
        self._cpu_spike_ticks:  int                    = 0
        self._prev_voice_state: str                    = ''
        self._prev_mrr:         float                  = 0.0
        self._prev_pipeline:    float                  = 0.0
        # Responsive layout — updated on terminal resize
        self._cols: int = 120
        self._rows: int = 40

    # ── Layout ────────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield TitleBar(id="title-bar")
        with TabbedContent(id="tab-area"):
            with TabPane("🌡 Heatmap", id="tab-heat"):
                with Horizontal(id="heat-row"):
                    yield Static(f"[{DIM}]Accumulating core samples…[/]", id="heat-static")
                    yield Static(f"[{DIM}]🔄 Loading voice…[/]", id="voice-static")
            with TabPane("📈 Analytics", id="tab-stats"):
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
                        render_focus({}),
                        id="focus-static")
                    yield Static(
                        f"\n[bold {HOT_PINK}]🐙 POLPO TENTACOLI[/]  [{DIM}]· background workers[/]\n"
                        f"[italic {DIM}]The autonomic nervous system of the Polpo — Claude, MCP, daemons, watchdogs, alive.[/]",
                        id="tent-header")
                    yield DataTable(id="tent-table", cursor_type="row", zebra_stripes=True)
            with TabPane("🕸 Graph", id="tab-graph"):
                with ScrollableContainer(id="graph-scroll"):
                    yield Static(
                        f"[{DIM}]🔄 Parsing vault Obsidian…[/]",
                        id="graph-static")
            with TabPane("📊 KPI", id="tab-kpi"):
                yield Static(f"[{DIM}]🔄 Leggendo KPI.md dal vault…[/]", id="kpi-static")
            with TabPane("📋 Logs", id="tab-logs"):
                with ScrollableContainer(id="logs-scroll"):
                    yield Static(
                        f"[bold #ff8a3d]📋 ACTIVITY STREAM[/]  [{DIM}]· cross-system log cascade[/]\n"
                        f"[italic {DIM}]Every signal from every tentacolo — leads, payments, calls, voice, security.[/]",
                        id="logs-header")
                    yield DataTable(id="log-table", cursor_type="row", zebra_stripes=True)
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
        # 53% widget, border=2, padding=6, row overhead "  P00 " (6) + "  100%" (6) = 14 + 6 margins
        return max(20, int(self._cols * 0.53) - 20)

    def _spark_width(self) -> int:
        """Sparkline width for analytics tab — full-width panel minus labels."""
        return max(20, self._cols - 24)

    def _voice_width(self) -> int:
        """Level bar width for voice panel — 47% panel minus padding/prefix."""
        return max(20, int(self._cols * 0.45) - 12)

    def _center_tabs(self) -> None:
        pass

    def on_resize(self, event: events.Resize) -> None:
        """Capture terminal size and adapt layout + panels immediately."""
        self._cols = event.size.width
        self._rows = event.size.height
        # Shrink top-row on small terminals so tab area keeps breathing room
        new_min = max(14, min(20, self._rows * 40 // 100))
        top_row = self.query_one("#top-row")
        top_row.styles.min_height = new_min
        top_row.styles.max_height = max(new_min + 24, 44)
        # Compact TitleBar: full=15 (ASCII banner visible), normal=8, mini=6
        title_bar = self.query_one("#title-bar", TitleBar)
        show = self._rows >= 35 and self._cols >= 52
        title_bar.show_ascii = show
        title_bar.styles.height = 6 if self._rows < 35 else (15 if show else 8)
        # Immediately re-render width-sensitive panels
        _heat_text = RichText.from_markup(render_heatmap(self._core_history, cols=self._heatmap_cols()))
        _heat_text.no_wrap = True
        self.query_one("#heat-static", Static).update(_heat_text)
        self._center_tabs()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        fullscreen_tabs = {"tab-graph", "tab-logs"}
        hide = event.pane is not None and event.pane.id in fullscreen_tabs
        self.query_one("#top-row").display = not hide

    async def on_mount(self) -> None:
        self._init_tables()
        self._center_tabs()
        await asyncio.to_thread(psutil.cpu_percent, percpu=True, interval=None)
        # Seed disk/net deltas
        await asyncio.to_thread(ds.disk_io_rate)
        await asyncio.to_thread(ds.net_io_rate)
        self.set_interval(2.0, self._refresh_fast)
        self.set_interval(5.0, self._refresh_slow)
        # Immediate initial load — all panels (incl. Graph + KPI) visible on startup
        await self._refresh_fast()
        await self._refresh_slow()

    def _init_tables(self) -> None:
        pt = self.query_one("#proc-table", DataTable)
        pt.add_columns("PID", "Process", "CPU %", "RAM MB")
        tt = self.query_one("#tent-table", DataTable)
        tt.add_columns(" ", "Process", "PID", "CPU %", "RAM MB", "Command", "🗑")
        lt = self.query_one("#log-table", DataTable)
        lt.add_columns("Time", " ", "Event", "Source", "Detail")

    # ── Refresh ───────────────────────────────────────────────────────────────
    async def _refresh_fast(self) -> None:
        if self._paused:
            return
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
        else:
            self._cpu_spike_ticks = 0

        mem_now = self._mem.get('pct', 0)
        la1, _, _ = ds.load_avg()

        self.query_one("#cpu-content",   Static).update(
            f"[bold {ELEC_BLUE}]⚡ CPU[/]  [{DIM}]· M5 Max 18C[/]\n" +
            render_cpu(self._cpu_percents, self._cpu_history, self._disk, self._net)
        )
        _heat_text = RichText.from_markup(render_heatmap(self._core_history, cols=self._heatmap_cols()))
        _heat_text.no_wrap = True
        self.query_one("#heat-static", Static).update(_heat_text)
        self.query_one("#analytics-static", Static).update(
            render_analytics(self._cpu_history, self._mem_history,
                             self._core_history, overall, mem_now, la1,
                             spark_w=self._spark_width())
        )
        vd = await asyncio.to_thread(voice_data)
        self.query_one("#voice-static", Static).update(
            render_voice(vd, level_w=self._voice_width())
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
        # ── Feed widget: single update per fast-cycle (CPU spike appended above)
        self.query_one("#feed-static", Static).update(_UNIFEED_HDR + render_feed(self._event_feed))
        self._update_subtitle(overall, la1)

    async def _refresh_slow(self) -> None:
        if self._paused:
            return
        (self._mem, self._bat, self._proc_counts, self._graph_data,
         self._kpi_data, self._focus_data, self._log_entries) = await asyncio.gather(
            asyncio.to_thread(ds.unified_memory),
            asyncio.to_thread(ds.battery),
            asyncio.to_thread(_count_claude_mcp),
            asyncio.to_thread(vault_parser.vault_graph_data),
            asyncio.to_thread(kpi_widget.read_kpi_data),
            asyncio.to_thread(ds.current_focus),
            asyncio.to_thread(ds.log_feed),
        )
        self._mem_history.append(self._mem.get('pct', 0))

        # ── Feed: detect memory pressure transitions
        ts = time.strftime("%H:%M:%S")
        new_pressure = self._mem.get('pressure', ('', 'ok'))[1]
        if self._prev_pressure and new_pressure != self._prev_pressure:
            _pcolor  = {'ok': LIME, 'info': ELEC_BLUE, 'warning': ORANGE, 'error': HOT_PINK}
            _pemoji  = {'ok': '🟢', 'info': '🔵', 'warning': '🟡', 'error': '🔴'}
            _plabel  = {'ok': 'NORMAL', 'info': 'MODERATE', 'warning': 'HIGH', 'error': 'CRITICAL'}
            c = _pcolor.get(new_pressure, DIM)
            e = _pemoji.get(new_pressure, '⚫')
            self._event_feed.appendleft(
                f"[{DIM}]{ts}[/] {e} [{c}]pressure[/] "
                f"[{DIM}]{_plabel.get(self._prev_pressure, self._prev_pressure)}"
                f" → {_plabel.get(new_pressure, new_pressure)}[/]"
            )
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

        self.query_one("#feed-static", Static).update(
            _UNIFEED_HDR + render_feed(self._event_feed)
        )

        cpu_avg = mean(self._cpu_percents) if self._cpu_percents else 0
        la1, _, _ = ds.load_avg()
        _mem_total_gb = self._mem.get('total', 0) / 1024 ** 3
        _mem_gb_str = f"{_mem_total_gb:.0f}GB" if _mem_total_gb > 0 else "—GB"
        self.query_one("#mem-content", Static).update(
            f"[bold {LIME}]🧠 UNIFIED MEMORY[/]  [{DIM}]· {_mem_gb_str}[/]\n" +
            render_mem(self._mem, self._mem_history, cpu_avg, la1)
        )
        await self._update_processes()
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
        self.query_one("#kpi-static", Static).update(
            kpi_widget.render_kpi(self._kpi_data)
        )
        self._render_logs(self._log_entries)

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
        for e in entries:
            src_col = _SRC_COLOR.get(e['source'], DIM)
            # NEW badge: teal dot for entries from a source active in the last 5 min
            new_badge = f"[bold {TEAL}]●[/]" if e.get('is_new') else f"[{DIM}]·[/]"
            lt.add_row(
                f"[{DIM}]{e['ts']}[/]",
                f"{new_badge} {e['emoji']}",
                e['title'][:36],
                f"[{src_col}]{e['source']}[/]",
                f"[{DIM}]{e['desc'][:60]}[/]",
            )

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
        pc   = {'ok': LIME, 'info': ELEC_BLUE, 'warning': ORANGE, 'error': HOT_PINK}.get(prs[1], DIM)
        prs_emoji = {'ok': '🟢', 'info': '🔵', 'warning': '🟡', 'error': '🔴'}.get(prs[1], '⚪')
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
        rich = {
            'session':      self._sess_n,
            'uptime':       _format_uptime(uptime_s),
            'claude_count': self._proc_counts.get('claude', 0),
            'mcp_count':    self._proc_counts.get('mcp', 0),
            'time':         time.strftime("%H:%M:%S"),
            'cols':         self._cols,
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

    def action_triage(self) -> None:
        self.push_screen(TriageScreen())

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


if __name__ == "__main__":
    M5Watcher().run()


# ── End of file ───────────────────────────────────────────────────────────────
# {__title__} v{__version__} — {__codename__}
# Forged in {__forged_in__} · 2026-05-02 · {__company__}
# {__copyright__}
# ──────────────────────────────────────────────────────────────────────────────
