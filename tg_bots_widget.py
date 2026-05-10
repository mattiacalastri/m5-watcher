"""
📡 TG BOTS WIDGET — m5-watcher tab content for Polpo Telegram Bot Watcher
==========================================================================
Forgiato sess.1605 (2026-05-07) come consumer di
~/.local/run/polpo-tg-watcher/state.json (prodotto da
polpo_tg_watcher_daemon.py).

Drop-in widget per app.py m5-watcher. Non importa Textual a livello modulo
(import-safe), espone una factory che ritorna un Static widget pronto da
montare in un nuovo TabPane "📡 Bots".

Sess.1703 redesign: passaggio da stringa Rich-markup → renderable Rich
(Panel + Table.grid + Rule + Padding) per ottenere boxing/padding/margin
reali. Allineato ai Polpo design tokens (polpo.tokens.json — mirror locale
per preservare l'invariante import-safe).

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
from pathlib import Path
from typing import Optional

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

STATE_PATH = Path.home() / ".local" / "run" / "polpo-tg-watcher" / "state.json"

# ── Polpo tokens (mirror polpo.tokens.json — keep in sync) ───────────────────
TEAL       = "#00d4aa"
TEAL_DIM   = "#007a62"
FG         = "#e6f1ff"
DIM        = "#8a98ad"
RED        = "#ff3366"
YELLOW     = "#ffd400"
GREEN      = "#5dffaa"
CYAN       = "#00d9ff"

SEV_STYLE = {
    "high":   f"bold {RED}",
    "medium": YELLOW,
    "low":    GREEN,
    "noise":  f"strike {DIM}",
}
SEV_GLYPH = {"high": "🔴", "medium": "🟡", "low": "🟢", "noise": "⚪"}


def load_state() -> Optional[dict]:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ── Sub-renderables ──────────────────────────────────────────────────────────
def _empty_state() -> RenderableType:
    body = Text()
    body.append("📡 ", style=TEAL)
    body.append("TG Bots Watcher", style=f"bold {FG}")
    body.append("  — daemon non attivo\n\n", style=DIM)
    body.append("Avvia con:\n", style=YELLOW)
    body.append("  python3 ~/scripts/polpo_tg_watcher_daemon.py\n\n", style=CYAN)
    body.append("LaunchAgent:\n", style=YELLOW)
    body.append("  launchctl load ~/Library/LaunchAgents/com.polpo.tg-bot-watcher.plist\n\n", style=CYAN)
    body.append("File atteso: ", style=DIM)
    body.append(str(STATE_PATH), style=CYAN)
    return Panel(body, border_style=TEAL_DIM, padding=(1, 2))


def _header(state: dict) -> RenderableType:
    title = Text()
    title.append("📡  ")
    title.append("TG BOTS WATCHER", style=f"bold {TEAL}")

    meta = Text()
    meta.append("updated ", style=DIM)
    meta.append(state.get("updated", "?"), style=FG)

    title_row = Table.grid(expand=True)
    title_row.add_column(justify="left")
    title_row.add_column(justify="right")
    title_row.add_row(title, meta)

    sub = Text(
        "Polpo Squad notification feed · severity-aware  ·  "
        "ogni eco dei tentacoli passa qui prima di farsi rumore.",
        style=f"italic {DIM}",
    )

    sev = state.get("by_severity", {})

    def _badge(glyph: str, count: int, label: str, color: str) -> Text:
        t = Text()
        t.append(f"{glyph}  ", style=color)
        t.append(str(count), style=f"bold {color}")
        t.append(f" {label}", style=color)
        return t

    total = Text()
    total.append("total  ", style=DIM)
    total.append(str(state.get("total_seen", 0)), style=f"bold {FG}")

    badges = Table.grid(padding=(0, 4))
    for _ in range(5):
        badges.add_column()
    badges.add_row(
        total,
        _badge("🔴", sev.get("high", 0), "high", RED),
        _badge("🟡", sev.get("medium", 0), "med", YELLOW),
        _badge("🟢", sev.get("low", 0), "low", GREEN),
        _badge("⚪", sev.get("noise", 0), "noise", DIM),
    )

    body = Group(title_row, Text(""), sub, Text(""), badges)
    return Panel(body, border_style=TEAL_DIM, padding=(1, 2))


def _section_header(label: str) -> RenderableType:
    head = Text()
    head.append("◇  ", style=f"bold {TEAL}")
    head.append(label, style=f"bold {FG}")
    return Group(head, Rule(style=TEAL_DIM, characters="─"))


def _per_bot(state: dict) -> RenderableType:
    by_bot = state.get("by_bot", {})
    if not by_bot:
        return Text()

    table = Table.grid(padding=(0, 3))
    table.add_column(style=CYAN, width=24, no_wrap=True)
    table.add_column(justify="right", style=f"bold {FG}", width=4)
    table.add_column(style=TEAL)

    for bot, cnt in list(by_bot.items())[:6]:
        bar = "▆" * min(cnt, 20)
        table.add_row(bot, str(cnt), bar)

    return Group(
        _section_header("PER-BOT ACTIVITY"),
        Padding(table, (1, 0, 0, 2)),
    )


def _recent(state: dict, max_recent: int) -> RenderableType:
    recent = state.get("recent", [])[:max_recent]
    if not recent:
        return Text()

    # 4 colonne: glyph+ts insieme · bot · tag chip · preview elastica
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(min_width=22, no_wrap=True)                            # glyph + ts
    table.add_column(min_width=18, no_wrap=True, overflow="ellipsis")       # bot
    table.add_column(min_width=8,  no_wrap=True)                            # tag chip
    table.add_column(ratio=1,      no_wrap=True, overflow="ellipsis")       # preview

    for ev in recent:
        sev = ev.get("severity", "low")
        glyph = SEV_GLYPH.get(sev, "•")
        ts = (ev.get("ts") or "")[:19].replace("T", " ")
        bot = (ev.get("bot") or "?")[:18]
        preview = (ev.get("preview") or "").replace("\n", " ⏎ ")

        tags = ev.get("tags", [])
        tag_chip = Text()
        if "urgent" in tags:
            tag_chip.append("urgent", style=f"bold {RED}")
        elif "noise" in tags:
            tag_chip.append("noise", style=f"strike {DIM}")
        elif "verbose" in tags:
            tag_chip.append("verbose", style=YELLOW)

        when = Text()
        when.append(f"{glyph}  ")
        when.append(ts, style=DIM)

        # Preview color: alta severità + noise restano segnati, gli altri body fg
        preview_style = SEV_STYLE.get(sev, FG) if sev in ("high", "noise") else FG

        table.add_row(
            when,
            Text(bot, style=CYAN),
            tag_chip,
            Text(preview, style=preview_style),
        )

    return Group(
        _section_header("RECENT STREAM"),
        Padding(table, (1, 0, 0, 2)),
    )


def build_renderable(state: Optional[dict] = None, max_recent: int = 18) -> RenderableType:
    """Build a Rich renderable for the tab body. Pure function for testing."""
    if state is None:
        state = load_state()
    if not state:
        return _empty_state()
    return Group(
        _header(state),
        Text(""),
        _per_bot(state),
        Text(""),
        _recent(state, max_recent),
    )


def render_state(state: Optional[dict] = None, max_recent: int = 18) -> RenderableType:
    """Backward-compatible alias. Returns a Rich renderable (was string until sess.1703).
    Static.update() accetta entrambi, quindi i caller esistenti continuano a funzionare."""
    return build_renderable(state, max_recent)


# ── Textual widget wrapper (lazy import) ─────────────────────────────────────
try:
    from textual.widgets import Static
    from textual.timer import Timer

    class TgBotsWidget(Static):
        """Auto-refreshing widget for the m5-watcher 📡 Bots tab."""

        DEFAULT_CSS = """
        TgBotsWidget {
            padding: 1 2;
            margin: 0 0 1 0;
            height: auto;
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
                self.update(build_renderable())
            except Exception as e:
                self.update(f"[red]TgBotsWidget error: {e}[/red]")

except ImportError:
    # Textual not available — module still importable for testing
    TgBotsWidget = None


# ── Standalone smoke ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    from rich.console import Console
    Console().print(build_renderable())
