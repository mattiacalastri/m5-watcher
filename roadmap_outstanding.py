"""roadmap_outstanding — Outstanding aging dynamic parser + Rich renderer.

Parse "Breakdown Outstanding" tabella + frontmatter outstanding_note.
D+N aging dinamico per cliente, severity P0/P1/info adattiva, mismatch
detection vs frontmatter (warn >€500). Cache TTL 60s, self-contained.

Public API:
    read_outstanding(force_refresh=False) -> list[dict]
    render_outstanding_section(entries=None) -> str
    read_frontmatter_outstanding() -> int | None
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from roadmap_common import (
    RED, ORANGE, DIM,
    KPI_FILE,
    IT_MONTHS_SHORT as _IT_MONTHS,
    today_date,
    cached,
    read_text,
)


# ══════════════════════════════════════════════════════════════════════════════
# Hardcoded emission dates (round 7) — ground truth più affidabile del regex
# D+N che può essere stale (es. outstanding_note dichiara D+27 ma sono 29).
# ══════════════════════════════════════════════════════════════════════════════

EMISSION_DATES: dict[str, date] = {
    "TimeGate": date(2026, 4, 5),  # Fattura #20/2026 emessa 5 Apr 2026
    # Maglificio: silent 14m → fallback al regex (no emission_date nota)
}


def _calculate_days_from_emission(cliente: str, today: date) -> Optional[int]:
    """Calcola days_aged da data emissione hardcoded.

    Match keyword case-insensitive: la prima parola del nome cliente che
    matcha una chiave di EMISSION_DATES restituisce il delta giorni.
    Returns None se nessun match → caller fa fallback al regex parser.
    """
    if not cliente:
        return None
    cliente_lower = cliente.lower()
    for key, emission_date in EMISSION_DATES.items():
        if key.lower() in cliente_lower:
            delta = (today - emission_date).days
            if 0 <= delta <= 730:
                return delta
            return None
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Date / aging extraction
# ══════════════════════════════════════════════════════════════════════════════

_DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?")


def _parse_it_date(text: str, today: date) -> Optional[date]:
    """Estrae prima data italiana 'DD Mes [YYYY]' dal testo."""
    for m in _DATE_RE.finditer(text):
        day_s, mon_s, yr_s = m.groups()
        mon = _IT_MONTHS.get(mon_s.lower()[:3])
        if mon is None:
            continue
        try:
            day = int(day_s)
            year = int(yr_s) if yr_s else today.year
            return date(year, mon, day)
        except ValueError:
            continue
    return None


def _extract_days_aged(note: str, today: date) -> Optional[int]:
    """Estrae D+N giorni aging dalla nota.

    Strategia (restituisce il PIÙ ALTO valore plausibile in [0..730]):
    1. Pattern 'D+N' esplicito
    2. 'silent Nm' (mesi → N*30)
    3. 'silent Nd' / 'silent Ngg' / 'silent N giorni'
    4. Data italiana 'DD Mes [YYYY]' → today - parsed
    """
    candidates: list[int] = []

    for m in re.finditer(r"D\+(\d+)", note):
        n = int(m.group(1))
        if 0 <= n <= 730:
            candidates.append(n)

    for m in re.finditer(r"silent\s+(\d+)\s*m\b", note, re.IGNORECASE):
        n = int(m.group(1)) * 30
        if n <= 730:
            candidates.append(n)

    for m in re.finditer(r"silent\s+(\d+)\s*(?:d|gg|giorni)\b", note, re.IGNORECASE):
        n = int(m.group(1))
        if 0 <= n <= 730:
            candidates.append(n)

    parsed = _parse_it_date(note, today)
    if parsed is not None:
        delta = (today - parsed).days
        if 0 <= delta <= 730:
            candidates.append(delta)

    return max(candidates) if candidates else None


# ══════════════════════════════════════════════════════════════════════════════
# Severity classifier
# ══════════════════════════════════════════════════════════════════════════════

def _classify_severity(days_aged: Optional[int]) -> str:
    """None/<7 → info  ·  7..29 → P1  ·  >=30 → P0."""
    if days_aged is None:
        return "info"
    if days_aged >= 30:
        return "P0"
    if days_aged >= 7:
        return "P1"
    return "info"


# ══════════════════════════════════════════════════════════════════════════════
# Frontmatter parser (locale: outstanding + outstanding_note multi-line quoted)
# ══════════════════════════════════════════════════════════════════════════════

_FRONTMATTER_BLOCK_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    """Estrae outstanding (int) + outstanding_note (string multi-line) da KPI.md.

    Locale (non shared con roadmap_common.parse_frontmatter): outstanding_note
    può essere quoted multi-line, regex dedicato.
    """
    m = _FRONTMATTER_BLOCK_RE.match(text)
    if not m:
        return {}
    fm = m.group(1)
    out: dict = {}

    km = re.search(r"^outstanding:\s*(\d+)\s*$", fm, re.MULTILINE)
    if km:
        try:
            out["outstanding"] = int(km.group(1))
        except ValueError:
            pass

    nm = re.search(r'^outstanding_note:\s*"(.+?)"\s*$', fm, re.MULTILINE | re.DOTALL)
    if nm:
        out["outstanding_note"] = nm.group(1)

    return out


# ══════════════════════════════════════════════════════════════════════════════
# Tabella "Breakdown Outstanding" parser
# ══════════════════════════════════════════════════════════════════════════════

def _amount_to_int(raw: str) -> Optional[int]:
    """Converte '€2,000' / '€1.159' / '€1,155' → int.

    Rimuove sia ',' sia '.' (entrambi separatori migliaia per outstanding tipici
    €100-€99.999, no decimali). Spazi e '*' tollerati.
    """
    s = raw.strip().replace("€", "").replace(" ", "").replace("*", "")
    if not s:
        return None
    s = re.sub(r"[.,]", "", s)
    try:
        return int(s)
    except ValueError:
        return None


def _parse_outstanding_table(text: str) -> list[dict]:
    """Estrae righe attive dalla sezione '## Breakdown Outstanding'.

    Skip: righe ~~Cliente~~ (pagato), 'Totale attivo', separator |---|---|.
    """
    section_m = re.search(r"##\s+Breakdown\s+Outstanding\b", text, re.IGNORECASE)
    if not section_m:
        return []

    section_start = section_m.start()
    next_section = re.search(r"\n##\s+", text[section_start + 1:])
    if next_section:
        section_text = text[section_start: section_start + 1 + next_section.start()]
    else:
        section_text = text[section_start:]

    rows: list[dict] = []
    header_seen = False

    for line in section_text.splitlines():
        if re.match(r"^\|[\s\-:|]+\|", line):
            header_seen = True
            continue
        if not line.startswith("|") or not header_seen:
            continue
        # Riga interamente strikethrough (pagato/perso)
        if line.count("~~") >= 2:
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue

        first = cells[0].lower().strip("*").strip()
        if first in ("cliente", "client", "name") or "totale" in first or not cells[0]:
            continue

        name = re.sub(r"\*\*", "", cells[0]).strip()
        amount = _amount_to_int(cells[1] if len(cells) > 1 else "")
        if amount is None or amount <= 0:
            continue

        rows.append({
            "cliente": name,
            "amount": amount,
            "note": cells[2] if len(cells) > 2 else "",
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Cross-source aging
# ══════════════════════════════════════════════════════════════════════════════

_NEXT_ACTION_KEYWORDS = (
    "sollecito", "WA", "Sissi", "bonifico", "reach", "pranzo",
    "fattura", "PDF", "draft",
)


def _extract_next_action(table_note: str) -> Optional[str]:
    """Cerca prima keyword azionabile nella note, ritorna 40-50char snippet."""
    if not table_note:
        return None
    note_lower = table_note.lower()
    for kw in _NEXT_ACTION_KEYWORDS:
        idx = note_lower.find(kw.lower())
        if idx == -1:
            continue
        snippet = table_note[idx: idx + 50].split("·")[0].split("—")[0]
        return snippet.strip().rstrip(",.;").strip()
    return None


def _enrich_with_aging(
    rows: list[dict],
    outstanding_note: str,
    today: date,
) -> list[dict]:
    """Per ogni cliente, aggrega aging: emission_date hardcoded > regex tabella + nota frontmatter.

    Strategy: emission_date prevale (round 7 fix); altrimenti max delle due
    fonti regex (table_note + outstanding_note frontmatter). Severity classificata.
    """
    enriched: list[dict] = []
    note_lower = outstanding_note.lower() if outstanding_note else ""

    for row in rows:
        cliente = row["cliente"]
        table_note = row["note"]

        days_emission = _calculate_days_from_emission(cliente, today)

        # Round 7: emission_date prevale (se noto) sul regex
        if days_emission is not None:
            days_aged = days_emission
        else:
            days_a = _extract_days_aged(table_note, today)
            days_b = None
            first_word = re.split(r"[\s/(]", cliente)[0].lower().strip()
            if outstanding_note and first_word and len(first_word) >= 3:
                for m in re.finditer(re.escape(first_word), note_lower):
                    start = max(0, m.start() - 10)
                    end = min(len(outstanding_note), m.end() + 80)
                    d = _extract_days_aged(outstanding_note[start:end], today)
                    if d is not None and (days_b is None or d > days_b):
                        days_b = d
            candidates = [d for d in (days_a, days_b) if d is not None]
            days_aged = max(candidates) if candidates else None

        enriched.append({
            "cliente":     cliente,
            "amount":      row["amount"],
            "days_aged":   days_aged,
            "severity":    _classify_severity(days_aged),
            "note":        table_note,
            "next_action": _extract_next_action(table_note),
        })

    return enriched


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

@cached(ttl=60.0)
def read_outstanding() -> list[dict]:
    """Ritorna lista entries outstanding con aging + severity. Cached 60s."""
    text = read_text(KPI_FILE)
    if not text:
        return []
    fm = _parse_frontmatter(text)
    rows = _parse_outstanding_table(text)
    return _enrich_with_aging(rows, fm.get("outstanding_note", ""), today_date())


def read_frontmatter_outstanding() -> Optional[int]:
    """Ritorna outstanding totale dichiarato in frontmatter (ground truth aggregato)."""
    text = read_text(KPI_FILE)
    if not text:
        return None
    return _parse_frontmatter(text).get("outstanding")


# ══════════════════════════════════════════════════════════════════════════════
# Severity helpers + renderer
# ══════════════════════════════════════════════════════════════════════════════

_SEV_ORDER = {"P0": 0, "P1": 1, "info": 2}
_SEV_COLOR = {"P0": RED, "P1": ORANGE, "info": DIM}
_SEV_ICON  = {"P0": "🔴", "P1": "🟡", "info": "·"}


def _severity_color(sev: str) -> str:
    return _SEV_COLOR.get(sev, DIM)


def _severity_icon(sev: str) -> str:
    return _SEV_ICON.get(sev, "·")


def _format_days(days: Optional[int]) -> str:
    return f"D+{days}" if days is not None else "D+? (data ignota)"


def render_outstanding_section(entries: list[dict] | None = None) -> str:
    """Rich markup section, sorted by severity (P0 first) then days_aged desc.

    Header: count totale, somma €, count P0/P1.
    Footer warning se mismatch >€500 vs frontmatter.
    """
    if entries is None:
        entries = read_outstanding()
    if not entries:
        return f"[{DIM}]nessun outstanding trovato — verificare KPI.md[/]"

    # Sort: severity asc, then days_aged desc (None last within group)
    sorted_entries = sorted(
        entries,
        key=lambda e: (
            _SEV_ORDER.get(e["severity"], 99),
            -(e["days_aged"] if e["days_aged"] is not None else -1),
        ),
    )

    total = len(sorted_entries)
    total_amount = sum(e["amount"] for e in sorted_entries)
    p0_count = sum(1 for e in sorted_entries if e["severity"] == "P0")
    p1_count = sum(1 for e in sorted_entries if e["severity"] == "P1")

    fm_total = read_frontmatter_outstanding()
    mismatch_warn = ""
    if fm_total is not None and abs(total_amount - fm_total) > 500:
        delta = total_amount - fm_total
        sign = "+" if delta > 0 else ""
        mismatch_warn = (
            f" · [{ORANGE}]⚠ mismatch {sign}€{delta} vs frontmatter €{fm_total}[/]"
        )

    total_str = f"{total_amount:,}".replace(",", ".")
    sev_summary = []
    if p0_count:
        sev_summary.append(f"{p0_count} P0")
    if p1_count:
        sev_summary.append(f"{p1_count} P1")
    sev_str = " · " + " · ".join(sev_summary) if sev_summary else ""

    header = (
        f"[bold {RED}]━━━ 💰 OUTSTANDING "
        f"({total} · €{total_str}{sev_str})"
        f"{mismatch_warn} ━━━━━━━━━━━━━━━━━━━━━[/]"
    )

    lines = [header]
    for e in sorted_entries:
        sev = e["severity"]
        color = _severity_color(sev)
        icon  = _severity_icon(sev)
        amt_str = f"{e['amount']:,}".replace(",", ".")
        days_str = _format_days(e["days_aged"])
        note = e["note"]
        note_short = note if len(note) <= 80 else note[:77] + "..."

        if sev in ("P0", "P1"):
            line = (
                f"[bold {color}]{icon} {e['cliente']}[/] · "
                f"€{amt_str} · "
                f"[bold {color}]{days_str}[/] · "
                f"[{DIM}]{note_short}[/]"
            )
        else:
            line = (
                f"[{DIM}]{icon} {e['cliente']} · "
                f"€{amt_str} · "
                f"{days_str} · "
                f"{note_short}[/]"
            )
        lines.append(line)

    return "\n".join(lines)
