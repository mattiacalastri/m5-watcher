"""roadmap_blocks — Blocchi Viventi parser + Rich renderer.

Self-contained (no Textual import). Reads the ## Blocchi Viventi table from
roadmap_q2_2026.md, classifies severity, computes D+ counters, audits drift
against recent session files, and renders a Rich markup string.

Public API:
    read_blocks(force_refresh=False) -> list[dict]
    render_blocks_section(blocks=None) -> str
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from roadmap_common import (
    RED, ORANGE, LIME, DIM, TEAL,
    ROADMAP_Q2 as _ROADMAP,
    SESSIONI_DIR as _SESSIONI,
    IT_MONTHS_SHORT,
    today_date,
    cached,
    read_text,
    severity_color as _common_severity_color,
)

# Round 9 (sess.1534): TODAY non più frozen all'import — TUI long-running
# >24h vedeva D+N counter inchiodati pre-mezzanotte. Ora ogni call legge
# today_date() fresh. Backward-compat: TODAY resta esposto per chi importa
# il simbolo, ma punta a una property-like che ricalcola.
TODAY = today_date()  # legacy backward-compat (può essere stale post-mezzanotte)


# ══════════════════════════════════════════════════════════════════════════════
# Duration parser ("'>6 sett'", "cronico", "1 mese", ...)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_duration_days(raw: str) -> int:
    """Convert Italian duration string to days (lower bound)."""
    s = raw.strip().lower()
    if s in ("cronico", "chronic", "cronica"):
        return 999
    s = re.sub(r"^[>~≥]+\s*", "", s)

    m = re.search(r"(\d+(?:\.\d+)?)\s*mes[ei]", s)
    if m:
        return int(float(m.group(1)) * 30)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:sett(?:imane?)?|week|w)", s)
    if m:
        return int(float(m.group(1)) * 7)
    m = re.search(r"(\d+)\s*(?:giorni?|days?|d)", s)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)$", s)
    if m:
        return int(m.group(1))
    return 0


# ══════════════════════════════════════════════════════════════════════════════
# Italian date parser (per D+ counter in Sblocco field)
# ══════════════════════════════════════════════════════════════════════════════

_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})(?:\s+(\d{4}))?")


def _parse_it_date(text: str, today: Optional[date] = None) -> Optional[date]:
    """Estrae prima data italiana 'DD Mes [YYYY]' dal testo.

    Round 9: today è parametro (default = today_date() fresh) per evitare
    freeze post-mezzanotte in TUI long-running.
    """
    today = today or today_date()
    for m in _DATE_RE.finditer(text):
        day_s, mon_s, yr_s = m.groups()
        mon = IT_MONTHS_SHORT.get(mon_s.lower()[:3])
        if mon is None:
            continue
        day = int(day_s)
        year = int(yr_s) if yr_s else today.year
        if not yr_s and date(year, mon, day) < today:
            year += 1
        try:
            return date(year, mon, day)
        except ValueError:
            continue
    return None


def _deadline_delta(sblocco: str) -> Optional[str]:
    """Return 'D+N' / 'OGGI' / 'scaduto Ngg' string if a date is in sblocco."""
    today = today_date()  # round 9: fresh ad ogni call (no freeze)
    d = _parse_it_date(sblocco, today=today)
    if d is None:
        return None
    delta = (d - today).days
    if delta > 0:
        return f"D+{delta}"
    if delta == 0:
        return "OGGI"
    return f"scaduto {abs(delta)}gg"


# ══════════════════════════════════════════════════════════════════════════════
# Severity classifier
# ══════════════════════════════════════════════════════════════════════════════

def _classify_severity(raw: str, days: int) -> str:
    """cronico / >=42d → P0  ·  >=21d → P1  ·  >=14d → info  ·  >0 → info-lime."""
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
# Markdown table parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_blocchi_table(text: str) -> list[dict]:
    """Extract rows from the ## Blocchi Viventi markdown table."""
    section_m = re.search(r"##\s+Blocchi\s+Viventi\b", text, re.IGNORECASE)
    if not section_m:
        return []

    section_start = section_m.start()
    next_section = re.search(r"\n##\s+", text[section_start + 1:])
    if next_section:
        section_text = text[section_start: section_start + 1 + next_section.start()]
    else:
        section_text = text[section_start:]

    rows: list[dict] = []
    header_found = False

    for line in section_text.splitlines():
        if re.match(r"^\|[\s\-:|]+\|", line):
            header_found = True
            continue
        if not line.startswith("|"):
            continue
        if not header_found:
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue

        first = cells[0].lower()
        if first in ("blocco", "block", "name") or not cells[0]:
            continue

        name      = cells[0]
        da_quanto = cells[1] if len(cells) > 1 else ""
        energia   = cells[2] if len(cells) > 2 else ""
        sblocco   = cells[3] if len(cells) > 3 else ""
        owner     = cells[4] if len(cells) > 4 else ""

        days = _parse_duration_days(da_quanto)
        rows.append({
            "name":             name,
            "da_quanto_raw":    da_quanto,
            "da_quanto_days":   days,
            "energia_bloccata": energia,
            "sblocco":          sblocco,
            "owner":            owner,
            "severity":         _classify_severity(da_quanto, days),
            "deadline":         _deadline_delta(sblocco),
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Audits (completeness + escalation + drift)
# ══════════════════════════════════════════════════════════════════════════════

def _audit_block(block: dict) -> list[str]:
    """Warnings per single block: missing sblocco + Mattia escalation."""
    warnings: list[str] = []
    if not block["sblocco"] or block["sblocco"] in ("-", "—", "N/A", "?"):
        warnings.append("sblocco mancante — blocco senza uscita definita")
    if block["owner"].lower() == "mattia" and block["da_quanto_days"] >= 28:
        warnings.append(
            f"ESCALATION: owner Mattia, bloccato da {block['da_quanto_days']}gg — "
            "re-routing energia o decision point necessario"
        )
    return warnings


def _load_recent_session_text(n_sessions: int = 5) -> str:
    """Concatenate text of the N most recent session files for drift detection."""
    if not _SESSIONI.exists():
        return ""
    files = sorted(
        _SESSIONI.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:n_sessions]
    return "\n".join(read_text(f) for f in files)


def _drift_audit(blocks: list[dict], session_text: str) -> dict[str, dict]:
    """Ghost detection: block in roadmap but ZERO keyword hits in recent sessions."""
    session_lower = session_text.lower()
    results: dict[str, dict] = {}
    for block in blocks:
        raw_words = re.sub(r"[~€\./\-]", " ", block["name"].lower()).split()
        keywords = [w for w in raw_words if len(w) >= 4]
        if not keywords:
            results[block["name"]] = {"hit_count": 0, "is_ghost": False}
            continue
        hits = sum(1 for kw in keywords if kw in session_lower)
        results[block["name"]] = {
            "hit_count": hits,
            "is_ghost": hits == 0 and block["da_quanto_days"] > 7,
        }
    return results


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

@cached(ttl=60.0)
def read_blocks() -> list[dict]:
    """Parse roadmap and return enriched block dicts. Cached 60s."""
    text = read_text(_ROADMAP)
    if not text:
        return []

    blocks = _parse_blocchi_table(text)
    for b in blocks:
        b["warnings"] = _audit_block(b)

    drift_map = _drift_audit(blocks, _load_recent_session_text(n_sessions=5))
    for b in blocks:
        dr = drift_map.get(b["name"], {})
        b["drift_hits"] = dr.get("hit_count", 0)
        b["is_ghost"]   = dr.get("is_ghost", False)
    return blocks


# ══════════════════════════════════════════════════════════════════════════════
# Severity helpers (info-lime → LIME via roadmap_common)
# ══════════════════════════════════════════════════════════════════════════════

_SEV_ORDER = {"P0": 0, "P1": 1, "info": 2, "info-lime": 3}

_BLOCK_ICONS = {"P0": "🔴", "P1": "🟡", "info": "·", "info-lime": "·"}


def _severity_icon(sev: str) -> str:
    return _BLOCK_ICONS.get(sev, "·")


def _severity_color(sev: str) -> str:
    # Override locale: 'info' usa DIM (no warning glow), 'info-lime' usa LIME
    if sev == "info-lime":
        return LIME
    return _common_severity_color(sev)


# ══════════════════════════════════════════════════════════════════════════════
# Renderer
# ══════════════════════════════════════════════════════════════════════════════

def render_blocks_section(blocks: list[dict] | None = None) -> str:
    """Return Rich markup string for the Blocchi Viventi section."""
    if blocks is None:
        blocks = read_blocks()
    if not blocks:
        return f"[{DIM}]nessun blocco trovato — verificare path roadmap[/]"

    sorted_blocks = sorted(blocks, key=lambda b: _SEV_ORDER.get(b["severity"], 99))
    total = len(sorted_blocks)
    owner_mattia = sum(1 for b in sorted_blocks if b["owner"].lower() == "mattia")
    ghosts = sum(1 for b in sorted_blocks if b.get("is_ghost"))

    ghost_suffix = f" · [{ORANGE}]{ghosts} fantasmi[/]" if ghosts else ""
    header = (
        f"[bold {RED}]━━━ 🚧 BLOCCHI VIVENTI "
        f"({total} · {owner_mattia} owner Mattia · principio 5)"
        f"{ghost_suffix} ━━━━━━━━━━[/]"
    )

    lines = [header]
    for b in sorted_blocks:
        sev = b["severity"]
        color = _severity_color(sev)
        icon  = _severity_icon(sev)
        deadline = b.get("deadline")
        dl_str = f" ({deadline})" if deadline else ""
        ghost_flag = " [blink]👻 fantasma[/blink]" if b.get("is_ghost") else ""

        esc_flag = ""
        for w in b.get("warnings", []):
            if "ESCALATION" in w:
                esc_flag = f" [{ORANGE}]⚠[/]"
                break

        if sev in ("P0", "P1"):
            line = (
                f"[{color}]{icon} {b['name']}[/]"
                f" · [{DIM}]{b['da_quanto_raw']}[/]"
                f" · {b['energia_bloccata']}"
                f" · [{TEAL}]{b['sblocco']}[/]{dl_str}"
                f"{esc_flag}{ghost_flag}"
            )
        else:
            sblocco_part = f" · {b['sblocco']}" if b["sblocco"] else ""
            line = (
                f"[{color}]{icon} {b['name']}"
                f" · {b['da_quanto_raw']}"
                f" · {b['energia_bloccata']}"
                f"{sblocco_part}{dl_str}[/]"
                f"{ghost_flag}"
            )
        lines.append(line)

    escalations = [
        b for b in sorted_blocks
        if b["owner"].lower() == "mattia" and b["da_quanto_days"] >= 28
    ]
    if escalations:
        lines.append("")
        lines.append(
            f"[{ORANGE}]⚠ {len(escalations)} blocco/i Mattia >4 sett → decision point[/]"
        )

    ghost_blocks = [b for b in sorted_blocks if b.get("is_ghost")]
    if ghost_blocks:
        names = ", ".join(b["name"] for b in ghost_blocks)
        lines.append(f"[{DIM}]👻 fantasmi (0 hit sessioni recenti): {names}[/]")

    return "\n".join(lines)
