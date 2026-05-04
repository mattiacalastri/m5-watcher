# Changelog

All notable changes to **M5 Max Watcher** documented here.
Format: [Keep a Changelog](https://keepachangelog.com/) · Versioning: [SemVer](https://semver.org/).

---

## [2.5.1] — 2026-05-05 · Sparkline trend in line5 + single-source API (sess.1539 round 2)

**Released sess.1539 round 2** — line5 KPI passa da snapshot statico a
trend monitor live. Sparkline inline (MRR/Out/Pipeline) leggono lo stesso
deque history del KPI tab → zero divergenza tra panel e header.

### Added
- **`kpi_widget.kpi_for_titlebar(data, spark_w=6)`** — nuova API pubblica,
  single source of truth per line5. Restituisce dict normalizzato con
  mrr/mrr_delta/outstanding/pipeline/leads/cold_avg + sparkline unicode
  (spark_mrr/spark_out/spark_pipe). NaN/inf safe via `_safe_float`.
- **Sparkline inline in line5** — trend visivo `▁▂▃▄▅▆▇█` accanto al valore
  numerico. Tier full+compact, omessi al tier tiny per spazio. Cresce dopo
  ≥2 punti history (15 min di dati a refresh slow 30s).
- **+4 test sess.1539 round 2** — kpi_for_titlebar empty/shape/sparkline/NaN.

### Changed
- **`_update_subtitle`** — collassato blocco 17 righe `_safe_float` ripetuto
  6× + try/except in 1 chiamata `kpi_widget.kpi_for_titlebar()`. Refactor
  zero-regression: stessa shape payload, semantica preservata.
- **line5 builder** — sparkline-aware: il prefix `[COLOR]bars[/]` viene
  aggiunto solo quando history ≥2 punti, non rompe il layout responsive.

---

## [2.5.0] — 2026-05-04 · Unified Header + KPI line5 (sess.1539)

**Released sess.1539** — header coerente cross-tab e KPI business sempre
visibili. Identità visiva costante: l'header non saltella più tra tab.

### Added
- **TitleBar line5 — Business KPI band** sempre presente: 💰 MRR · 📌 Outstanding
  · 🎯 Pipeline · 🔥 Lead · 🕐 Cold avg. Pattern uniforme `Nome KPI · Dato · Unità`,
  responsive su 3 breakpoint (≥100, ≥80, <80 cols)
- **Per-tab header banner** uniforme su tutti i 9 TabPane (Heatmap, Analytics,
  Processes, Tentacoli, Graph, KPI, Logs, Sentinel, Debug). Pattern condiviso
  `[bold COLOR]EMOJI NAME[/] · tagline\n[italic DIM]poetic line[/]`
- **CSS `*-header` unificato** — height: auto + padding 0 0 1 0 per coerenza visiva

### Changed
- **TitleBar uniforme cross-tab** — rimossa logica `is_dense` che collassava
  l'header a 6 righe sui tab data-dense (Heatmap/Logs/Procs/Tent/Debug). Sizing
  ora deciso solo da `on_resize` (cols/rows-based)
- **TitleBar height bumped** 6/8/15 → 7/9/16 per ospitare line5 KPI
- **Logs tab strip order** — header `📋 ACTIVITY STREAM` ora sopra le 5 strip
  roadmap (Polestar/Vettori/Trap/Filamenti/Blocchi) per gerarchia visiva chiara
- **Cold avg precision** `:.0f` → `:.1f` gg (granularità decimale)

---

## [2.4.0] — 2026-05-03 · UNIFEED Event Panel (sess.1465)

**Released sess.1465** — live event feed under Unified Memory panel.
Zero new dependencies — pure state comparison on already-collected data.

### Added
- **`render_feed()`** — timestamped event renderer, newest-first, Textual markup
- **`#feed-panel` (UNIFEED)** below `#mem-panel` inside `Vertical #mem-col` (3fr/1fr split)
- **Memory pressure transitions** — NORMAL/MODERATE/HIGH/CRITICAL with color coding
- **Swap activation/deactivation** — threshold >500MB (aligns with `render_mem` logic)
- **CPU spike detection** — >80% avg sustained for 3 consecutive ticks (6s) → HOT_PINK alert
- **`_event_feed` deque** — maxlen=15, `appendleft` so newest is always first
- State attrs: `_prev_pressure`, `_prev_swap_active`, `_cpu_spike_ticks`

### Fixed
- Swap threshold was `> 0` (always true on M5 Max); corrected to `> 0.5e9`

---

## [2.3.0] — 2026-05-03 · Process Triage Advisor (sess.1376)

**Released sess.1376** — process knowledge base with 40+ Polpo-aware patterns.
Press `c` to open TriageScreen modal with KILL_SAFE / CAUTIOUS / KEEP labels.

### Added
- **`triage_processes()` in data_sources.py** — KB 40+ patterns for MCP servers,
  LaunchAgents, Claude sessions, daemons, watchdogs. Edge case: MCP with
  `parent=launchd` → CAUTIOUS (not spawned by a live Claude = orphan risk)
- **`TriageScreen` ModalScreen in app.py** — overlay with KILL_SAFE (green) /
  CAUTIOUS (yellow) / KEEP (teal) color-coded process list. Keybinding `c`
- **Knowledge base** — recognizes: claude code sessions, MCP server processes,
  Jarvis STT/TTS daemons, LaunchAgents (btc, bridge, watcher), polpo daemons

---

## [2.2.1] — 2026-05-03 · Graph Full-Screen Mode

**Released sess.1376** — full-screen Graph tab: top-row hides automatically
when Graph tab is active, maximizing vault visualization space.

### Changed
- **Graph tab full-screen** — `on_tabbed_content_tab_activated` handler hides
  `#top-row` (CPU/MEM panels + titlebar) when Graph tab is active; restores on
  any other tab switch. Zero layout jank, zero extra bindings needed.

---

## [2.2.0] — 2026-05-02 · KPI Tab + POLPO Rainbow Banner (sess.1346 · sess.1350)

**Released sess.1350** — adds a business vitals KPI panel and the iconic POLPO
ASCII rainbow banner in the titlebar.

### Added
- **📊 KPI tab** — business vitals panel v2.2.0: MRR, outstanding, pipeline
  weighted, setter metrics, infra counts; reads live from `KPI.md` frontmatter
- **POLPO ASCII art rainbow banner** — rendered in TitleBar with ansi colors;
  fixes Tab top margin that was clipping the tab labels

---

## [2.1.0] — 2026-05-02 · Knowledge Graph + Test Suite (sess.1279 · sess.1301 · sess.1302)

**Released sess.1302** — adds Vault Intelligence Panel (Tab 5 🕸 Graph) and first
comprehensive test suite (64 tests) covering all modules including vault_parser and graph_widget.

### Added
- **Tab 5 🕸 Graph — Vault Intelligence Panel** (two-phase delivery):
  - *Phase 1 (sess.1279, commit a61e99c)*: base panel with dot-plot ASCII layout,
    filter modes (all / moc / orphan), keybinding `5` + `f` to cycle filters
  - *Phase 2 (sess.1301, commit 405046c)*: full Neural Density cockpit —
    ⚡ Neural Density score (0-100, formula: density×0.30 + clustering×0.25 + giant_ratio×0.25 + connectivity×0.20),
    🧠 Data Attractors (top-10 in-degree with betweenness centrality profile),
    📊 Stato Vault (seed / growing / evergreen / stub frontmatter distribution),
    🕐 Modificate Oggi (recent activity + 7-day growth count),
    🕸 Topologia (bridge nodes + cluster map)
- **`vault_parser.py`** — wikilink extractor → NetworkX DiGraph + Neural Density metrics.
  Two-pass (stat-only Pass 1 + read Pass 2), cache TTL 60s, TOP_N=120, betweenness k=30.
  Live vault at release: 3190 note · 13413 link · 24 MOC · 233 orphan · ND 69/100
- **`graph_widget.py`** — Vault Intelligence Panel renderer (Rich markup, Polpo palette,
  `_ND_LOW=0.0003` / `_ND_MID=0.001` / `_ND_HIGH=0.002` thresholds)
- **`test_suite.py`** (sess.1302, commit 267fce9) — 64-test comprehensive suite:
  TestSyntax · TestDeps · TestUtilities · TestDataSources · TestRenderers ·
  TestInternals · TestVaultParser · TestGraphWidget · TestHeadlessTextual.
  Covers py_compile, all imports, all data sources (async-safe with `asyncio.run`),
  all renderers, vault_parser live + error path, graph_widget all filter modes + error path,
  headless Textual compose + tab switch 1-5 + pause toggle.

### Changed
- `_refresh_slow()` extended with `asyncio.to_thread(vault_parser.vault_graph_data)` (5s, non-blocking)
- Keybinding docstring updated: `1-5 tab switch · f cycle graph filter`

---

## [2.0.2] — 2026-05-02 · Polpo Voice Panel (sess.1253 + sess.1269)

**Released sess.1269** — integrates the Polpo Voice system as a native Textual panel,
making M5 Max Watcher the canonical reference TUI architecture for all future Polpo cockpit panels.

### Added
- **Polpo Voice panel** — mirrors JarvisToggle.app layout in Textual markup:
  - Header: `🐙 Polpo · Voice` with device info (mic + speaker)
  - State pills: `OUT` (active/idle) · `IN` (active/idle) · `LOOP` (on/off) · `DIALOG` (mode)
  - Audio waveform: HOT_PINK sparkline from `stt_levels.bin` float32 stream
  - "VOCE DEL POLPO" section: active voice name + star accent (dynamically read from `voices.json`)
  - Recent transcriptions: last 10 entries from `stt_history.jsonl` with relative timestamps
- **`voice_data()` data source** — reads `~/.local/run/jarvis/stt_history.jsonl`,
  `stt_levels.bin`, `stt_state`, `voice_selected`; safe no-op when Jarvis offline
- **Dynamic voice name** — reads `voices.json` for display name with fallback to hardcoded dict
- **Tab centering** — all 4 tabs (Heatmap · Analytics · Processes · Tentacoli) center-aligned
- **Active tab highlight** — active tab in `ELEC_BLUE bold`, inactive in dim

### Fixed
- `stt_state` pill logic: corrected state values (`speaking`/`listening`/`idle`) for
  accurate OUT/IN/LOOP status rendering

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
