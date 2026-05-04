"""roadmap_outstanding.py — Outstanding aging dynamic parser + Rich renderer.

Sess.1534 round 6 — sister module di roadmap_blocks.py:
- Parse "Breakdown Outstanding" tabella + frontmatter outstanding_note
- D+N aging dinamico per cliente (data fattura / sollecito / silenzio)
- Severity P0/P1/info adattiva
- Mismatch detection: somma entries vs frontmatter outstanding (warn >€500)
- Cache TTL 60s
- Self-contained (no Textual), solo stdlib + Rich markup

Cicatrici onorate:
- sess.1224: mai dato statico passato per dinamico → fallback 'D+? (data ignota)' senza crash
- sess.1058 FiscoZen audit: outstanding ground truth = frontmatter, NON solo tabella

Public API:
    read_outstanding() -> list[dict]
    render_outstanding_section() -> str

Usage:
    python3 roadmap_outstanding.py          # stress-test
    python3 roadmap_outstanding.py --json   # raw JSON
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

from roadmap_common import (
    RED, ORANGE, LIME, DIM, TEAL,
    KPI_FILE,
    today_date,
)

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE_TTL = 60.0
_cache: list[dict] | None = None
_cache_ts: float = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Date helpers (Italian months + heuristic D+N extraction)
# ══════════════════════════════════════════════════════════════════════════════

_IT_MONTHS = {
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
    "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
}


def _parse_it_date(text: str, today: date) -> Optional[date]:
    """Estrae prima data italiana 'DD Mes [YYYY]' dal testo.

    Year inference: se mese parsato è > today.month → anno corrente,
    altrimenti se è ben prima dell'oggi assume stesso anno (no rollback).
    """
    pattern = re.compile(
        r"\b(\d{1,2})\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?",
    )
    for m in pattern.finditer(text):
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

    Strategia (in ordine, restituisce il PIÙ ALTO valore plausibile):
    1. Pattern esplicito 'D+N' o 'D-N' o '(D+N)' → match diretto
    2. Pattern 'silent Nm' (N mesi) → N*30
    3. Pattern 'silent Nd' / 'NN gg' → N
    4. Pattern data italiana 'DD Mes [YYYY]' → today - parsed_date
    5. None se nessun pattern trovato

    Range plausibilità: 0..730 giorni (2 anni). Outlier scartati.
    """
    candidates: list[int] = []

    # 1. Pattern D+N esplicito
    for m in re.finditer(r"D\+(\d+)", note):
        n = int(m.group(1))
        if 0 <= n <= 730:
            candidates.append(n)

    # 2. silent Nm (mesi)
    for m in re.finditer(r"silent\s+(\d+)\s*m\b", note, re.IGNORECASE):
        n = int(m.group(1)) * 30
        if n <= 730:
            candidates.append(n)

    # 3. silent Nd / Nd / N gg
    for m in re.finditer(r"silent\s+(\d+)\s*(?:d|gg|giorni)\b", note, re.IGNORECASE):
        n = int(m.group(1))
        if 0 <= n <= 730:
            candidates.append(n)

    # 4. Data italiana → delta da today
    parsed = _parse_it_date(note, today)
    if parsed is not None:
        delta = (today - parsed).days
        if 0 <= delta <= 730:
            candidates.append(delta)

    if not candidates:
        return None
    return max(candidates)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Severity classifier
# ══════════════════════════════════════════════════════════════════════════════

