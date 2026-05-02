"""Knowledge Graph canvas renderer — Rich markup string, Polpo palette."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import networkx as nx

# ── Polpo palette (same source as app.py) ─────────────────────────────────────
_P = json.loads((Path(__file__).parent / "polpo.tokens.json").read_text())["palette"]
TEAL      = _P["polpo_teal"]       # #00d4aa — MOC nodes
DIM       = _P["polpo_dim"]        # #6b7a8f — orphans + edges
FG        = _P["polpo_fg"]         # #e6f1ff — normal nodes
WHITE     = "#ffffff"
HOT_PINK  = "#ff2d92"              # focused node
ELEC_BLUE = "#00e5ff"              # hub nodes (degree >= 15)
LIME      = "#a8ff60"
ORANGE    = "#ff8a3d"

FILTER_MODES = ("all", "moc", "orphan")

# Canvas size — fits standard 220-col terminal at comfortable zoom
CANVAS_W = 90
CANVAS_H = 28


def _bresenham(x0: int, y0: int, x1: int, y1: int):
    """Yield (x, y) for each step of a Bresenham line."""
    dx = abs(x1 - x0); dy = abs(y1 - y0)
    sx = 1 if x1 > x0 else -1
    sy = 1 if y1 > y0 else -1
    err = dx - dy
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy; x0 += sx
        if e2 < dx:
            err += dx; y0 += sy


def _canvas_xy(raw_xy, w: int, h: int) -> tuple[int, int]:
    """Map spring_layout [-1,1] coordinates to canvas (col, row)."""
    x, y = float(raw_xy[0]), float(raw_xy[1])
    col = int((x + 1) / 2 * (w - 6)) + 3
    row = int((y + 1) / 2 * (h - 4)) + 2
    return max(0, min(w - 1, col)), max(0, min(h - 1, row))


def render_graph(
    gdata: dict,
    w: int = CANVAS_W,
    h: int = CANVAS_H,
    filter_mode: str = "all",
    focus_node: Optional[str] = None,
) -> str:
    """Return Rich markup string — full graph panel ready for Static.update()."""

    if "error" in gdata:
        return (
            f"[bold {ELEC_BLUE}]🕸 KNOWLEDGE GRAPH[/]\n\n"
            f"  [{ORANGE}]⚠ {gdata['error']}[/]\n\n"
            f"  [{DIM}]Vault path: ~/Library/Mobile Documents/iCloud~md~obsidian/[/]\n"
            f"  [{DIM}]Assicurati che Obsidian sia sincronizzato.[/]"
        )

    if not gdata.get("pos"):
        return (
            f"[bold {ELEC_BLUE}]🕸 KNOWLEDGE GRAPH[/]\n\n"
            f"  [{DIM}]🔄 Parsing vault…[/]"
        )

    G: nx.DiGraph = gdata["graph"]
    pos: dict     = gdata["pos"]
    stats: dict   = gdata.get("stats", {})

    # ── Filter visible set ────────────────────────────────────────────────────
    if filter_mode == "moc":
        visible = {n for n in pos if G.nodes[n].get("type") == "moc"}
    elif filter_mode == "orphan":
        visible = {n for n in pos if G.nodes[n].get("type") == "orphan"}
    else:
        visible = set(pos.keys())

    # ── Framebuffer: list[list[(char, color|None)]] ───────────────────────────
    buf: list[list[tuple[str, str | None]]] = [[(" ", None)] * w for _ in range(h)]

    def put(x: int, y: int, ch: str, col: str | None) -> None:
        if 0 <= x < w and 0 <= y < h:
            buf[y][x] = (ch, col)

    # ── Draw edges (cap at 300 for perf) ─────────────────────────────────────
    drawn = 0
    for src, dst in G.edges():
        if drawn >= 300:
            break
        if src not in visible or dst not in visible:
            continue
        if src not in pos or dst not in pos:
            continue
        x0, y0 = _canvas_xy(pos[src], w, h)
        x1, y1 = _canvas_xy(pos[dst], w, h)
        for bx, by in _bresenham(x0, y0, x1, y1):
            if buf[by][bx][0] == " ":
                put(bx, by, "·", DIM)
        drawn += 1

    # ── Draw nodes (low-degree first so hubs render on top) ───────────────────
    for node in sorted(visible, key=lambda n: G.degree(n)):
        if node not in pos:
            continue
        x, y   = _canvas_xy(pos[node], w, h)
        ntype  = G.nodes[node].get("type", "normal")
        deg    = G.degree(node)

        if node == focus_node:
            color, glyph = HOT_PINK, "◉"
        elif ntype == "moc":
            color, glyph = TEAL, "◆"
        elif deg >= 15:
            color, glyph = ELEC_BLUE, "●"
        elif ntype == "orphan":
            color, glyph = DIM, "○"
        else:
            color, glyph = FG, "●"

        put(x, y, glyph, color)

        # Label: hubs + MOC only (keeps canvas readable)
        if deg >= 8 or ntype == "moc":
            label = node[:15]
            for i, ch in enumerate(label):
                lx = x + 1 + i
                if lx < w and buf[y][lx][0] == " ":
                    put(lx, y, ch, color)

    # ── Render framebuffer → Rich markup ──────────────────────────────────────
    canvas_lines: list[str] = []
    for row in buf:
        parts: list[str] = []
        i = 0
        while i < len(row):
            ch, col = row[i]
            if col:
                # Merge consecutive same-color chars for shorter markup
                j = i + 1
                run = ch
                while j < len(row) and row[j][1] == col:
                    run += row[j][0]
                    j += 1
                parts.append(f"[{col}]{run}[/]")
                i = j
            else:
                # Collect plain whitespace
                j = i + 1
                run = ch
                while j < len(row) and row[j][1] is None:
                    run += row[j][0]
                    j += 1
                parts.append(run)
                i = j
        canvas_lines.append("".join(parts))

    # ── Header ────────────────────────────────────────────────────────────────
    n_total  = stats.get("total", "?")
    n_edges  = stats.get("edges", "?")
    n_mocs   = stats.get("mocs", 0)
    n_orphan = stats.get("orphans", 0)
    n_vis    = len(visible)

    sep = f"  [{DIM}]·[/]  "
    header = (
        f"[bold {TEAL}]🕸 KNOWLEDGE GRAPH[/]"
        f"{sep}[{DIM}]{n_total} note[/]"
        f"{sep}[{DIM}]{n_edges} link[/]"
        f"{sep}[{TEAL}]◆ {n_mocs} MOC[/]"
        f"{sep}[{DIM}]{n_orphan} orphan[/]"
        f"{sep}[{ELEC_BLUE}]● {n_vis} visible[/]"
    )

    filter_tabs = "   ".join(
        f"[bold {TEAL}][{m}][/]" if m == filter_mode else f"[{DIM}][{m}][/]"
        for m in FILTER_MODES
    )
    legend = (
        f"  [{TEAL}]◆ MOC[/]"
        f"  [{ELEC_BLUE}]● hub(≥15)[/]"
        f"  [{FG}]● note[/]"
        f"  [{DIM}]○ orphan[/]"
        f"  [{DIM}]· link[/]"
        f"      {filter_tabs}"
        f"    [{DIM}]f filter · r refresh[/]"
    )

    return "\n".join([header, legend, *canvas_lines])
