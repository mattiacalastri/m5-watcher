"""feed_populators — Live populator delle 4 DataTable del tab Feed (m5-watcher).

Forgiato sess.1607 per sostituire i 4 Static markup blob (outstanding-section,
traps-banner, filaments-section, blocks-section) con DataTable strutturate
auto-popolanti. Ogni funzione svuota il DataTable, riordina per severity, riempie
con Rich markup color-coded.

Self-contained: solo dipendenze stdlib + Textual DataTable + roadmap_*.
Cache: le read_*() API hanno già TTL 60s upstream, qui solo formatter.

Public API:
    populate_outstanding_table(table)
    populate_traps_table(table)
    populate_filaments_table(table)
    populate_blocks_table(table)

Schema colonne (settati on_mount in app.py — qui solo add_row):
    outstanding: 5 col → Cliente · € · D+ · Stato · Note
    traps:       4 col → Trap · Dettaglio · Owner · Action
    filaments:   4 col → Filamento · Stato · Deadline · Δ session
    blocks:      4 col → Blocco · Owner · D+ · Severity badge
"""
from __future__ import annotations

import textwrap
from typing import Any

from textual.widgets import DataTable

from roadmap_blocks import read_blocks
from roadmap_common import DIM, LIME, ORANGE, RED, TEAL, severity_rank
from roadmap_filaments import detect_session_drift, read_filaments
from roadmap_outstanding import read_outstanding
from roadmap_traps import detect_active_traps


# ── Severity badges (allineati a app.py _SEV_BADGE) ──────────────────────────
_SEV_BADGE: dict[str, str] = {
    "P0":        f"[bold {RED}]●[/]",
    "P1":        f"[bold {ORANGE}]●[/]",
    "P2":        f"[bold {ORANGE}]●[/]",
    "info":      f"[{DIM}]·[/]",
    "info-lime": f"[{LIME}]·[/]",
    "noise":     f"[{DIM}]·[/]",
}

_SEV_LABEL: dict[str, str] = {
    "P0":        "🔴 P0",
    "P1":        "🟡 P1",
    "P2":        "🟡 P2",
    "info":      "· info",
    "info-lime": "· info",
    "noise":     "· noise",
}

_SEV_COLOR: dict[str, str] = {
    "P0":        RED,
    "P1":        ORANGE,
    "P2":        ORANGE,
    "info":      DIM,
    "info-lime": LIME,
    "noise":     DIM,
}


def _trunc(text: str | None, width: int) -> str:
    """Truncate string a width char con placeholder ellipsis. Safe su None/empty."""
    if not text:
        return ""
    s = str(text).replace("\n", " ").strip()
    if len(s) <= width:
        return s
    return textwrap.shorten(s, width=width, placeholder="…") or (s[: width - 1] + "…")


def _empty_row(table: DataTable, n_cols: int, msg: str = "✓ no entries — silenzio operativo") -> None:
    """Aggiunge una singola riga empty-state con markup dim italic, padding colonne."""
    cells = [f"[dim italic]{msg}[/dim italic]"] + [""] * (n_cols - 1)
    table.add_row(*cells)


def _error_row(table: DataTable, n_cols: int, source: str) -> None:
    """Banner errore single-row quando read_*() esplode."""
    cells = [f"[{RED}]⚠ {source} unavailable[/{RED}]"] + [""] * (n_cols - 1)
    table.add_row(*cells)


# ══════════════════════════════════════════════════════════════════════════════
# OUTSTANDING — 5 col: Cliente · € · D+ · Stato · Note
# ══════════════════════════════════════════════════════════════════════════════

def populate_outstanding_table(table: DataTable) -> None:
    """Popola la tabella Outstanding ordinata per severity (P0 first) + days_aged desc."""
    table.clear()
    try:
        entries = read_outstanding() or []
    except Exception:
        _error_row(table, 5, "outstanding")
        return

    if not entries:
        _empty_row(table, 5)
        return

    # Sort: severity asc (P0=0 prima), then days_aged desc (None last)
    sorted_entries = sorted(
        entries,
        key=lambda e: (
            severity_rank(e.get("severity", "info")),
            -(e.get("days_aged") if e.get("days_aged") is not None else -1),
        ),
    )

    for entry in sorted_entries:
        sev = entry.get("severity", "info")
        sev_color = _SEV_COLOR.get(sev, DIM)
        sev_badge = _SEV_BADGE.get(sev, _SEV_BADGE["info"])
        sev_label = _SEV_LABEL.get(sev, "· info")
        amount = entry.get("amount", 0) or 0
        amt_str = f"{amount:,}".replace(",", ".")
        days_aged = entry.get("days_aged")
        d_plus_str = (
            f"[{sev_color}]D+{days_aged}[/]" if days_aged is not None
            else f"[{DIM}]D+?[/]"
        )
        cliente = entry.get("cliente", "?")
        note = entry.get("note", "") or ""

        table.add_row(
            f"[bold]{_trunc(cliente, 32)}[/]",
            f"[{ORANGE}]€{amt_str}[/]",
            d_plus_str,
            f"{sev_badge} {sev_label}",
            f"[{DIM}]{_trunc(note, 50)}[/]",
        )


# ══════════════════════════════════════════════════════════════════════════════
# TRAPS — 4 col: Trap · Dettaglio · Owner · Action
# ══════════════════════════════════════════════════════════════════════════════

