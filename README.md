# 🐙 M5 Max Watcher

> **Real-time analytics TUI for Apple M5 Max** — stream cores, memory, I/O,
> tentacoli (Polpo background processes) in a beautiful terminal cockpit.

**Version:** 2.0.0 · **Codename:** Polpo Data Viz Edition · **Released:** 2026-05-02
**Author:** Mattia Calastri · **Company:** Astra Digital Marketing
**Pillar:** Astra OS · Polpo Cockpit Suite · **Forged in:** sess.1238

---

## What it does

M5 Max Watcher is a Textual-based terminal UI that streams live observability
data from your Apple Silicon M5 Max:

- **CPU** per-core (6 efficiency + 12 performance) with sub-pixel smooth bars
- **Unified memory** breakdown — wired / active / inactive / compressed / free
- **Temporal heatmap** of all 18 cores (88s rolling window, Δt=2s)
- **Statistics** min/avg/p95/max + P/S efficiency ratio + 2-min sparklines
- **Top processes** by CPU + RAM
- **Tentacoli** — Polpo background process map (Claude sessions, MCP servers,
  Jarvis voice daemon, watchdogs, dashboards)
- **Header rich-info** — session number, uptime, claude×N, mcp×N, time, status

## Design philosophy

- **Data viz first** — every glyph, color, emoji is a semantic anchor
- **Polpo Design System** — pastel rainbow ad onda title with HSV wave +
  luminosity sin modulation
- **Energy palette** — LIME · ELEC_BLUE · DEEP_PURPL · HOT_PINK · ORANGE ·
  SOFT_GREEN · WHITE
- **Hierarchy explicit** — H1 rainbow → H2 colored emoji → H3 cluster →
  critical values WHITE bold → body semantic-colored → chrome DIM

## Tabs

| Tab | Content |
|---|---|
| 🌡 Heatmap | Temporal core heatmap (S-cluster 🍃 + P-cluster 🚀) |
| 📈 Analytics | Stats min/avg/p95/max + P/S ratio + 2-min sparklines |
| 🔝 Processes | Top 16 by CPU + RAM |
| 🐙 Tentacoli | Polpo background processes map |

## Keybindings

| Key | Action |
|---|---|
| `q` | Quit |
| `r` | Force refresh |
| `p` | Toggle pause |
| `1` `2` `3` `4` | Switch tab (Heatmap / Analytics / Processes / Tentacoli) |
| Click `+` `−` (bottom-right) | Zoom Ghostty terminal (delegates `Cmd+`/`Cmd-`) |

## Installation

```bash
cd ~/projects/m5-watcher
python3 -m venv venv
venv/bin/pip install -r requirements.txt
./run.sh
```

Or launch dedicated Ghostty window:

```bash
open -na Ghostty.app --args -e ~/projects/m5-watcher/run.sh
```

## Architecture

```
m5-watcher/
├── app.py               # main Textual app (TUI, render functions, ZoomControls)
├── data_sources.py      # psutil/sysctl wrappers — pure data layer
├── polpo.tokens.json    # Polpo Design System palette
├── requirements.txt     # textual + psutil pinned
├── run.sh               # launcher
└── README.md            # this file
```

**Refresh cadence:**
- Fast (2s): per-core CPU, history, heatmap, analytics, header status
- Slow (5s): unified memory, battery, top processes, tentacoli, claude+mcp count

**Data sources:** `data_sources.py` pure wrapper around `psutil` + `sysctl`,
no async I/O, can be tested headless.

## Visual language

| Color | Meaning |
|---|---|
| 🟢 `LIME` `#a8ff60` | alive / free RAM / S-cluster signature |
| 🔵 `ELEC_BLUE` `#00e5ff` | live / cpu / electric flow |
| 💜 `DEEP_PURPL` `#9d4dff` | P-cluster (performance) |
| 🌸 `HOT_PINK` `#ff2d92` | spike / write / attention |
| 🟧 `ORANGE` `#ff8a3d` | warm warning / compressed / heat |
| 🍃 `SOFT_GREEN` `#5dffaa` | S-cluster soft signature |
| ⚪ `WHITE` `#ffffff` | critical headlines (HEALTH score, sess number) |

## Trend glyphs

| Glyph | Meaning |
|---|---|
| `▲▲` HOT_PINK | rising fast (>+4/sample) |
| `▲` ORANGE | rising (>+1.5/sample) |
| `●` DIM | stable |
| `▼` SOFT_GREEN | descending (<-1.5/sample) |
| `▼▼` LIME | descending fast (<-4/sample) |

## Health scoring

`(100 - cpu) × 0.35 + (100 - ram) × 0.45 + (100 - load/N_CORES × 100) × 0.20`

Visual badge: 💚 ≥80 · 💛 ≥60 · 🟧 ≥40 · ❤️ <40

## License

Proprietary © 2026 Mattia Calastri · Astra Digital Marketing — All Rights Reserved.

See [LICENSE](LICENSE) for details.

## Credits

- **Polpo Design System** — color tokens, glyph language, hierarchy doctrine
- **Textual** — TUI framework
- **psutil** — process & system metrics
- **Apple Silicon M5 Max** — the silicon that earns the cockpit

---

🐙 *"Data is beautiful. Polpo is beautiful."* — Mattia Calastri, sess.1238
