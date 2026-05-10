"""
polpo_heartbeat_widget — Polpo Heartbeat panel per M5 Watcher

Forge sess.1745 (10 Mag 2026). Integra il loop heartbeat
(`~/scripts/polpo_heartbeat_loop.sh`) nel cockpit M5 Watcher.

Source: `~/.local/run/m5-watcher/polpo_heartbeat.json` (atomic write ad ogni tick).
Pattern compatibile con tg_bots_widget.py / kpi_widget.py / health_widget.py.

USO STANDALONE (CLI quick check):
    python3 ~/projects/m5-watcher/polpo_heartbeat_widget.py

USO INTEGRATO (in app.py M5 Watcher):
    from polpo_heartbeat_widget import heartbeat_data, render_heartbeat
    # in periodic refresh loop:
    hd = await asyncio.to_thread(heartbeat_data)
    panel_text = render_heartbeat(hd)

Cicatrice madre: feedback_dictation_mode_eco_loop_speaker_bleed_sess1745.md
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

HEARTBEAT_FILE = Path.home() / ".local" / "run" / "m5-watcher" / "polpo_heartbeat.json"
LOG_FILE = Path.home() / ".local" / "run" / "polpo_heartbeat.jsonl"
GOVERNANCE_FILE = Path.home() / ".local" / "run" / "governance_signals.jsonl"


def heartbeat_data() -> dict[str, Any]:
    """Read latest heartbeat snapshot. Returns dict with status/age/alerts.

    Schema returned:
        {
            "stale": bool,                # True if file missing or >5min old
            "age_s": int,
            "stt_up": bool,
            "stt_lines": int,
            "stt_delta": int,
            "f24_days_left": int,
            "mute_stale": bool,
            "scripts_dirty": int,
            "alerts": str,                # space-separated alert tokens
            "ts": str,                    # ISO from last tick
            "loop_history_n": int,        # tick count from log file
        }
    """
    result: dict[str, Any] = {
        "stale": True, "age_s": 99999,
        "stt_up": False, "stt_lines": 0, "stt_delta": 0,
        "f24_days_left": -1, "mute_stale": False, "scripts_dirty": 0,
        "alerts": "", "ts": "", "loop_history_n": 0,
    }

    if HEARTBEAT_FILE.exists():
        try:
            data = json.loads(HEARTBEAT_FILE.read_text())
            mtime = HEARTBEAT_FILE.stat().st_mtime
            age = int(time.time() - mtime)
            result.update(data)
            result["age_s"] = age
            result["stale"] = age > 300  # 5 min threshold
        except (json.JSONDecodeError, OSError):
            pass

    # Tick count via stat() size approx — O(1) vs O(N) line scan (sess.1745 perf fix)
    if LOG_FILE.exists():
        try:
            size = LOG_FILE.stat().st_size
            # Each line ~120 bytes JSON. Approx is fine for "ticks" UI hint.
            result["loop_history_n"] = max(0, size // 120)
        except OSError:
            pass

    return result


def governance_signals_recent(n: int = 3) -> list[dict[str, Any]]:
    """Read last N governance signals from JSONL tail. Returns most-recent-first.

    Sess.1745 — cuce heartbeat al daemon governance v1.1.0 (sess.1744-1748 doctrine).
    Read-only, fail-soft.
    """
    if not GOVERNANCE_FILE.exists():
        return []

    # Tail read solo ultimi ~16KB invece di intero file — perf fix sess.1745
    TAIL_BYTES = 16 * 1024
    try:
        with GOVERNANCE_FILE.open("rb") as f:
            try:
                f.seek(-TAIL_BYTES, 2)  # SEEK_END
            except OSError:
                f.seek(0)  # file più piccolo del tail
            chunk = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return []

    lines = chunk.strip().splitlines()
    # Drop prima riga: probabilmente troncata se siamo partiti da metà file
    if len(lines) > 1:
        lines = lines[1:]

    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            if "signal" in entry:
                out.append(entry)
                if len(out) >= n:
                    break
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def render_heartbeat(data: dict[str, Any], width: int = 60) -> str:
    """Render Rich-compatible markup string per Textual panel.

    Output stile m5-watcher: header + KPI row + alerts.
    """
    if data.get("stale", True):
        age = data.get("age_s", 0)
        return (
            "[bold red]🐙 POLPO HEARTBEAT[/bold red]\n"
            f"[dim]No data — last tick {age}s ago. "
            f"Loop /loop 30m attivo? Verifica: tail -1 {LOG_FILE}[/dim]"
        )

    stt = "[green]●[/green] UP" if data["stt_up"] else "[red]●[/red] DOWN"
    mute = "[red]LEAK[/red]" if data["mute_stale"] else "[green]OK[/green]"
    f24 = data["f24_days_left"]
    f24_color = "red" if f24 < 4 else ("yellow" if f24 < 7 else "green")

    dirty = data["scripts_dirty"]
    dirty_color = "red" if dirty > 100 else ("yellow" if dirty > 30 else "green")

    age = data["age_s"]
    age_str = f"{age}s" if age < 60 else f"{age // 60}m{age % 60}s"

    out = []
    out.append(f"[bold cyan]🐙 POLPO HEARTBEAT[/bold cyan]  [dim]· age {age_str} · ticks {data['loop_history_n']}[/dim]")
    out.append("")
    out.append(f"  STT bar       {stt}  [dim](history {data['stt_lines']} lines · Δ {data['stt_delta']:+d})[/dim]")
    out.append(f"  Voice mute    {mute}")
    out.append(f"  F24 16 Mag    [bold {f24_color}]D-{f24}[/bold {f24_color}]")
    out.append(f"  scripts/git   [bold {dirty_color}]{dirty} dirty[/bold dirty_color]".replace("dirty_color", dirty_color))

    alerts = (data.get("alerts") or "").strip()
    if alerts:
        out.append("")
        out.append(f"  [bold red]⚠ ALERTS:[/bold red] [yellow]{alerts}[/yellow]")

    return "\n".join(out)


def main_cli() -> None:
    """Standalone CLI: stampa snapshot corrente."""
    data = heartbeat_data()

    # Plain output (no rich markup) per terminal
    print("=" * 60)
    print("🐙 POLPO HEARTBEAT — m5-watcher widget standalone")
    print("=" * 60)

    if data["stale"]:
        print(f"⚠️  STALE — last tick {data['age_s']}s ago")
        print(f"   Loop alive? Verifica: tail -1 {LOG_FILE}")
        return

    age = data["age_s"]
    age_str = f"{age}s" if age < 60 else f"{age // 60}m{age % 60}s"
    stt = "● UP" if data["stt_up"] else "● DOWN"
    mute = "LEAK" if data["mute_stale"] else "OK"
    f24 = data["f24_days_left"]
    dirty = data["scripts_dirty"]

    print(f"  TS:           {data['ts']}  (age {age_str})")
    print(f"  Tick history: {data['loop_history_n']} entries in log")
    print()
    print(f"  STT bar:      {stt}  (history {data['stt_lines']} lines, Δ {data['stt_delta']:+d})")
    print(f"  Voice mute:   {mute}")
    print(f"  F24 16 Mag:   D-{f24}")
    print(f"  scripts/git:  {dirty} dirty")

    alerts = (data.get("alerts") or "").strip()
    if alerts:
        print()
        print(f"  ⚠️  ALERTS: {alerts}")

    print("=" * 60)


if __name__ == "__main__":
    main_cli()