def populate_traps_table(table: DataTable) -> None:
    """Popola la tabella Traps. Sort by severity. Owner = cicatrice_ref short."""
    table.clear()
    try:
        traps = detect_active_traps() or []
    except Exception:
        _error_row(table, 4, "traps")
        return

    if not traps:
        _empty_row(table, 4, "✓ 0/5 trap attivi — silenzio operativo")
        return

    sorted_traps = sorted(
        traps,
        key=lambda t: severity_rank(t.get("severity", "P2")),
    )

    for trap in sorted_traps:
        sev = trap.get("severity", "P2")
        sev_color = _SEV_COLOR.get(sev, ORANGE)
        sev_badge = _SEV_BADGE.get(sev, _SEV_BADGE["info"])
        trap_name = trap.get("trap", "?")
        evidence = trap.get("evidence", "")
        cicatrice = trap.get("cicatrice_ref", "")
        mitigation = trap.get("mitigation", "")

        table.add_row(
            f"{sev_badge} [{sev_color}]🪤 {_trunc(trap_name, 28)}[/]",
            f"[{DIM}]{_trunc(evidence, 48)}[/]",
            f"[{DIM}]{_trunc(cicatrice, 28)}[/]",
            f"[{TEAL}]{_trunc(mitigation, 40)}[/]",
        )


# ══════════════════════════════════════════════════════════════════════════════
# FILAMENTS — 4 col: Filamento · Stato · Deadline · Δ session
# ══════════════════════════════════════════════════════════════════════════════

def populate_filaments_table(table: DataTable) -> None:
    """Popola la tabella Filamenti. Skip is_resolved (chiusi). Drift-aware Δ session."""
    table.clear()
    try:
        entries = read_filaments() or []
    except Exception:
        _error_row(table, 4, "filaments")
        return

    # Skip resolved (filaments chiusi ✓)
    active = [f for f in entries if not f.get("is_resolved", False)]

    if not active:
        _empty_row(table, 4)
        return

    # Drift detection — graceful fallback se esplode
    try:
        drift_map = detect_session_drift(active)
    except Exception:
        drift_map = {}

    # Sort: severity asc, then days_drift desc (None last)
    sorted_entries = sorted(
        active,
        key=lambda f: (
            severity_rank(f.get("severity", "info")),
            -(f.get("days_drift") or 0),
        ),
    )

    for entry in sorted_entries:
        sev = entry.get("severity", "info")
        sev_color = _SEV_COLOR.get(sev, DIM)
        sev_badge = _SEV_BADGE.get(sev, _SEV_BADGE["info"])
        name = entry.get("name", "?")
        stato = entry.get("stato", "") or ""
        deadline = entry.get("deadline") or ""
        days_drift = entry.get("days_drift")

        if days_drift and days_drift > 0:
            drift_str = f"[bold {RED}]D+{days_drift}[/]"
        else:
            d_info = drift_map.get(name, {}) if isinstance(drift_map, dict) else {}
            status = d_info.get("status", "")
            if status == "roadmap_stale":
                drift_str = f"[{LIME}]✓ stale-resolved[/]"
            elif status == "in_sync":
                drift_str = f"[{LIME}]✓ in_sync[/]"
            else:
                drift_str = f"[{DIM}]—[/]"

        deadline_str = (
            f"[{TEAL}]{_trunc(deadline, 14)}[/]" if deadline
            else f"[{DIM}]—[/]"
        )

        table.add_row(
            f"{sev_badge} [{sev_color}]{_trunc(name, 36)}[/]",
            f"[{DIM}]{_trunc(stato, 50)}[/]",
            deadline_str,
            drift_str,
        )


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKS — 4 col: Blocco · Owner · D+ · Severity badge
# ══════════════════════════════════════════════════════════════════════════════

def populate_blocks_table(table: DataTable) -> None:
    """Popola la tabella Blocchi. Append 👻 a fantasma. D+ = da_quanto_days."""
    table.clear()
    try:
        blocks = read_blocks() or []
    except Exception:
        _error_row(table, 4, "blocks")
        return

    if not blocks:
        _empty_row(table, 4)
        return

    sorted_blocks = sorted(
        blocks,
        key=lambda b: (
            severity_rank(b.get("severity", "info")),
            -(b.get("da_quanto_days") or 0),
        ),
    )

    for block in sorted_blocks:
        sev = block.get("severity", "info")
        sev_color = _SEV_COLOR.get(sev, DIM)
        sev_badge = _SEV_BADGE.get(sev, _SEV_BADGE["info"])
        sev_label = _SEV_LABEL.get(sev, "· info")
        name = block.get("name", "?")
        owner = block.get("owner", "") or "—"
        days = block.get("da_quanto_days") or 0
        is_ghost = bool(block.get("is_ghost", False))

        # Ghost suffix + dim
        if is_ghost:
            name_str = f"[{DIM}]{_trunc(name, 32)} 👻[/]"
        else:
            name_str = f"[{sev_color}]{_trunc(name, 36)}[/]"

        d_plus_str = (
            f"[{sev_color}]D+{days}[/]" if days > 0
            else f"[{DIM}]D+?[/]"
        )

        table.add_row(
            name_str,
            f"[{DIM}]{_trunc(owner, 14)}[/]",
            d_plus_str,
            f"{sev_badge} {sev_label}",
        )


__all__ = [
    "populate_outstanding_table",
    "populate_traps_table",
    "populate_filaments_table",
    "populate_blocks_table",
]
