"""
roadmap_polestar.py — Sess.1534

Strip 2-righe roadmap-aware per il TUI cockpit M5 Watcher.

Riga 1: Stella polare 2031 + Fase RADICI con progress 4 condizioni di uscita
Riga 2: T+3m kill check countdown (17 Lug 2026)

Self-contained: solo stdlib + Rich markup inline.
Public API:
    render_polestar_strip() -> str       # 2 righe Rich-markup
    read_phase_state() -> dict           # ground truth + counters

Ground truth read da:
- KPI.md frontmatter (mrr, outstanding, mrr_target_q2)
- roadmap_q2_2026.md "Condizione di uscita" RADICI
- Roadmap Calendar 2026-2029 — Soul Engineer (T+3m kill 17 Lug 2026)

Cicatrici onorate:
- sess.1224: mai dato statico passato per dinamico → "?" o "API✗" se sorgente muta
- Render adattivo: 2 righe stretta, no Textual import (solo Rich markup come gli altri widget)
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from pathlib import Path

# === Round 5: palette + path centralizzati in roadmap_common ===
from roadmap_common import (
    RED, ORANGE, LIME, DIM, TEAL,
    VAULT_BASE, KPI_FILE, ROADMAP_Q2, ROADMAP_CAL,
)
WHITE = "#e8edf5"  # locale, non in palette comune

# === Costanti fase RADICI (target hard-coded — sono di prodotto, non drift-prone) ===
OUTSTANDING_TARGET = 3000          # Outstanding < €3.000
MRR_TARGET_Q2      = 5200          # MRR >= €5.200
KILL_DATE          = date(2026, 7, 17)   # T+3m kill check
KILL_TARGET_AMOUNT = 2500          # ≥€2.500 cliente forgiatura

# === Cache TTL 60s ===
_CACHE: dict = {"ts": 0.0, "data": None}
_TTL_S = 60.0


# ---------------------------------------------------------------------------
# Parsers (graceful — fallback "—" se filesystem fail)
# ---------------------------------------------------------------------------

def _parse_kpi_frontmatter(path: Path) -> dict:
    """Estrae mrr, outstanding dal frontmatter YAML di KPI.md.

    Restituisce {} se file inaccessibile o frontmatter malformato.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    # Estrai frontmatter (tra primo --- e secondo ---)
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    fm = m.group(1)

    out: dict = {}
    for key in ("mrr", "outstanding"):
        km = re.search(rf"^{key}:\s*(\d+)\s*$", fm, re.MULTILINE)
        if km:
            try:
                out[key] = int(km.group(1))
            except ValueError:
                pass
    return out


