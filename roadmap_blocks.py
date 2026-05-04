"""roadmap_blocks.py — Blocchi Viventi parser + Rich renderer.

Self-contained (no Textual import). Reads the ## Blocchi Viventi table from
roadmap_q2_2026.md, classifies severity, computes D+ counters, audits drift
against recent session files, and renders a Rich markup string ready to embed
in M5-Watcher KPI tab or any Rich Console.

Usage:
    python3 roadmap_blocks.py          # stress-test: parse + dump + render
    python3 roadmap_blocks.py --json   # output raw JSON
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# Round 5: palette + path centralizzati in roadmap_common
from roadmap_common import (
    RED, ORANGE, LIME, DIM, TEAL,
    VAULT_BASE as _VAULT,
    ROADMAP_Q2 as _ROADMAP,
    SESSIONI_DIR as _SESSIONI,
    today_date,
)
WHITE = "#ffffff"
GOLD  = "#ffd700"

TODAY = today_date()   # ground truth, override-able via M5_TODAY_OVERRIDE

# ── Cache ──────────────────────────────────────────────────────────────────────
_CACHE_TTL = 60.0
_cache: dict | None = None
_cache_ts: float = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Duration parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_duration_days(raw: str) -> int:
    """Convert Italian duration string to days (lower bound).

    Examples:
        '>6 sett'  → 42
        '>4 sett'  → 28
        '>3 sett'  → 21
        '>2 sett'  → 14
        '2 sett'   → 14
        '1 sett'   → 7
        'cronico'  → 999
        '>1 mese'  → 30
    """
    s = raw.strip().lower()

    if s in ("cronico", "chronic", "cronica"):
        return 999

    # strip leading > or ~
    s = re.sub(r"^[>~≥]+\s*", "", s)

    # mese/mesi
    m = re.search(r"(\d+(?:\.\d+)?)\s*mes[ei]", s)
    if m:
        return int(float(m.group(1)) * 30)

    # settimane (sett / week / w)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:sett(?:imane?)?|week|w)", s)
    if m:
        return int(float(m.group(1)) * 7)

    # giorni
    m = re.search(r"(\d+)\s*(?:giorni?|days?|d)", s)
    if m:
        return int(m.group(1))

    # bare number → treat as days
    m = re.match(r"^(\d+)$", s)
    if m:
        return int(m.group(1))

    return 0


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Italian date parser (for D+ counter in Sblocco field)
# ══════════════════════════════════════════════════════════════════════════════

_IT_MONTHS = {
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
    "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
}


def _parse_it_date(text: str) -> Optional[date]:
    """Extract first Italian date from text, e.g. '1 Apr', '5 Mag 2026'."""
    pattern = re.compile(
        r"(\d{1,2})\s+([A-Za-z]{3})"
        r"(?:\s+(\d{4}))?",
    )
    for m in pattern.finditer(text):
        day_s, mon_s, yr_s = m.groups()
        mon = _IT_MONTHS.get(mon_s.lower()[:3])
        if mon is None:
            continue
        day = int(day_s)
        year = int(yr_s) if yr_s else TODAY.year
        # if parsed month is before today's month, assume next year
        if date(year, mon, day) < TODAY and not yr_s:
            year += 1
        try:
            return date(year, mon, day)
        except ValueError:
            continue
    return None


def _deadline_delta(sblocco: str) -> Optional[str]:
    """Return 'D+N' / 'D-N' / 'scaduto N' string if an Italian date is in sblocco."""
    d = _parse_it_date(sblocco)
    if d is None:
        return None
    delta = (d - TODAY).days
    if delta > 0:
        return f"D+{delta}"
    if delta == 0:
        return "OGGI"
    return f"scaduto {abs(delta)}gg"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Severity classifier
# ══════════════════════════════════════════════════════════════════════════════

def _classify_severity(raw: str, days: int) -> str:
    """
    cronico or >6 sett  → P0
    >4 sett or >3 sett  → P1
    >2 sett or 2 sett   → info
    <2 sett / recent    → info-lime
    """
    s = raw.strip().lower()
    if s in ("cronico", "cronica") or days >= 42:
        return "P0"
    if days >= 21:
        return "P1"
    if days >= 14:
        return "info"
    if days > 0:
        return "info-lime"
    return "info"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Markdown table parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_blocchi_table(text: str) -> list[dict]:
    """Extract rows from the ## Blocchi Viventi markdown table.

    Robust: skips separator lines, handles variable whitespace.
    Expected columns: Blocco | Da quanto | Energia bloccata | Sblocco | Owner
    """
    # Find the Blocchi Viventi section
    section_m = re.search(r"##\s+Blocchi\s+Viventi\b", text, re.IGNORECASE)
    if not section_m:
        return []

    section_start = section_m.start()
    # Find the next ## heading after the section
    next_section = re.search(r"\n##\s+", text[section_start + 1:])
    if next_section:
        section_text = text[section_start: section_start + 1 + next_section.start()]
    else:
        section_text = text[section_start:]

    rows = []
    header_found = False

    for line in section_text.splitlines():
        # Skip separator lines (|---|---|...)
        if re.match(r"^\|[\s\-:|]+\|", line):
            header_found = True
            continue

        if not line.startswith("|"):
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue

        # First line after separator row is data
        if not header_found:
            # still looking for separator
            continue

        # Skip if this looks like a header row
        first = cells[0].lower()
        if first in ("blocco", "block", "name"):
            continue

        # Require at least Blocco + Da quanto + Energia
        if not cells[0]:
            continue

        name        = cells[0].strip()
        da_quanto   = cells[1].strip() if len(cells) > 1 else ""
        energia     = cells[2].strip() if len(cells) > 2 else ""
        sblocco     = cells[3].strip() if len(cells) > 3 else ""
        owner       = cells[4].strip() if len(cells) > 4 else ""

        days = _parse_duration_days(da_quanto)
        severity = _classify_severity(da_quanto, days)
        deadline = _deadline_delta(sblocco)

        rows.append(
            {
                "name":           name,
                "da_quanto_raw":  da_quanto,
                "da_quanto_days": days,
                "energia_bloccata": energia,
                "sblocco":        sblocco,
                "owner":          owner,
                "severity":       severity,
                "deadline":       deadline,        # e.g. 'D+33' or None
            }
        )

    return rows


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Completeness + escalation audits
# ══════════════════════════════════════════════════════════════════════════════

def _audit_block(block: dict) -> list[str]:
    """Return list of warning strings for a single block."""
    warnings = []

    # Missing sblocco
    if not block["sblocco"] or block["sblocco"] in ("-", "—", "N/A", "?"):
        warnings.append("sblocco mancante — blocco senza uscita definita")

    # Escalation: owner Mattia + >4 sett
    if block["owner"].lower() == "mattia" and block["da_quanto_days"] >= 28:
        warnings.append(
            f"ESCALATION: owner Mattia, bloccato da {block['da_quanto_days']}gg — "
            "re-routing energia o decision point necessario"
        )

    return warnings


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Session drift audit
# ══════════════════════════════════════════════════════════════════════════════

def _load_recent_session_text(n_sessions: int = 5) -> str:
    """Concatenate text of the N most recent session files for drift detection."""
    if not _SESSIONI.exists():
        return ""

    files = sorted(
        _SESSIONI.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:n_sessions]

    parts = []
    for f in files:
        try:
            parts.append(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            pass
    return "\n".join(parts)


def _drift_audit(blocks: list[dict], session_text: str) -> list[dict]:
    """
    Ghost detection: block present in roadmap but ZERO keyword hits in recent
    sessions → 'blocco fantasma' flag.

    Returns list of dicts:  {name, hit_count, is_ghost}
    """
    results = []
    session_lower = session_text.lower()

    for block in blocks:
        # Build a set of keywords from the block name (split on spaces, strip punctuation)
        raw_words = re.sub(r"[~€\./\-]", " ", block["name"].lower()).split()
        keywords = [w for w in raw_words if len(w) >= 4]

        if not keywords:
            results.append({"name": block["name"], "hit_count": 0, "is_ghost": False})
            continue

        # Count how many keywords appear in session text
        hits = sum(1 for kw in keywords if kw in session_lower)
        is_ghost = hits == 0 and block["da_quanto_days"] > 7

        results.append(
            {
                "name":      block["name"],
                "hit_count": hits,
                "is_ghost":  is_ghost,
            }
        )

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Public API: read_blocks()
# ══════════════════════════════════════════════════════════════════════════════

def read_blocks(force_refresh: bool = False) -> list[dict]:
    """Parse roadmap and return enriched block dicts. Cached for CACHE_TTL seconds."""
    global _cache, _cache_ts

    now = time.monotonic()
    if not force_refresh and _cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    try:
        text = _ROADMAP.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []

    blocks = _parse_blocchi_table(text)

    # Attach warnings
    for b in blocks:
        b["warnings"] = _audit_block(b)

    # Drift audit against recent sessions
    session_text = _load_recent_session_text(n_sessions=5)
    drift_results = _drift_audit(blocks, session_text)
    drift_map = {d["name"]: d for d in drift_results}

    for b in blocks:
        dr = drift_map.get(b["name"], {})
        b["drift_hits"]  = dr.get("hit_count", 0)
        b["is_ghost"]    = dr.get("is_ghost", False)

    _cache    = blocks
    _cache_ts = now
    return blocks


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Severity sort + icon helpers
# ══════════════════════════════════════════════════════════════════════════════

_SEV_ORDER = {"P0": 0, "P1": 1, "info": 2, "info-lime": 3}


def _severity_icon(sev: str) -> str:
    return {
        "P0":        "🔴",
        "P1":        "🟡",
        "info":      "·",
        "info-lime": "·",
    }.get(sev, "·")


def _severity_color(sev: str) -> str:
    return {
        "P0":        RED,
        "P1":        ORANGE,
        "info":      DIM,
        "info-lime": LIME,
    }.get(sev, DIM)


def _severity_label(sev: str) -> str:
    return {
        "P0":        "P0",
        "P1":        "P1",
        "info":      "INFO",
        "info-lime": "LIME",
    }.get(sev, "?")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — render_blocks_section()
# ══════════════════════════════════════════════════════════════════════════════

def render_blocks_section(blocks: list[dict] | None = None) -> str:
    """Return Rich markup string for the Blocchi Viventi section.

    Ready to feed into: rich.console.Console().print(Markup(render_blocks_section()))
    or Textual Static widget with markup=True.
    """
    if blocks is None:
        blocks = read_blocks()

    if not blocks:
        return f"[{DIM}]nessun blocco trovato — verificare path roadmap[/]"

    # Sort: P0 first, then P1, then info
    sorted_blocks = sorted(blocks, key=lambda b: _SEV_ORDER.get(b["severity"], 99))

    total = len(sorted_blocks)
    owner_mattia = sum(1 for b in sorted_blocks if b["owner"].lower() == "mattia")
    ghosts = sum(1 for b in sorted_blocks if b.get("is_ghost"))

    # Build header
    ghost_suffix = f" · [{ORANGE}]{ghosts} fantasmi[/]" if ghosts else ""
    header = (
        f"[bold {RED}]━━━ 🚧 BLOCCHI VIVENTI "
        f"({total} · {owner_mattia} owner Mattia · principio 5)"
        f"{ghost_suffix} ━━━━━━━━━━[/]"
    )

    lines = [header]

    for b in sorted_blocks:
        sev   = b["severity"]
        color = _severity_color(sev)
        icon  = _severity_icon(sev)

        name    = b["name"]
        dq      = b["da_quanto_raw"]
        energia = b["energia_bloccata"]
        sblocco = b["sblocco"]
        owner   = b["owner"]
        deadline = b.get("deadline")

        # Build deadline string
        dl_str = f" ({deadline})" if deadline else ""

        # Ghost flag
        ghost_flag = " [blink]👻 fantasma[/blink]" if b.get("is_ghost") else ""

        # Escalation / warning inline flag
        esc_flag = ""
        for w in b.get("warnings", []):
            if "ESCALATION" in w:
                esc_flag = f" [{ORANGE}]⚠[/]"
                break

        # Line format varies by severity
        if sev == "P0":
            line = (
                f"[{color}]{icon} {name}[/]"
                f" · [{DIM}]{dq}[/]"
                f" · {energia}"
                f" · [{TEAL}]{sblocco}[/]{dl_str}"
                f"{esc_flag}{ghost_flag}"
            )
        elif sev == "P1":
            line = (
                f"[{color}]{icon} {name}[/]"
                f" · [{DIM}]{dq}[/]"
                f" · {energia}"
                f" · [{TEAL}]{sblocco}[/]{dl_str}"
                f"{esc_flag}{ghost_flag}"
            )
        else:
            # info / info-lime: compact, dimmed
            sblocco_part = f" · {sblocco}" if sblocco else ""
            dl_part = dl_str
            line = (
                f"[{color}]{icon} {name}"
                f" · {dq}"
                f" · {energia}"
                f"{sblocco_part}{dl_part}[/]"
                f"{ghost_flag}"
            )

        lines.append(line)

    # Escalation summary footer (Mattia P0/P1 blocks)
    escalations = [
        b for b in sorted_blocks
        if b["owner"].lower() == "mattia" and b["da_quanto_days"] >= 28
    ]
    if escalations:
        lines.append("")
        lines.append(
            f"[{ORANGE}]⚠ {len(escalations)} blocco/i Mattia >4 sett → decision point[/]"
        )

    # Ghost summary footer
    ghost_blocks = [b for b in sorted_blocks if b.get("is_ghost")]
    if ghost_blocks:
        names = ", ".join(b["name"] for b in ghost_blocks)
        lines.append(
            f"[{DIM}]👻 fantasmi (0 hit sessioni recenti): {names}[/]"
        )

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — Stress test  (python3 roadmap_blocks.py)
# ══════════════════════════════════════════════════════════════════════════════

def _breakdown_by_severity(blocks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for b in blocks:
        s = b["severity"]
        counts[s] = counts.get(s, 0) + 1
    return counts


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Blocchi Viventi stress test")
    ap.add_argument("--json", action="store_true", help="Output raw JSON")
    args = ap.parse_args()

    print(f"TODAY = {TODAY}")
    print(f"ROADMAP = {_ROADMAP}")
    print()

    t0 = time.monotonic()
    blocks = read_blocks(force_refresh=True)
    elapsed = (time.monotonic() - t0) * 1000

    if not blocks:
        print("ERRORE: nessun blocco trovato. Verificare path roadmap.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(blocks, ensure_ascii=False, indent=2))
        sys.exit(0)

    # ── Parse report ──────────────────────────────────────────────────────────
    breakdown = _breakdown_by_severity(blocks)
    owner_counts: dict[str, int] = {}
    for b in blocks:
        owner_counts[b["owner"]] = owner_counts.get(b["owner"], 0) + 1

    ghosts   = [b for b in blocks if b.get("is_ghost")]
    warnings = [(b["name"], w) for b in blocks for w in b.get("warnings", [])]

    print("=" * 70)
    print("PARSE REPORT")
    print("=" * 70)
    print(f"  Blocchi totali   : {len(blocks)}")
    print(f"  Tempo parse      : {elapsed:.1f}ms")
    print()
    print("  Breakdown severity:")
    for sev in ("P0", "P1", "info", "info-lime"):
        n = breakdown.get(sev, 0)
        bar = "#" * n
        print(f"    {sev:<12} {n:>2}  {bar}")
    print()
    print("  Owner breakdown:")
    for owner, n in sorted(owner_counts.items(), key=lambda x: -x[1]):
        print(f"    {owner:<12} {n}")
    print()

    if ghosts:
        print("  DRIFT — Blocchi fantasma (0 menzioni in sessioni recenti):")
        for g in ghosts:
            print(f"    - {g['name']}  (da_quanto: {g['da_quanto_raw']})")
        print()
    else:
        print("  Drift: nessun blocco fantasma rilevato")
        print()

    if warnings:
        print("  Warnings:")
        for name, w in warnings:
            print(f"    [{name}] {w}")
        print()
    else:
        print("  Warnings: nessuno")
        print()

    # ── Block dump ────────────────────────────────────────────────────────────
    print("=" * 70)
    print("BLOCK DUMP")
    print("=" * 70)
    for b in blocks:
        print(f"  [{b['severity']}] {b['name']}")
        print(f"         da_quanto : {b['da_quanto_raw']} ({b['da_quanto_days']}gg)")
        print(f"         energia   : {b['energia_bloccata']}")
        print(f"         sblocco   : {b['sblocco']}{' (' + b['deadline'] + ')' if b.get('deadline') else ''}")
        print(f"         owner     : {b['owner']}")
        print(f"         drift_hits: {b['drift_hits']}  ghost={b['is_ghost']}")
        if b.get("warnings"):
            for w in b["warnings"]:
                print(f"         ⚠  {w}")
        print()

    # ── Rich render ───────────────────────────────────────────────────────────
    print("=" * 70)
    print("RICH RENDER OUTPUT (raw markup — no Rich installed check)")
    print("=" * 70)
    markup = render_blocks_section(blocks)
    print(markup)
    print()

    # Try pretty Rich render if available
    try:
        from rich.console import Console
        from rich.markup import Markup

        print("=" * 70)
        print("RICH RENDER (rendered)")
        print("=" * 70)
        console = Console()
        console.print(Markup(markup))
    except ImportError:
        print("(rich not available — install with: pip install rich)")

    print()
    print("Stress test PASS.")
