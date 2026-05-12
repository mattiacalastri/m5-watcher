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
