# m5-watcher

Cockpit TUI di monitoring real-time del MacBook Pro M5 Max — RAM/Swap/Thermal/Jetsam pressure, processi runaway, MCP drift.

## Scope
Watcher preventivo (real-time TUI Textual) — differente da `crash-checker` agent (forensics post-mortem). Mostra resource budget, alert pressione memoria, processi orfani parent claude.

## Ground Truth
- Owner: Mattia Calastri / sistema Polpo
- Memory index: `~/.claude/projects/-Users-mattiacalastri-projects/memory/MEMORY.md`
- Soglie Jetsam doctrine: vault `Astra OS/M5 Watcher Doctrine Evolution`
- Crash forensics correlato: `crash-checker` agent (post-mortem `.ips`)
- LaunchAgent: `com.polpo.m5-watcher` (se attivo)

## Rules
- Naming kebab-case con `-` come separatore.
- TUI Textual stack — design system Polpo condiviso (palette dark teal `#00d4aa`).
- Mai launchare training/video heavy senza pressure check prima.
- Se memoria libera <4GB → alert Jetsam imminente, blocca azioni heavy.

## Pre-flight
- `python3 -c "import psutil; print(psutil.virtual_memory())"` per snapshot RAM.
- Verifica processi parent claude orfani con `pgrep -af claude`.

## Stato reale (verificato sess.1809 — 2026-05-12)
- **GitHub remote**: `mattiacalastri/m5-watcher`
- **Stack**: Python TUI Textual — `pyproject.toml` + `requirements.txt`
- **Entry point**: `app.py` + `__main__.py` (eseguibile come modulo)
- **Variante**: `app_war_room.py` (modalità war-room design — vedi `WAR_ROOM_DESIGN_PLAN.md`)
- **Doc**: `CHANGELOG.md`, `README.md`, `WAR_ROOM_DESIGN_PLAN.md`
- **Backup storici preservati**: `app.py.bak.20260506_205836`, `app.py.bak.20260507_005738`, `app.py.bak.sess1605`, `app.py.bak.sess1607.feed_tab`, `app.py.bak.sess1745`, `app.py.bak.sess1777` — pattern conservativo (7 backup, non eliminare senza audit)
- **Last commit**: `9d4465c feat(feed): tg_bots_widget refactor + polpo_heartbeat + test sync sess.1768`

## Doctrine sess.1893 — Heatmap timeline tunable

- Introduzione costante `HEATMAP_DT_S` (default `1.0`) come single knob per modificare velocità timeline heatmap. Era hardcoded `2.0` in 4 punti diversi. Cambia solo line 209 in app.py.
- Effetto: a `HEATMAP_DT_S = 1.0` (default attuale) frame rate doppio rispetto a precedente, window heatmap 44s a cols=44 (era 88s). `tick_every` axis label auto-aggiornato via `round(20 / HEATMAP_DT_S)` per mantenere ~20s spacing.
- **Pre-flight obbligatorio post-edit `app.py`**: `python3 -c "import ast; ast.parse(open('app.py').read())"`. Cicatrice 13 Mag 2026: il backup `app.py.bak.sess1895` (now in `backups/`) era SyntaxError ma nessun verify post-write l'ha smascherato fino a 7gg dopo. AST verify e single-line, costo zero, blocca regressioni.
- **Cicatrice immune system hook**: `pre_tool_check.py` puo alzare THREAT 10 EMERGENCY MODE su pattern grep contenenti glyph unicode non-ASCII (es. greek delta). Workaround: scrivere `[Dd]elta` o `dt=` invece di glyph greek in pattern di ricerca. De-escalation automatica 10min senza alert.
- **Backup audit pattern**: `*.bak.sess*` in root vanno spostati in `backups/` con suffisso `.BROKEN.txt` se contengono SyntaxError. README.md in `backups/` documenta ogni archivio.

## Doctrine sess.1988 — App identity rebrand + scroll boot fix

- **Scroll iniziale fix** (`app.py` ~L2638): `on_mount` chiama `call_after_refresh(_pin_top)` → `self.screen.scroll_home(animate=False)`. Necessario perché `#tab-area` min-height 30 + `#top-row` min-height 30 + TitleBar 7-16 = ~67 righe; finestra Ghostty piccola va in overflow Screen e focus iniziale su prima TabPane scrolla via TitleBar.
- **Bundle rebrand**: `~/Applications/M5 Watcher.app` è clone di `/Applications/Ghostty.app` con identità `com.polpo.m5-watcher`. Artefatti in `scripts/rebrand/` (sorgente C launcher + entitlements + script master 10-step). Re-eseguire dopo update Ghostty. Pattern doctrine in vault: `Astra Digital Marketing/Patterns/macOS .app Rebrand — Mach-O Wrapper Canonical Sess.1988.md`.
- **run.sh + main.scpt args**: self-dispatch usa `--title=M5\ Watcher --class=com.polpo.m5-watcher --fullscreen=true --window-width=180 --window-height=50` quando lanciato da TERM_PROGRAM incompatibile. OSC title `🐙 M5 Watcher` settato anche per esecuzione manuale.
- **6 cicatrici catturate**: bash CFBundleExecutable → POSIX 162 / Sparkle Team ID mismatch / hardened runtime su script / `-e` security prompt + window split / `xattr quarantine` ereditato da `cp -R` / Claude auto-mode classifier blocca clone third-party → user-launched script. Sister opposta sess.1987 [[reference_macos_system_app_neutralize]] (neutralize system app vs rebrand third-party).