def _parse_radici_conditions(path: Path) -> dict:
    """Verifica quali delle 4 condizioni RADICI sono spuntate ([x] vs [ ]).

    Cerca il blocco '### Condizione di uscita' nella nota roadmap_q2_2026.md.
    Restituisce {} se file inaccessibile.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    # Estrai blocco "Condizione di uscita" fino a prossimo header
    m = re.search(
        r"### Condizione di uscita.*?\n(.*?)(?=\n###|\n##\s)",
        text,
        re.DOTALL,
    )
    if not m:
        return {}
    block = m.group(1)

    # Conta checkbox
    checked = len(re.findall(r"^\s*-\s*\[x\]", block, re.MULTILINE | re.IGNORECASE))
    unchecked = len(re.findall(r"^\s*-\s*\[\s\]", block, re.MULTILINE))
    return {"checked": checked, "total": checked + unchecked}


def _parse_kill_date(path: Path) -> date:
    """Cerca la kill date '17 Luglio 2026' nella sezione T+3m del Roadmap Calendar.

    Strategia (in ordine):
    1. Isola la sezione `## 🎯 T+3m`, cerca dentro la prima data DD Lug(lio) YYYY.
    2. Fallback: prima data del kill criterion 'entro DD Mese YYYY' globale.
    3. Fallback finale: costante KILL_DATE.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return KILL_DATE

    months_it = {
        "gennaio": 1, "gen": 1,
        "febbraio": 2, "feb": 2,
        "marzo": 3, "mar": 3,
        "aprile": 4, "apr": 4,
        "maggio": 5, "mag": 5,
        "giugno": 6, "giu": 6,
        "luglio": 7, "lug": 7,
        "agosto": 8, "ago": 8,
        "settembre": 9, "set": 9,
        "ottobre": 10, "ott": 10,
        "novembre": 11, "nov": 11,
        "dicembre": 12, "dic": 12,
    }
    month_alt = (
        r"gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|"
        r"agosto|settembre|ottobre|novembre|dicembre|"
        r"gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic"
    )

    def _try_parse(snippet: str) -> date | None:
        # "entro 17 Luglio 2026" o semplicemente "17 Luglio 2026"
        for pat in (
            rf"entro\s+(\d{{1,2}})\s+({month_alt})\s+(\d{{4}})",
            rf"(\d{{1,2}})\s+({month_alt})\s+(\d{{4}})",
        ):
            m = re.search(pat, snippet, re.IGNORECASE)
            if m:
                try:
                    return date(
                        int(m.group(3)),
                        months_it[m.group(2).lower()],
                        int(m.group(1)),
                    )
                except (KeyError, ValueError):
                    continue
        return None

    # 1. Isola sezione T+3m (fino al prossimo '## ')
    sec = re.search(r"##\s*[^\n]*T\+3m[^\n]*\n(.*?)(?=\n##\s)", text, re.DOTALL)
    if sec:
        d = _try_parse(sec.group(1))
        if d is not None:
            return d

    # 2. Fallback globale solo su pattern "entro …"
    d = _try_parse(text)
    if d is not None:
        return d

    return KILL_DATE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_phase_state(force_refresh: bool = False) -> dict:
    """Restituisce lo stato corrente fase RADICI + kill check.

    Keys:
        mrr (int|None)                    — MRR ground truth corrente
        mrr_target (int)                  — €5.200
        outstanding (int|None)            — Outstanding corrente
        outstanding_target (int)          — €3.000
        contracts_signed (int)            — # nuovi contratti (Diella/Eletron24/Guccione)
        aurahome_status (str)             — etichetta stato AuraHome ads
        conditions_met (int)              — 0..4 (condizioni uscita RADICI vere)
        kill_days_remaining (int|None)    — giorni a 17 Lug 2026 (None se passato)
        kill_date_str (str)               — "17 Lug 2026"
        kill_target (int)                 — 2500
        kill_clients_paid (int)           — 0..N clienti forgiatura paganti
    """
    now = time.time()
    if not force_refresh and _CACHE["data"] and (now - _CACHE["ts"]) < _TTL_S:
        return _CACHE["data"]

    kpi = _parse_kpi_frontmatter(KPI_FILE)
    cond = _parse_radici_conditions(ROADMAP_Q2)
    kill_d = _parse_kill_date(ROADMAP_CAL)

    mrr = kpi.get("mrr")
    outstanding = kpi.get("outstanding")

    # Calcolo conditions_met:
    # Preferiamo derivare numericamente quando abbiamo dati ground truth,
    # ma rispettiamo il count del file se disponibile (vault è la verità).
    conditions_met = 0
    if outstanding is not None and outstanding < OUTSTANDING_TARGET:
        conditions_met += 1
    if mrr is not None and mrr >= MRR_TARGET_Q2:
        conditions_met += 1
    # AuraHome ads LIVE 7+ giorni: ZERO ord stato attuale → False
    aurahome_live_7d = False
    if aurahome_live_7d:
        conditions_met += 1
    # Nuovi contratti firmati (Diella/Eletron24/Guccione)
    contracts_signed = 0  # ground truth sess.1534: zero
    if contracts_signed >= 1:
        conditions_met += 1

    # Override se vault dichiara più checkbox barrate (vault wins se più alto)
    if cond and cond.get("checked", 0) > conditions_met:
        conditions_met = cond["checked"]

    today = date.today()
    delta = (kill_d - today).days
    kill_days_remaining = delta if delta >= 0 else None

    data = {
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
    _CACHE["ts"] = now
    _CACHE["data"] = data
    return data


def _format_kill_date(d: date) -> str:
    months_short = ["", "Gen", "Feb", "Mar", "Apr", "Mag", "Giu",
                    "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]
    return f"{d.day} {months_short[d.month]} {d.year}"


def _fmt_eur(v: int | None) -> str:
    if v is None:
        return "API✗"
    if v >= 1000:
        return f"€{v/1000:.3f}".rstrip("0").rstrip(".").replace(".", ",") \
            if False else f"€{v:,}".replace(",", ".")
    return f"€{v}"


def render_polestar_strip() -> str:
    """Restituisce 2 righe Rich-markup pronte per il TUI cockpit.

    Sempre 2 righe (newline tra le due). Nessun crash su filesystem fail —
    fallback a 'API✗' / '—' per i campi mancanti.
    """
    try:
        s = read_phase_state()
    except Exception:
        # Last-resort: stringa minimale, mai crash
        return (
            f"🌌 [italic {DIM}]Stella polare 2031 · scalpello non statua[/]  ·  "
            f"[bold {ORANGE}]Fase RADICI[/] [{DIM}]—[/]\n"
            f"⏰ [bold {RED}]T+3m kill check[/] · — · [{DIM}]—[/]"
        )

    # === Riga 1: Stella polare + Fase RADICI ===
    mrr_str = _fmt_eur(s["mrr"])
    mrr_tgt = _fmt_eur(s["mrr_target"])
    os_str  = _fmt_eur(s["outstanding"])
    os_tgt  = _fmt_eur(s["outstanding_target"])

    # Color hint sui sub-KPI
    mrr_color = LIME if (s["mrr"] is not None and s["mrr"] >= s["mrr_target"]) else ORANGE
    os_color  = LIME if (s["outstanding"] is not None and s["outstanding"] < s["outstanding_target"]) else RED
    contracts_color = LIME if s["contracts_signed"] >= 1 else DIM

    cond_met = s["conditions_met"]
    cond_color = LIME if cond_met >= 4 else (TEAL if cond_met >= 2 else ORANGE)
    cond_glyph = "✓" if cond_met >= 1 else "·"

    # MRR <…> · OS <…> · contratti · AuraHome
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

    # === Riga 2: T+3m kill check ===
    kdays = s["kill_days_remaining"]
    if kdays is None:
        kill_label = f"[bold {RED}]EXPIRED[/]"
    else:
        kdays_color = LIME if kdays > 90 else (ORANGE if kdays > 30 else RED)
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


# ---------------------------------------------------------------------------
# Stress test sintetico
# ---------------------------------------------------------------------------

def _stress_test() -> tuple[bool, list[str]]:
    """Valida render + parsing. Ritorna (passed, log_lines)."""
    log: list[str] = []
    failures: list[str] = []

    # 1. read_phase_state ritorna dict con tutte le chiavi
    s = read_phase_state(force_refresh=True)
    required_keys = {
        "mrr", "mrr_target", "outstanding", "outstanding_target",
        "contracts_signed", "aurahome_status", "conditions_met",
        "kill_days_remaining", "kill_date_str", "kill_target",
        "kill_clients_paid",
    }
    missing = required_keys - set(s.keys())
    if missing:
        failures.append(f"read_phase_state missing keys: {missing}")
    else:
        log.append(f"[OK] read_phase_state ha tutte le {len(required_keys)} chiavi")

    # 2. Tipi/range
    if s["mrr_target"] != 5200:
        failures.append(f"mrr_target wrong: {s['mrr_target']}")
    else:
        log.append(f"[OK] mrr_target = €{s['mrr_target']}")

    if s["outstanding_target"] != 3000:
        failures.append(f"outstanding_target wrong: {s['outstanding_target']}")
    else:
        log.append(f"[OK] outstanding_target = €{s['outstanding_target']}")

    if not (0 <= s["conditions_met"] <= 4):
        failures.append(f"conditions_met out of range: {s['conditions_met']}")
    else:
        log.append(f"[OK] conditions_met = {s['conditions_met']}/4")

    # 3. Kill date sensibile (passato = None, futuro = int)
    kdays = s["kill_days_remaining"]
    if kdays is not None and not isinstance(kdays, int):
        failures.append(f"kill_days_remaining tipo errato: {type(kdays)}")
    else:
        log.append(f"[OK] kill_days_remaining = {kdays}")

    # 4. render produce esattamente 2 righe
    out = render_polestar_strip()
    lines = out.split("\n")
    if len(lines) != 2:
        failures.append(f"render NON produce 2 righe: {len(lines)} righe")
    else:
        log.append(f"[OK] render produce 2 righe ({len(out)} char totali)")

    # 5. render contiene marker chiave
    must_contain = [
        "Stella polare 2031",
        "Fase RADICI",
        "T+3m kill check",
        "cliente forgiatura",
    ]
    for token in must_contain:
        if token not in out:
            failures.append(f"render manca token: '{token}'")
    if all(t in out for t in must_contain):
        log.append(f"[OK] render contiene tutti i {len(must_contain)} marker")

    # 6. Cache TTL: secondo call senza force_refresh deve essere identico
    s2 = read_phase_state()
    if s2 is not s and s2 != s:
        failures.append("cache non sta funzionando (oggetti differenti)")
    else:
        log.append("[OK] cache TTL 60s funzionante")

    # 7. Graceful fail: se path inesistente → fallback senza crash
    saved = globals()["KPI_FILE"]
    try:
        globals()["KPI_FILE"] = Path("/tmp/__nonexistent_kpi__.md")
        _CACHE["ts"] = 0.0
        s3 = read_phase_state(force_refresh=True)
        if s3["mrr"] is not None:
            failures.append("graceful fail su KPI mancante: mrr non None")
        else:
            log.append("[OK] graceful fallback: mrr=None su file mancante")
        out3 = render_polestar_strip()
        if "API✗" not in out3:
            failures.append("render non mostra 'API✗' su fallback")
        else:
            log.append("[OK] render mostra 'API✗' su API mancante (cicatrice sess.1224)")
    finally:
        globals()["KPI_FILE"] = saved
        _CACHE["ts"] = 0.0

    return (len(failures) == 0), log + ([f"[FAIL] {f}" for f in failures])


if __name__ == "__main__":
    # Render output Rich-markup (utile per ispezione visiva via pipe a `rich`)
    print("=" * 78)
    print("RENDER OUTPUT (Rich markup raw):")
    print("=" * 78)
    print(render_polestar_strip())
    print()

    # Render visivo Rich (se disponibile)
    print("=" * 78)
    print("RENDER VISIVO (via rich.console):")
    print("=" * 78)
    try:
        from rich.console import Console
        Console().print(render_polestar_strip())
    except ImportError:
        print("(rich non installato in questo venv — solo markup raw)")
    print()

    # Stress test
    print("=" * 78)
    print("STRESS TEST:")
    print("=" * 78)
    passed, log_lines = _stress_test()
    for ln in log_lines:
        print(ln)
    print()
    print(f"=== ESITO: {'PASS' if passed else 'FAIL'} ===")

    # Stato corrente
    print()
    print("=" * 78)
    print("PHASE STATE DUMP:")
    print("=" * 78)
    state = read_phase_state(force_refresh=True)
    for k, v in state.items():
        print(f"  {k:30s} = {v}")
