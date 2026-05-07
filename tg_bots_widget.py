"""
📡 TG BOTS WIDGET — m5-watcher tab content for Polpo Telegram Bot Watcher
==========================================================================
Forgiato sess.1605 (2026-05-07) come consumer di
~/.local/run/polpo-tg-watcher/state.json (prodotto da
polpo_tg_watcher_daemon.py).

Drop-in widget per app.py m5-watcher. Non importa Textual a livello modulo
(import-safe), espone una factory che ritorna un Static widget pronto da
montare in un nuovo TabPane "📡 Bots".

USO in app.py m5-watcher:

    from tg_bots_widget import TgBotsWidget, render_state

    # ... dentro compose_tabs:
    yield TabPane("📡 Bots", TgBotsWidget(id="tg-bots"))

    # in on_mount o refresh callback:
    self.query_one("#tg-bots", TgBotsWidget).refresh_data()

Standalone smoke (senza Textual):
    python3 tg_bots_widget.py
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

STATE_PATH = Path.home() / ".local" / "run" / "polpo-tg-watcher" / "state.json"

# ── Polpo color palette (mirror app.py) ──────────────────────────────────────
COLORS = {
    "high":   "bold red",
    "medium": "yellow",
    "low":    "dim white",
    "noise":  "grey50 strike",
}
SEV_GLYPH = {"high": "🔴", "medium": "🟡", "low": "🟢", "noise": "⚪"}


def load_state() -> Optional[dict]:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def render_state(state: Optional[dict] = None, max_recent: int = 18) -> str:
    """Build a Rich-markup string for the tab body. Pure function for testing."""
    if state is None:
        state = load_state()

    if not state:
        return (
            "[dim]📡 [bold]TG Bots Watcher[/bold] — daemon non attivo[/dim]\n\n"
            "[yellow]Avvia con:[/yellow]\n"
            "  [cyan]python3 ~/scripts/polpo_tg_watcher_daemon.py[/cyan]\n\n"
            "[yellow]LaunchAgent:[/yellow]\n"
            "  [cyan]launchctl load ~/Library/LaunchAgents/com.polpo.tg-bot-watcher.plist[/cyan]\n\n"
            "[dim]File atteso:[/dim] [cyan]" + str(STATE_PATH) + "[/cyan]"
        )

    lines = []

    # Header
    updated = state.get("updated", "?")
    total = state.get("total_seen", 0)
    by_sev = state.get("by_severity", {})

    h_high = by_sev.get("high", 0)
    h_med = by_sev.get("medium", 0)
    h_low = by_sev.get("low", 0)
    h_noise = by_sev.get("noise", 0)

    lines.append(
        f"[bold cyan]📡 TG Bots Watcher[/bold cyan]   "
        f"[dim]updated[/dim] [white]{updated}[/white]   "
        f"[dim]total[/dim] [bold white]{total}[/bold white]"
    )
    lines.append(
        f"[red]🔴 high {h_high}[/red]  "
        f"[yellow]🟡 med {h_med}[/yellow]  "
        f"[green]🟢 low {h_low}[/green]  "
        f"[grey50]⚪ noise {h_noise}[/grey50]"
    )
    lines.append("")

    # Per-bot top
    by_bot = state.get("by_bot", {})
    if by_bot:
        lines.append("[bold magenta]Per-bot[/bold magenta]")
        for bot, cnt in list(by_bot.items())[:6]:
            bar = "▆" * min(cnt, 20)
            lines.append(f"  [cyan]{bot:<22}[/cyan] [bold]{cnt:>4}[/bold]  [dim cyan]{bar}[/dim cyan]")
        lines.append("")

    # Recent events
    recent = state.get("recent", [])[:max_recent]
    if recent:
        lines.append("[bold magenta]Recent[/bold magenta]")
        for ev in recent:
            sev = ev.get("severity", "low")
            color = COLORS.get(sev, "white")
            glyph = SEV_GLYPH.get(sev, "•")
            ts = (ev.get("ts") or "")[:19].replace("T", " ")
            bot = (ev.get("bot") or "?")[:18]
            preview = (ev.get("preview") or "").replace("\n", " ⏎ ")[:78]
            tags = ev.get("tags", [])
            tag_str = ""
            if "urgent" in tags:
                tag_str += "[bold red]\\[urgent][/bold red] "
            if "noise" in tags:
                tag_str += "[grey50]\\[noise][/grey50] "
            if "verbose" in tags:
                tag_str += "[yellow]\\[verbose][/yellow] "

            lines.append(
                f"  [{color}]{glyph} {ts}[/{color}] "
                f"[bright_blue]{bot:<18}[/bright_blue] {tag_str}"
                f"[white]{preview}[/white]"
            )

    return "\n".join(lines)


# ── Textual widget wrapper (lazy import) ─────────────────────────────────────
try:
    from textual.widgets import Static
    from textual.timer import Timer

    class TgBotsWidget(Static):
        """Auto-refreshing widget for the m5-watcher 📡 Bots tab."""

        DEFAULT_CSS = """
        TgBotsWidget {
            padding: 1 2;
            content-align: left top;
        }
        """

        def __init__(self, *args, refresh_seconds: float = 5.0, **kwargs):
            super().__init__("", *args, **kwargs)
            self._refresh_s = refresh_seconds
            self._timer: Optional[Timer] = None

        def on_mount(self) -> None:
            self.refresh_data()
            self._timer = self.set_interval(self._refresh_s, self.refresh_data)

        def refresh_data(self) -> None:
            try:
                self.update(render_state())
            except Exception as e:
                self.update(f"[red]TgBotsWidget error: {e}[/red]")

except ImportError:
    # Textual not available — module still importable for testing
    TgBotsWidget = None


# ── Standalone smoke ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(render_state())
