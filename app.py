"""🐙 M5 MAX WATCHER — Apple M5 Max · Visual Analytics TUI (Textual · Polpo DS)"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from colorsys import hsv_to_rgb
from pathlib import Path
from statistics import mean, median

import psutil
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Static, TabbedContent, TabPane

import data_sources as ds

# ── Design tokens ──────────────────────────────────────────────────────────────
_TOKENS = json.loads((Path(__file__).parent / "polpo.tokens.json").read_text())
P = _TOKENS["palette"]
BG, BG_ALT           = P["polpo_bg"], P["polpo_bg_alt"]
TEAL, DIM            = P["polpo_teal"], P["polpo_dim"]
GREEN, YELLOW, RED   = P["polpo_green"], P["polpo_yellow"], P["polpo_red"]
FG, MAG, CYAN, SCAR  = P["polpo_fg"], P["polpo_magenta"], P["polpo_cyan"], P["polpo_scar"]

# ── Visual primitives ──────────────────────────────────────────────────────────
BAR8  = ' ▏▎▍▌▋▊▉█'   # 9-step smooth fill
SPARK = ' ▁▂▃▄▅▆▇█'   # 9-step sparkline

HEAT_MAP = [           # (char, color) by intensity 0-7
    (' ',  DIM),   ('░', DIM),
    ('▒',  CYAN),  ('▒', TEAL),
    ('▓',  YELLOW),('▓', SCAR),
    ('█',  RED),   ('█', MAG),
]

TREND_WINDOW = 6
N_CORES = ds.E_CORES + ds.P_CORES   # 18


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
        return f"[{DIM}]Probing 18 cores…[/]"

    e_vals = percents[:ds.E_CORES]
    p_vals = percents[ds.E_CORES: ds.E_CORES + ds.P_CORES]
    e_avg  = mean(e_vals)
    p_avg  = mean(p_vals)
    la1, la5, la15 = ds.load_avg()
    overall = mean(percents)
    hs, hc  = health_score(overall, 0, la1)   # mem not available here, pass 0

    lines = [
        # ── Health badge + overall bar
        f"  [{hc}]● HEALTH {hs:3d}/100[/]   [{DIM}]Load[/] [{TEAL}]{la1:.2f}[/] [{DIM}]{la5:.2f}  {la15:.2f}[/]",
        f"  [{_c(overall)}]{bar(overall, 24)}[/]  [{_c(overall)}]{overall:4.1f}%[/] {trend_arrow(history)}",
        "",
        f"  [{DIM}]━━ S-CORES  avg[/{DIM}] [{_c(e_avg)}]{e_avg:4.1f}%[/]  [{DIM}](6 efficiency)[/]",
    ]
    for i, v in enumerate(e_vals):
        lines.append(f"   [{DIM}]S{i}[/] [{_c(v)}]{bar(v, 14)}[/] {v:3.0f}%")

    lines += [
        "",
        f"  [{DIM}]━━ P-CORES  avg[/{DIM}] [{_c(p_avg)}]{p_avg:4.1f}%[/]  [{DIM}](12 performance)[/]",
    ]
    for i, v in enumerate(p_vals):
        lines.append(f"   [{DIM}]P{i:02d}[/] [{_c(v)}]{bar(v, 14)}[/] {v:3.0f}%")

    # ── I/O footer
    lines += [
        "",
        f"  [{DIM}]disk[/] [{CYAN}]↓{disk.get('read', 0):5.1f}[/][{DIM}] ↑[/][{MAG}]{disk.get('write', 0):5.1f} MB/s[/]   "
        f"[{DIM}]net[/] [{CYAN}]↓{net.get('recv', 0):5.2f}[/][{DIM}] ↑[/][{MAG}]{net.get('sent', 0):5.2f} MB/s[/]",
    ]
    return "\n".join(lines)


def render_mem(m: dict, history: deque[float], cpu_avg: float, load: float) -> str:
    if not m:
        return f"[{DIM}]Reading unified memory…[/]"

    total = m['total']
    prs_label, prs_key = m['pressure']
    prs_color = {'ok': GREEN, 'info': TEAL, 'warning': YELLOW, 'error': RED}[prs_key]
    hs, hc = health_score(cpu_avg, m['pct'], load)

    # Stacked proportional bar
    seg_bar = stacked_bar([
        (m['wired'],      MAG),
        (m['active'],     _c(m['active'] / total * 100)),
        (m['inactive'],   DIM),
        (m['compressed'], YELLOW),
        (m['free'],       GREEN),
    ], total, w=38)

    seg_labels = (
        f"[{MAG}]W[/][{DIM}]{gb(m['wired'])}[/] "
        f"[{TEAL}]A[/][{DIM}]{gb(m['active'])}[/] "
        f"[{DIM}]I[/][{DIM}]{gb(m['inactive'])}[/] "
        f"[{YELLOW}]Z[/][{DIM}]{gb(m['compressed'])}[/] "
        f"[{GREEN}]F[/][{DIM}]{gb(m['free'])}[/]"
    )

    def seg(label: str, val: int, color: str) -> str:
        b = bar(val / total * 100, 14)
        return f"   [{DIM}]{label:<11}[/] [{color}]{b}[/] [{DIM}]{gb(val):>7}[/]"

    return "\n".join([
        f"  [{hc}]● HEALTH {hs:3d}/100[/]   [{DIM}]Pressure[/] [{prs_color}]{prs_label}[/]  [{DIM}]Swap[/] [{CYAN}]{gb(m['swap'])}[/]",
        "",
        # Stacked bar — the crown jewel
        f"  {seg_bar}  [{_c(m['pct'])}]{m['pct']:4.1f}%[/] {trend_arrow(history)}",
        f"  [{DIM}]W=wired  A=active  I=inactive  Z=compressed  F=free[/]",
        f"  {seg_labels}",
        "",
        f"  [{DIM}]━━ BREAKDOWN[/]  [{DIM}]Total[/] [{TEAL}]{gb(total)}[/]",
        seg("Wired",      m['wired'],      MAG),
        seg("Active",     m['active'],     _c(m['active'] / total * 100)),
        seg("Inactive",   m['inactive'],   DIM),
        seg("Compressed", m['compressed'], YELLOW),
        seg("Free",       m['free'],       GREEN),
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
        f"[bold {TEAL}]CPU HEATMAP[/]  [{DIM}]Δt=2s  window={total_secs}s[/]  "
        f"[{DIM}]░[/]<25  [{CYAN}]▒[/]25-50  [{YELLOW}]▓[/]50-75  [{RED}]█[/]>75",
        f"[{DIM}]{axis_str}[/]  [{DIM}]avg[/]",
        f"  [{DIM}]S-CORES (efficiency)[/]",
    ]

    for i in range(ds.E_CORES):
        vals = list(core_history.get(i, deque()))[-cols:]
        cells = ''.join(f'[{heat(v)[1]}]{heat(v)[0]}[/]' for v in vals)
        # Pad left if not enough data
        pad = cols - len(vals)
        pad_str = f'[{DIM}]{" " * pad}[/]' if pad > 0 else ''
        avg = mean(vals) if vals else 0
        lines.append(f"  [{DIM}]S{i}[/] {pad_str}{cells}  [{_c(avg)}]{avg:3.0f}%[/]")

    lines += ["", f"  [{DIM}]P-CORES (performance)[/]"]

    for i in range(ds.P_CORES):
        idx  = ds.E_CORES + i
        vals = list(core_history.get(idx, deque()))[-cols:]
        cells = ''.join(f'[{heat(v)[1]}]{heat(v)[0]}[/]' for v in vals)
        pad = cols - len(vals)
        pad_str = f'[{DIM}]{" " * pad}[/]' if pad > 0 else ''
        avg = mean(vals) if vals else 0
        lines.append(f"  [{DIM}]P{i:02d}[/] {pad_str}{cells}  [{_c(avg)}]{avg:3.0f}%[/]")

    return "\n".join(lines)


def render_analytics(cpu_h: deque[float], mem_h: deque[float],
                     core_h: dict[int, deque[float]],
                     cpu_now: float, mem_now: float, load: float) -> str:

    def stat_row(label: str, data: deque[float], unit: str = '%') -> str:
        vals = list(data)
        if not vals:
            return f"   [{DIM}]{label:<16}[/]  [{DIM}]—[/]"
        mn = min(vals); mx = max(vals); avg = mean(vals)
        p95 = p_pct(vals, 0.95);  arr = trend_arrow(data)
        col = _c(avg)
        return (
            f"   [{DIM}]{label:<16}[/]"
            f" [{GREEN}]{mn:5.1f}{unit}[/]"
            f" [{col}]{avg:5.1f}{unit}[/]"
            f" [{YELLOW}]{p95:5.1f}{unit}[/]"
            f" [{RED}]{mx:5.1f}{unit}[/]"
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

    # P/S efficiency ratio
    ratio_txt = '—'
    ratio_bar = ''
    if e_agg and p_agg:
        ratio = mean(p_agg[-10:]) / max(mean(e_agg[-10:]), 0.1)
        ratio_bar = bar(min(ratio * 20, 100), 18)
        note = 'P heavy' if ratio > 3 else 'balanced' if ratio > 0.8 else 'S dominant'
        ratio_txt = f"[{TEAL}]{ratio:.2f}×[/] [{DIM}]({note})[/]  [{TEAL}]{ratio_bar}[/]"

    lines = [
        f"[bold {TEAL}]SYSTEM ANALYTICS[/]  [{DIM}]{len(cpu_h)} samples · {len(cpu_h)*2}s window[/]",
        "",
        f"  [{hc}]{hs_bar}[/]  [{hc}]HEALTH {hs}/100[/]",
        "",
        f"  [{DIM}]━━ STATISTICS   min    avg    p95    max    trend[/]",
        stat_row("Overall CPU",     cpu_h),
    ]
    if e_dq: lines.append(stat_row("S-cluster (6E)",  e_dq))
    if p_dq: lines.append(stat_row("P-cluster (12P)", p_dq))
    lines.append(stat_row("RAM used",        mem_h))
    lines += [
        "",
        f"  [{DIM}]━━ P/S EFFICIENCY RATIO[/]",
        f"   {ratio_txt}",
        "",
        f"  [{DIM}]━━ 2-MIN TIMELINE[/]",
        f"  [{_c(cpu_now)}]{sparkline(cpu_h, 56)}[/]  [{DIM}]cpu[/]",
        f"  [{_c(mem_now)}]{sparkline(mem_h, 56)}[/]  [{DIM}]ram[/]",
    ]
    return "\n".join(lines)


# ── Rainbow title (HSV flow, light pastel) ─────────────────────────────────────
RAINBOW_SAT     = 0.55   # 0=grey, 1=neon. 0.55 = leggero/pastel
RAINBOW_VAL     = 1.00   # luminosità piena (uniforme su tutte le lettere)
RAINBOW_SPREAD  = 0.07   # offset di hue tra una lettera e la successiva
RAINBOW_SPEED   = 0.008  # incremento di phase per tick → 12.5s ciclo a 10fps
RAINBOW_FPS_DT  = 0.10   # 100ms tick = 10fps (dentro polpo.tokens fps_target)


def _rainbow_hex(idx: int, phase: float) -> str:
    h = (idx * RAINBOW_SPREAD + phase) % 1.0
    r, g, b = hsv_to_rgb(h, RAINBOW_SAT, RAINBOW_VAL)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def rainbow_text(text: str, phase: float) -> str:
    out = []
    for i, ch in enumerate(text):
        if ch.isspace():
            out.append(ch)
        else:
            out.append(f"[{_rainbow_hex(i, phase)}]{ch}[/]")
    return ''.join(out)


class TitleBar(Static):
    """Fascia titolo a 3 righe — emoji statica + titolo arcobaleno animato + status."""

    DEFAULT_CSS = f"""
    TitleBar {{
        height: 5;
        background: {BG};
        padding: 1 3;
        color: {FG};
        border-bottom: heavy {TEAL};
    }}
    """

    TITLE_TEXT = "M5 MAX WATCHER"
    EMOJI      = "🐙"

    phase:  reactive[float] = reactive(0.0)
    status: reactive[str]   = reactive("")

    def on_mount(self) -> None:
        self.set_interval(RAINBOW_FPS_DT, self._tick)
        self._render()

    def _tick(self) -> None:
        self.phase = (self.phase + RAINBOW_SPEED) % 1.0

    def watch_phase(self, _new: float) -> None:
        self._render()

    def watch_status(self, _new: str) -> None:
        self._render()

    def _render(self) -> None:
        rainbow = rainbow_text(self.TITLE_TEXT, self.phase)
        line1 = f"{self.EMOJI}  [bold]{rainbow}[/]"
        line2 = self.status if self.status else f"[{DIM}]Probing system…[/]"
        self.update(f"{line1}\n\n{line2}")


# ── App ────────────────────────────────────────────────────────────────────────
class M5Watcher(App):
    TITLE     = "🐙 M5 MAX WATCHER"
    SUB_TITLE = "Apple M5 Max · 18C (6S+12P) · 36GB Unified Memory"

    CSS = f"""
    Screen {{
        background: {BG};
        color: {FG};
    }}
    #top-row {{
        height: auto;
        min-height: 26;
        max-height: 34;
    }}
    #cpu-panel, #mem-panel {{
        width: 1fr;
        border: heavy {TEAL};
        padding: 1 1;
        background: {BG_ALT};
    }}
    #tab-area {{
        height: 1fr;
        min-height: 20;
    }}
    TabbedContent {{
        background: {BG};
    }}
    TabPane {{
        background: {BG};
        padding: 1 1;
    }}
    #heat-static, #analytics-static {{
        background: {BG_ALT};
        border: heavy {DIM};
        padding: 1 2;
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
    """

    BINDINGS = [
        Binding("q",   "quit",           "Quit"),
        Binding("r",   "force_refresh",  "Refresh"),
        Binding("p",   "toggle_pause",   "Pause"),
        Binding("1",   "show_tab_heat",  "Heatmap",   show=False),
        Binding("2",   "show_tab_stats", "Analytics", show=False),
        Binding("3",   "show_tab_procs", "Processes", show=False),
        Binding("4",   "show_tab_tent",  "Tentacoli", show=False),
    ]

    def __init__(self):
        super().__init__()
        self._cpu_percents: list[float]               = []
        self._mem:          dict                       = {}
        self._bat:          dict                       = {}
        self._disk:         dict                       = {}
        self._net:          dict                       = {}
        self._cpu_history:  deque[float]               = deque(maxlen=60)
        self._mem_history:  deque[float]               = deque(maxlen=60)
        self._core_history: dict[int, deque[float]]    = {
            i: deque(maxlen=60) for i in range(N_CORES)
        }
        self._paused = False
        self._tick   = 0

    # ── Layout ────────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield TitleBar(id="title-bar")
        with TabbedContent(id="tab-area"):
            with TabPane("🌡 Heatmap", id="tab-heat"):
                yield Static(f"[{DIM}]Accumulating core samples…[/]", id="heat-static")
            with TabPane("📈 Analytics", id="tab-stats"):
                yield Static(f"[{DIM}]Building statistics…[/]", id="analytics-static")
            with TabPane("🔝 Processes", id="tab-procs"):
                yield DataTable(id="proc-table", cursor_type="row", zebra_stripes=True)
            with TabPane("🐙 Tentacoli", id="tab-tent"):
                yield DataTable(id="tent-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="top-row"):
            with ScrollableContainer(id="cpu-panel"):
                yield Static(f"[bold {TEAL}]CPU — M5 Max 18C[/]\n[{DIM}]Probing…[/]",
                             id="cpu-content")
            with ScrollableContainer(id="mem-panel"):
                yield Static(f"[bold {TEAL}]UNIFIED MEMORY 36GB[/]\n[{DIM}]Reading…[/]",
                             id="mem-content")
        yield Footer()

    async def on_mount(self) -> None:
        self._init_tables()
        await asyncio.to_thread(psutil.cpu_percent, percpu=True, interval=None)
        # Seed disk/net deltas
        await asyncio.to_thread(ds.disk_io_rate)
        await asyncio.to_thread(ds.net_io_rate)
        self.set_interval(2.0, self._refresh_fast)
        self.set_interval(5.0, self._refresh_slow)

    def _init_tables(self) -> None:
        pt = self.query_one("#proc-table", DataTable)
        pt.add_columns("PID", "Process", "CPU %", "RAM MB")
        tt = self.query_one("#tent-table", DataTable)
        tt.add_columns(" ", "Process", "PID", "CPU %", "RAM MB", "Command")

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

        mem_now = self._mem.get('pct', 0)
        la1, _, _ = ds.load_avg()

        self.query_one("#cpu-content",   Static).update(
            f"[bold {TEAL}]CPU — M5 Max 18C[/]\n" +
            render_cpu(self._cpu_percents, self._cpu_history, self._disk, self._net)
        )
        self.query_one("#heat-static",   Static).update(
            render_heatmap(self._core_history)
        )
        self.query_one("#analytics-static", Static).update(
            render_analytics(self._cpu_history, self._mem_history,
                             self._core_history, overall, mem_now, la1)
        )
        self._update_subtitle(overall, la1)

    async def _refresh_slow(self) -> None:
        if self._paused:
            return
        self._mem, self._bat = await asyncio.gather(
            asyncio.to_thread(ds.unified_memory),
            asyncio.to_thread(ds.battery),
        )
        self._mem_history.append(self._mem.get('pct', 0))

        cpu_avg = mean(self._cpu_percents) if self._cpu_percents else 0
        la1, _, _ = ds.load_avg()
        self.query_one("#mem-content", Static).update(
            f"[bold {TEAL}]UNIFIED MEMORY 36GB[/]\n" +
            render_mem(self._mem, self._mem_history, cpu_avg, la1)
        )
        await self._update_processes()

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
                       t['cmd'])

    def _update_subtitle(self, cpu: float, load: float) -> None:
        bat  = self._bat
        pct  = bat.get('pct', 100)
        icon = '⚡' if bat.get('charging') else ('🔋' if pct > 20 else '🪫')
        prs  = self._mem.get('pressure', ('—', 'ok'))
        pc   = {'ok': GREEN, 'info': TEAL, 'warning': YELLOW, 'error': RED}.get(prs[1], DIM)
        d, n = self._disk, self._net
        live = f'[{GREEN}]●[/]' if not self._paused else f'[{YELLOW}]‖[/]'
        status = (
            f"{live} bat {pct}%{icon}  "
            f"cpu {cpu:.0f}%  load {load:.1f}  "
            f"ram [{pc}]{prs[0]}[/]  "
            f"disk ↓{d.get('read', 0):.1f} ↑{d.get('write', 0):.1f}  "
            f"net ↓{n.get('recv', 0):.2f} ↑{n.get('sent', 0):.2f} MB/s"
        )
        self.query_one("#title-bar", TitleBar).status = status

    # ── Actions ───────────────────────────────────────────────────────────────
    async def action_force_refresh(self) -> None:
        await self._refresh_fast()
        await self._refresh_slow()
        self.notify("Refreshed", severity="information", timeout=1)

    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        self.notify(
            f"[{YELLOW}]PAUSED[/]" if self._paused else f"[{GREEN}]LIVE[/]",
            timeout=1.5
        )

    def action_show_tab_heat(self)  -> None: self.query_one(TabbedContent).active = "tab-heat"
    def action_show_tab_stats(self) -> None: self.query_one(TabbedContent).active = "tab-stats"
    def action_show_tab_procs(self) -> None: self.query_one(TabbedContent).active = "tab-procs"
    def action_show_tab_tent(self)  -> None: self.query_one(TabbedContent).active = "tab-tent"


if __name__ == "__main__":
    M5Watcher().run()
