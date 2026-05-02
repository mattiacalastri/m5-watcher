# Changelog

All notable changes to **M5 Max Watcher** documented here.
Format: [Keep a Changelog](https://keepachangelog.com/) · Versioning: [SemVer](https://semver.org/).

---

## [2.0.1] — 2026-05-02 · Philosophical voice + cleanup

**Released sess.1238** — patch release. Adds Polpo philosophical-developer
sub-headers in EN under each section, removes buggy ZoomControls widget
(Cmd+/Cmd- native Ghostty kept), retires legacy `~/scripts/m5_watcher.py`.

### Added
- **Philosophical sub-headers** (italic DIM, one sentence each, EN voice):
  - ⚡ CPU — *Where silicon thinks — six leaves of efficiency, twelve rockets of performance.*
  - 🧠 Unified Memory — *One pool, no walls — Apple unified architecture observed as a single organism.*
  - 🔥 Heatmap — *The memory of work, rendered as heat — time scrolls left, intensity blooms hot.*
  - 📊 Analytics — *Where averages reveal the truth that instants hide — the slow drift behind every spike.*
  - 🔝 Processes — *The hungriest first — when something feels wrong, the answer is usually here.*
  - 🐙 Tentacoli — *The autonomic nervous system of the Polpo — Claude, MCP, daemons, watchdogs, alive.*
- **Static headers** for Processes & Tentacoli tabs (previously DataTable only)

### Removed
- **`ZoomControls` widget** — buggy, didn't work as expected. Cmd+/Cmd-
  native Ghostty zoom is the canonical UX (Mattia direct feedback)
- Unused imports: `Vertical`, `Button`, `subprocess`

### Infrastructure (out-of-tree, related)
- Killed legacy `~/scripts/m5_watcher.py` (PID 770, 20h29m uptime)
- Unloaded launchd `com.polpo.m5-watcher.plist` (kept respawning legacy)
- Renamed plist → `.LEGACY` and script → `m5_watcher.LEGACY.py`
- Only `com.polpo.m5-watcher-tui` (this v2.x project) remains active

---

## [2.0.0] — 2026-05-02 · Polpo Data Viz Edition

**Released sess.1238** — official version with energy palette + emoji semantics
+ rich-info header. Major design polish without architecture changes.

### Added
- **Module metadata** — `__version__`, `__author__`, `__license__`, `__company__`,
  `__codename__`, `__release_date__`, `__pillar__`, `__forged_in__`
- **Energy palette** — `LIME`, `ELEC_BLUE`, `DEEP_PURPL`, `HOT_PINK`, `ORANGE`,
  `SOFT_GREEN`, `WHITE` for visual hierarchy
- **Rainbow ad onda** — title with HSV spatial scrolling + sin V-modulation
  wave (`WAVE_AMP=0.45 FREQ=0.32 SPEED=2.0`) — flowing light wave across letters
- **Rich-info header** — 4-line centered TitleBar:
  1. emoji + rainbow wave title + emoji
  2. hardware identity (🍎 Apple · 💎 18C · 🧠 36GB Unified)
  3. operational state (🎯 sess · ⏱ uptime · 🐙×N · 🔌×N · 🕐 time)
  4. status live (🟢 LIVE · 🔋 bat · ⚡ cpu · ⚖ load · pressure · 💾 disk · 🌐 net)
- **Semantic emojis everywhere** — 🍃 S-CORES, 🚀 P-CORES, 🔥 HEATMAP,
  📊 ANALYTICS, 🧠 MEMORY, 💚/💛/🟧/❤️ HEALTH, ⚡⚖🩷🔷⚫🟧🟢 stat-row
- **Trend emoji-glyphs** — ▲▲/▲/●/▼/▼▼ colored by direction speed
- **Health emoji function** — `health_emoji(score)` for instant visual readout
- **`_count_claude_mcp()`** — robust dedupe-by-needle for claude+mcp counting
  (cicatrice sess.1192 version-rename safe)
- **`_claude_session_number()`** — multi-path probing (active_claims.json
  + session_current.md candidates)
- **`_format_uptime()`** — smart Nd/Nh/Nm formatting
- **ZoomControls widget** — bottom-right docked vertical `+`/`−` buttons,
  delegate Cmd+/− to Ghostty via osascript
- **Tab labels emoji** — 🌡 Heatmap, 📈 Analytics, 🔝 Processes, 🐙 Tentacoli
- **Tabs centered** via `align-horizontal: center`
- **CPU/MEM panels border decoration** — heavy TEAL with margin spacing
- **README.md** + **CHANGELOG.md** + **LICENSE**

### Changed
- TitleBar height 5 → 8 to host 4-line rich header
- Rainbow text now applies V (luminosity) sinusoidal modulation = wave effect
- All headers H2 colored + emoji-prefixed for hierarchy
- HEALTH score now displays in `bold WHITE` (max contrast)
- Trend arrows replaced with `trend_emoji()` (more visible glyphs)
- Stacked memory bar uses HOT_PINK/cluster_color/DIM/ORANGE/LIME segments
- Status footer reorganized with `┃` separators + emoji per section

### Removed
- Unused `median` import from `statistics`

### Architecture stability
- Layout `compose()` unchanged
- `_refresh_fast` / `_refresh_slow` cadence unchanged
- `data_sources.py` unchanged
- Bindings unchanged

---

## [1.0.0] — 2026-04-30

Initial release.

### Added
- 🐙 M5 Max Watcher v1.0 — Visual Analytics TUI
- TitleBar arcobaleno animato + swap layout (tabs sopra, cpu/mem sotto)
- Tentacoli detection Claude Code via name+cmdline (rename version-safe)
- `.gitignore` (venv + pycache)

---

🐙 *Forged by Mattia Calastri · Astra Digital Marketing*
