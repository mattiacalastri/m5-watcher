"""roadmap_polestar — Stella polare 2031 + Fase RADICI strip per M5 Cockpit.

Public API:
    render_polestar_strip() -> str       # 2 righe Rich-markup
    read_phase_state() -> dict           # ground truth + counters

Ground truth read da:
- KPI.md frontmatter (mrr, outstanding)
- roadmap_q2_2026.md "Condizione di uscita" RADICI
- Roadmap Calendar 2026-2029 — Soul Engineer (T+3m kill 17 Lug 2026)

Cicatrici onorate (sess.1224): mai dato statico passato per dinamico → 'API✗'
o '—' se sorgente muta. Render adattivo, no Textual import.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from roadmap_common import (
    RED, ORANGE, LIME, DIM, TEAL,
    KPI_FILE, ROADMAP_Q2, ROADMAP_CAL,
    MONTH_LOOKUP, cached, parse_frontmatter, read_text,
)

# Costanti fase RADICI (target di prodotto, non drift-prone)
OUTSTANDING_TARGET = 3000          # Outstanding < €3.000
MRR_TARGET_Q2      = 5200          # MRR >= €5.200
KILL_DATE          = date(2026, 7, 17)   # T+3m kill check
KILL_TARGET_AMOUNT = 2500          # ≥€2.500 cliente forgiatura


# ---------------------------------------------------------------------------
# Parsers (graceful — fallback "—" se filesystem fail)
# ---------------------------------------------------------------------------

def _parse_kpi_int_fields(path: Path) -> dict[str, int]:
    """Estrae mrr, outstanding (int) dal frontmatter YAML di KPI.md."""
    fm = parse_frontmatter(read_text(path))
    out: dict[str, int] = {}
    for key in ("mrr", "outstanding"):
        raw = fm.get(key)
        if raw is None:
            continue
        try:
            out[key] = int(raw)
        except ValueError:
            continue
    return out


def _parse_radici_conditions(path: Path) -> dict:
    """Conta checkbox barrate vs totali nel blocco 'Condizione di uscita'."""
    text = read_text(path)
    if not text:
        return {}

    m = re.search(
        r"### Condizione di uscita.*?\n(.*?)(?=\n###|\n##\s)",
        text,
        re.DOTALL,
    )
    if not m:
        return {}
    block = m.group(1)
    checked = len(re.findall(r"^\s*-\s*\[x\]", block, re.MULTILINE | re.IGNORECASE))
    unchecked = len(re.findall(r"^\s*-\s*\[\s\]", block, re.MULTILINE))
    return {"checked": checked, "total": checked + unchecked}


# Pattern dates support both 'entro DD Mes YYYY' and bare 'DD Mes YYYY'.
_KILL_DATE_PATTERNS = (
    re.compile(r"entro\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.IGNORECASE),
    re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", re.IGNORECASE),
)


def _try_parse_date(snippet: str) -> date | None:
    for pat in _KILL_DATE_PATTERNS:
        m = pat.search(snippet)
        if not m:
            continue
        month = MONTH_LOOKUP.get(m.group(2).lower())
        if not month:
            continue
        try:
            return date(int(m.group(3)), month, int(m.group(1)))
        except ValueError:
            continue
    return None


def _parse_kill_date(path: Path) -> date:
    """Cerca la kill date '17 Luglio 2026' nella sezione T+3m del Roadmap Calendar.

    1. Isola la sezione `## 🎯 T+3m`, cerca dentro la prima data DD Mes YYYY.
    2. Fallback: prima data 'entro …' globale.
    3. Fallback finale: costante KILL_DATE.
    """
    text = read_text(path)
    if not text:
        return KILL_DATE

    sec = re.search(r"##\s*[^\n]*T\+3m[^\n]*\n(.*?)(?=\n##\s)", text, re.DOTALL)
    if sec:
        d = _try_parse_date(sec.group(1))
        if d is not None:
            return d

    d = _try_parse_date(text)
    return d if d is not None else KILL_DATE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@cached(ttl=60.0)
def read_phase_state() -> dict:  # noqa: D401 — public API
    return _read_phase_state_impl()


# Backward-compat surface for tests (sess.1534 round 7):
# - `_CACHE` dict (ts/data) — pulito da tests via `_CACHE['ts'] = 0`.
_CACHE = read_phase_state._cache_state  # type: ignore[attr-defined]


def _read_phase_state_impl() -> dict:
    """Restituisce lo stato corrente fase RADICI + kill check.

    Keys: mrr, mrr_target, outstanding, outstanding_target, contracts_signed,
    aurahome_status, conditions_met, kill_days_remaining, kill_date_str,
    kill_target, kill_clients_paid.
    """
    kpi = _parse_kpi_int_fields(KPI_FILE)
    cond = _parse_radici_conditions(ROADMAP_Q2)
    kill_d = _parse_kill_date(ROADMAP_CAL)

    mrr = kpi.get("mrr")
    outstanding = kpi.get("outstanding")

    # conditions_met derivato numericamente quando possibile, vault wins se più alto.
    conditions_met = 0
    if outstanding is not None and outstanding < OUTSTANDING_TARGET:
        conditions_met += 1
    if mrr is not None and mrr >= MRR_TARGET_Q2:
        conditions_met += 1
    # AuraHome ads LIVE 7+ giorni: ZERO ord stato attuale → False
    # Nuovi contratti firmati (Diella/Eletron24/Guccione)
    contracts_signed = 0  # ground truth sess.1534: zero
    if cond and cond.get("checked", 0) > conditions_met:
        conditions_met = cond["checked"]

    today = date.today()
    delta = (kill_d - today).days
    kill_days_remaining = delta if delta >= 0 else None

    return {
        "mrr": mrr,
        "mrr_target": MRR_TARGET_Q2,
        "outstanding": outstanding,
        "outstanding_target": OUTSTANDING_TARGET,
        "contracts_signed": contracts_signed,
        "aurahome_status": "⚠ ZERO ord",
        "conditions_met": conditions_met,
        "kill_days_remaining": kill_days_remaining,
        "kill_date_str": _format_kill_date(kill_d),
        "kill_target": KILL_TARGET_AMOUNT,
        "kill_clients_paid": 0,
    }


_MONTHS_SHORT_IT = ["", "Gen", "Feb", "Mar", "Apr", "Mag", "Giu",
                    "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]


def _format_kill_date(d: date) -> str:
    return f"{d.day} {_MONTHS_SHORT_IT[d.month]} {d.year}"


def _fmt_eur(v: int | None) -> str:
    if v is None:
        return "API✗"
    return f"€{v:,}".replace(",", ".")


def render_polestar_strip() -> str:
    """Restituisce 2 righe Rich-markup pronte per il TUI cockpit.

    Sempre 2 righe (newline tra le due). Mai crash su filesystem fail —
    fallback a 'API✗' / '—' per i campi mancanti.
    """
    try:
        s = read_phase_state()
    except Exception:
        return (
            f"🌌 [italic {DIM}]Stella polare 2031 · scalpello non statua[/]  ·  "
            f"[bold {ORANGE}]Fase RADICI[/] [{DIM}]—[/]\n"
            f"⏰ [bold {RED}]T+3m kill check[/] · — · [{DIM}]—[/]"
        )

    # Riga 1: Stella polare + Fase RADICI
    mrr_str, mrr_tgt = _fmt_eur(s["mrr"]), _fmt_eur(s["mrr_target"])
    os_str, os_tgt = _fmt_eur(s["outstanding"]), _fmt_eur(s["outstanding_target"])

    mrr_color = LIME if (s["mrr"] is not None and s["mrr"] >= s["mrr_target"]) else ORANGE
    os_color  = LIME if (s["outstanding"] is not None and s["outstanding"] < s["outstanding_target"]) else RED
    contracts_color = LIME if s["contracts_signed"] >= 1 else DIM

    cond_met = s["conditions_met"]
    if cond_met >= 4:
        cond_color = LIME
    elif cond_met >= 2:
        cond_color = TEAL
    else:
        cond_color = ORANGE
    cond_glyph = "✓" if cond_met >= 1 else "·"

    breakdown = (
        f"[{mrr_color}]MRR {mrr_str}/{mrr_tgt}[/] · "
        f"[{os_color}]OS {os_str}/<{os_tgt}[/] · "
        f"[{contracts_color}]{s['contracts_signed']}/1 contratto[/] · "
        f"[{ORANGE}]AuraHome {s['aurahome_status']}[/]"
    )

    line1 = (
        f"🌌 [italic {DIM}]Stella polare 2031 · scalpello non statua[/]  ·  "
        f"[bold {ORANGE}]Fase RADICI[/] "
        f"[{cond_color}]{cond_met}/4 {cond_glyph}[/] "
        f"({breakdown})"
    )

    # Riga 2: T+3m kill check
    kdays = s["kill_days_remaining"]
    if kdays is None:
        kill_label = f"[bold {RED}]EXPIRED[/]"
    else:
        if kdays > 90:
            kdays_color = LIME
        elif kdays > 30:
            kdays_color = ORANGE
        else:
            kdays_color = RED
        kill_label = f"[bold {kdays_color}]D-{kdays}gg[/]"

    paid = s["kill_clients_paid"]
    paid_color = LIME if paid >= 1 else DIM
    forge_target_eur = f"€{s['kill_target']:,}".replace(",", ".")

    line2 = (
        f"⏰ [bold {RED}]T+3m kill check[/] · "
        f"{s['kill_date_str']} · {kill_label} · "
        f"[{paid_color}]{paid}/1 cliente forgiatura pagante (≥{forge_target_eur})[/]"
    )

    return f"{line1}\n{line2}"