def _classify_severity(days_aged: Optional[int]) -> str:
    """
    None       → info  (data ignota, no escalation forzata)
    >= 30      → P0    (churn risk / silent debt)
    7..29      → P1    (sollecito attivo)
    < 7        → info  (recente, monitor only)
    """
    if days_aged is None:
        return "info"
    if days_aged >= 30:
        return "P0"
    if days_aged >= 7:
        return "P1"
    return "info"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Frontmatter parser (outstanding_note + outstanding total)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_frontmatter(text: str) -> dict:
    """Estrae frontmatter YAML (semplice key:value, no nested) da KPI.md."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    fm = m.group(1)

    out: dict = {}

    # outstanding (int)
    km = re.search(r"^outstanding:\s*(\d+)\s*$", fm, re.MULTILINE)
    if km:
        try:
            out["outstanding"] = int(km.group(1))
        except ValueError:
            pass

    # outstanding_note (string, può essere multi-line via quote)
    nm = re.search(r'^outstanding_note:\s*"(.+?)"\s*$', fm, re.MULTILINE | re.DOTALL)
    if nm:
        out["outstanding_note"] = nm.group(1)

    return out


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Tabella "Breakdown Outstanding" parser
# ══════════════════════════════════════════════════════════════════════════════

def _amount_to_int(raw: str) -> Optional[int]:
    """Converte '€2,000' / '€1.159' / '€1,155' → int.

    Heuristic: separator (',' o '.') è migliaia se ci sono ≤3 cifre dopo,
    decimale se ci sono >3 cifre dopo o nessun separator.
    """
    s = raw.strip()
    s = s.replace("€", "").replace(" ", "").replace("*", "")
    if not s:
        return None

    # Rimuovi tutti i separatori (in EUR sia ',' sia '.' sono migliaia per
    # importi outstanding tipici €100-€99.999, no decimali)
    s = re.sub(r"[.,]", "", s)
    try:
        return int(s)
    except ValueError:
        return None


def _is_struck_through(line: str, name: str) -> bool:
    """True se il nome cliente nella riga ha tilde markdown ~~name~~ → escluso."""
    # cerca ~~ attorno al nome
    pattern = re.escape(name)
    return bool(re.search(rf"~~\s*{pattern}", line)) or "~~" in line[:line.find(name) + 1]


def _parse_outstanding_table(text: str) -> list[dict]:
    """Estrae righe attive dalla sezione '## Breakdown Outstanding'.

    Skip:
    - righe ~~Cliente~~ (struck-through = pagato/perso/churn)
    - riga 'Totale attivo' (è il totale, non un cliente)
    - separator |---|---|

    Returns lista raw senza days_aged calc (calcolato in SECTION 5).
    """
    section_m = re.search(
        r"##\s+Breakdown\s+Outstanding\b",
        text,
        re.IGNORECASE,
    )
    if not section_m:
        return []

    section_start = section_m.start()
    next_section = re.search(r"\n##\s+", text[section_start + 1:])
    if next_section:
        section_text = text[section_start: section_start + 1 + next_section.start()]
    else:
        section_text = text[section_start:]

    rows = []
    header_seen = False

    for line in section_text.splitlines():
        # separator line
        if re.match(r"^\|[\s\-:|]+\|", line):
            header_seen = True
            continue

        if not line.startswith("|"):
            continue

        if not header_seen:
            continue

        # Skip se la riga intera è struck-through (~~ riga ~~ pagato/perso)
        # Tipico: | ~~LuxGuard~~ | ~~€1,500~~ | **PAGATO ...** ✅
        # Detection robusta: se ci sono >=2 ~~ nella riga è strikethrough
        if line.count("~~") >= 2:
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue

        # Skip header row
        first = cells[0].lower().strip("*").strip()
        if first in ("cliente", "client", "name"):
            continue

        # Skip totale row (es. "**Totale attivo**")
        if "totale" in first:
            continue

        if not cells[0]:
            continue

        # Pulisci nome (rimuovi grassetto markdown)
        name = re.sub(r"\*\*", "", cells[0]).strip()
        amount_raw = cells[1] if len(cells) > 1 else ""
        note = cells[2] if len(cells) > 2 else ""

        amount = _amount_to_int(amount_raw)
        if amount is None or amount <= 0:
            continue

        rows.append({
            "cliente": name,
            "amount": amount,
            "note": note,
        })

    return rows


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Cross-source aging (note in tabella + outstanding_note frontmatter)
# ══════════════════════════════════════════════════════════════════════════════

def _enrich_with_aging(
    rows: list[dict],
    outstanding_note: str,
    today: date,
) -> list[dict]:
    """Per ogni cliente, aggrega aging da:
    1. note in tabella (col più recente '🔴 D+N')
    2. outstanding_note frontmatter (es. 'TimeGate #21 €1.159 (D+27, churn risk)')
    3. data fattura / silent in note (heuristic)

    Strategy: prendi il MASSIMO D+N tra le due fonti (più conservativo per
    severity escalation). Se entrambe None → days_aged=None, severity=info.
    """
    enriched = []
    note_lower = outstanding_note.lower() if outstanding_note else ""

    for row in rows:
        cliente = row["cliente"]
        table_note = row["note"]

        # 1. Prova D+N dalla note tabella
        days_a = _extract_days_aged(table_note, today)

        # 2. Cerca menzione cliente in outstanding_note frontmatter
        #    Estrai keyword principale (prima parola del nome, no parentesi)
        first_word = re.split(r"[\s/(]", cliente)[0].lower().strip()
        days_b = None
        if outstanding_note and first_word and len(first_word) >= 3:
            # Trova snippet attorno al nome (50 chars context window)
            for m in re.finditer(re.escape(first_word), note_lower):
                start = max(0, m.start() - 10)
                end = min(len(outstanding_note), m.end() + 80)
                snippet = outstanding_note[start:end]
                d = _extract_days_aged(snippet, today)
                if d is not None and (days_b is None or d > days_b):
                    days_b = d

        # Aggrega: max dei due (None-safe)
        candidates = [d for d in (days_a, days_b) if d is not None]
        days_aged = max(candidates) if candidates else None

        severity = _classify_severity(days_aged)

        # Next action heuristic da note (estrai prima azione esplicita)
        next_action = None
        if table_note:
            # cerca pattern azionabili
            for kw in ("sollecito", "WA", "Sissi", "bonifico", "reach", "pranzo",
                      "fattura", "PDF", "draft"):
                if kw.lower() in table_note.lower():
                    # estrai 40 char context
                    idx = table_note.lower().find(kw.lower())
                    snippet = table_note[idx: idx + 50].split("·")[0].split("—")[0]
                    next_action = snippet.strip().rstrip(",.;").strip()
                    break

        enriched.append({
            "cliente":    cliente,
            "amount":     row["amount"],
            "days_aged":  days_aged,
            "severity":   severity,
            "note":       table_note,
            "next_action": next_action,
        })

    return enriched


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Public API: read_outstanding()
# ══════════════════════════════════════════════════════════════════════════════

def read_outstanding(force_refresh: bool = False) -> list[dict]:
    """Ritorna lista entries outstanding con aging + severity.

    Schema entry:
        {
            'cliente':     str,
            'amount':      int,
            'days_aged':   int | None,
            'severity':    'P0' | 'P1' | 'info',
            'note':        str,
            'next_action': str | None,
        }

    Cached per CACHE_TTL secondi. Graceful: KPI.md mancante → [].
    """
    global _cache, _cache_ts

    now = time.monotonic()
    if not force_refresh and _cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache

    try:
        text = KPI_FILE.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        _cache = []
        _cache_ts = now
        return []

    fm = _parse_frontmatter(text)
    outstanding_note = fm.get("outstanding_note", "")

    rows = _parse_outstanding_table(text)
    today = today_date()
    enriched = _enrich_with_aging(rows, outstanding_note, today)

    _cache = enriched
    _cache_ts = now
    return enriched


def read_frontmatter_outstanding() -> Optional[int]:
    """Ritorna outstanding totale dichiarato in frontmatter (ground truth aggregato)."""
    try:
        text = KPI_FILE.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return None
    fm = _parse_frontmatter(text)
    return fm.get("outstanding")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Severity helpers
# ══════════════════════════════════════════════════════════════════════════════

_SEV_ORDER = {"P0": 0, "P1": 1, "info": 2}


def _severity_color(sev: str) -> str:
    return {"P0": RED, "P1": ORANGE, "info": DIM}.get(sev, DIM)


def _severity_icon(sev: str) -> str:
    return {"P0": "🔴", "P1": "🟡", "info": "·"}.get(sev, "·")


def _format_days(days: Optional[int]) -> str:
    if days is None:
        return "D+? (data ignota)"
    return f"D+{days}"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — render_outstanding_section()
# ══════════════════════════════════════════════════════════════════════════════

def render_outstanding_section(entries: list[dict] | None = None) -> str:
    """Rich markup section, sorted by severity (P0 first) then days_aged desc.

    Header include count totale, somma €, count P0, count P1.
    Footer warning se mismatch >€500 vs frontmatter.
    """
    if entries is None:
        entries = read_outstanding()

    if not entries:
        return f"[{DIM}]nessun outstanding trovato — verificare KPI.md[/]"

    # Sort: severity asc, then days_aged desc (None last within group)
    def _sort_key(e):
        sev = _SEV_ORDER.get(e["severity"], 99)
        days = e["days_aged"] if e["days_aged"] is not None else -1
        return (sev, -days)

    sorted_entries = sorted(entries, key=_sort_key)

    total = len(sorted_entries)
    total_amount = sum(e["amount"] for e in sorted_entries)
    p0_count = sum(1 for e in sorted_entries if e["severity"] == "P0")
    p1_count = sum(1 for e in sorted_entries if e["severity"] == "P1")

    # Mismatch detection vs frontmatter
    fm_total = read_frontmatter_outstanding()
    mismatch_warn = ""
    if fm_total is not None and abs(total_amount - fm_total) > 500:
        delta = total_amount - fm_total
        sign = "+" if delta > 0 else ""
        mismatch_warn = f" · [{ORANGE}]⚠ mismatch {sign}€{delta} vs frontmatter €{fm_total}[/]"

    # Format total amount con separator migliaia '.'
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
        icon = _severity_icon(sev)
        cliente = e["cliente"]
        amount = e["amount"]
        days_str = _format_days(e["days_aged"])
        note = e["note"]

        # Truncate note se troppo lunga (>80 char)
        note_short = note if len(note) <= 80 else note[:77] + "..."

        # Format amount con separator
        amt_str = f"{amount:,}".replace(",", ".")

        if sev == "P0":
            line = (
                f"[bold {color}]{icon} {cliente}[/] · "
                f"€{amt_str} · "
                f"[bold {color}]{days_str}[/] · "
                f"[{DIM}]{note_short}[/]"
            )
        elif sev == "P1":
            line = (
                f"[bold {color}]{icon} {cliente}[/] · "
                f"€{amt_str} · "
                f"[bold {color}]{days_str}[/] · "
                f"[{DIM}]{note_short}[/]"
            )
        else:
            # info: tutto dimmed in singolo blocco
            line = (
                f"[{DIM}]{icon} {cliente} · "
                f"€{amt_str} · "
                f"{days_str} · "
                f"{note_short}[/]"
            )

        lines.append(line)

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Stress test
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Outstanding aging stress test")
    ap.add_argument("--json", action="store_true", help="Output raw JSON")
    args = ap.parse_args()

    today = today_date()
    print(f"TODAY    = {today}")
    print(f"KPI_FILE = {KPI_FILE}")
    print()

    t0 = time.monotonic()
    entries = read_outstanding(force_refresh=True)
    elapsed = (time.monotonic() - t0) * 1000

    fm_total = read_frontmatter_outstanding()

    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2, default=str))
        sys.exit(0)

    if not entries:
        print("ERRORE: nessun outstanding trovato. Verificare KPI.md.", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print("PARSE REPORT")
    print("=" * 70)
    print(f"  Outstanding totali  : {len(entries)}")
    print(f"  Tempo parse         : {elapsed:.1f}ms")
    print(f"  Frontmatter total   : €{fm_total}")
    sum_entries = sum(e["amount"] for e in entries)
    print(f"  Sum entries         : €{sum_entries}")
    delta = sum_entries - (fm_total or 0)
    if fm_total is not None:
        print(f"  Delta sum vs fm     : €{delta}{' ⚠' if abs(delta) > 500 else ' ✓'}")
    print()

    breakdown: dict[str, int] = {}
    for e in entries:
        s = e["severity"]
        breakdown[s] = breakdown.get(s, 0) + 1

    print("  Severity breakdown:")
    for sev in ("P0", "P1", "info"):
        n = breakdown.get(sev, 0)
        bar = "#" * n
        print(f"    {sev:<6} {n:>2}  {bar}")
    print()

    print("=" * 70)
    print("ENTRY DUMP")
    print("=" * 70)
    for e in entries:
        days = e["days_aged"]
        days_disp = f"D+{days}" if days is not None else "D+?"
        print(f"  [{e['severity']:<4}] {e['cliente']:<25} €{e['amount']:>5}  {days_disp}")
        print(f"         note  : {e['note']}")
        if e["next_action"]:
            print(f"         action: {e['next_action']}")
        print()

    print("=" * 70)
    print("RICH RENDER OUTPUT (raw markup)")
    print("=" * 70)
    markup = render_outstanding_section(entries)
    print(markup)
    print()

    try:
        from rich.console import Console
        from rich.markup import Markup
        print("=" * 70)
        print("RICH RENDER (rendered)")
        print("=" * 70)
        Console().print(Markup(markup))
    except ImportError:
        print("(rich not available)")

    print()
    print("Stress test PASS.")
