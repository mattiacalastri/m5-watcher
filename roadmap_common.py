"""roadmap_common — costanti + helper condivisi dei moduli roadmap.

Estratto in round 5-7 (sess.1534) per ridurre duplicazione cross-module:
  - Palette colore (RED/ORANGE/LIME/DIM/TEAL + extra)
  - Vault path canonico (env-aware)
  - Today helpers ground truth
  - Italian month/weekday lookup (round 7)
  - Frontmatter parser, file reader, int-from-eur parser (round 7)
  - Severity → colore / icon mapping (round 7)
  - cached(ttl) decorator per i moduli con cache TTL boilerplate (round 7)

Public API target:
    from roadmap_common import (
        VAULT_BASE, KPI_FILE, RED, ORANGE, LIME, DIM, TEAL,
        today_iso, today_date, IT_MONTHS, read_text, parse_frontmatter,
        parse_int_eur, severity_color, severity_icon, cached,
    )
"""
from __future__ import annotations

import os
import re
import time
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any, Callable

# ── Color palette (hex inline, allineata a app.py + Polpo Brand) ──────────────
RED        = "#ff3366"
ORANGE     = "#ff8a3d"
LIME       = "#3ddc97"
DIM        = "#8a98ad"
TEAL       = "#00d4aa"
ELEC_BLUE  = "#3a7afe"
HOT_PINK   = "#ff6ec7"
DEEP_PURPL = "#9d4edd"
SOFT_GREEN = "#7dd87f"

PALETTE = {
    "RED": RED, "ORANGE": ORANGE, "LIME": LIME, "DIM": DIM, "TEAL": TEAL,
    "ELEC_BLUE": ELEC_BLUE, "HOT_PINK": HOT_PINK, "DEEP_PURPL": DEEP_PURPL,
    "SOFT_GREEN": SOFT_GREEN,
}


# ── Vault path (env-aware con fallback canonico) ──────────────────────────────
def _resolve_vault_base() -> Path:
    """Risolve la radice del vault Obsidian Astra Digital Marketing.

    Override via env var M5_VAULT_PATH (testabilità + portabilità M1/M5).
    Fallback al path canonico iCloud Obsidian di Mattia.
    """
    env_path = os.environ.get("M5_VAULT_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return (
        Path.home()
        / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Astra Digital Marketing"
    )


VAULT_BASE = _resolve_vault_base()


# ── Path canonici dei file roadmap-rilevanti ──────────────────────────────────
KPI_FILE        = VAULT_BASE / "KPI.md"
SESSION_CURRENT = VAULT_BASE / "session_current.md"
ROADMAP_Q2      = VAULT_BASE / "🧠 Memory" / "roadmap_q2_2026.md"
ROADMAP_CAL     = (
    VAULT_BASE
    / "5 — Vision"
    / "Roadmap Calendar 2026-2029 — Soul Engineer 3-6-12-24-36.md"
)
SESSIONI_DIR    = VAULT_BASE / "Sessioni"
CICATRICI_DIR   = VAULT_BASE / "Cicatrici"


# ── Date helper (ground truth, non hardcoded test) ───────────────────────────
def today_iso() -> str:
    """ISO date today — wrapper per allineamento test riproducibili.

    Override via env M5_TODAY_OVERRIDE (es. '2026-05-04') per snapshot test.
    """
    override = os.environ.get("M5_TODAY_OVERRIDE")
    if override:
        return override
    return date.today().isoformat()


def today_date() -> date:
    override = os.environ.get("M5_TODAY_OVERRIDE")
    if override:
        try:
            return date.fromisoformat(override)
        except ValueError:
            pass
    return date.today()


# ── Italian month lookup (round 7: estratto da blocks/filaments/outstanding) ──
IT_MONTHS_SHORT: dict[str, int] = {
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
    "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
}

IT_MONTHS_FULL: dict[str, int] = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

ENG_MONTHS_SHORT: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Lookup unificato (3-letter prefix → mese). Filaments usa anche varianti
# 4-letter ("genn", "febb", ...) e nomi inglesi pieni ("january"...) ma i suoi
# parser leggono prefissi short, quindi questo è sufficiente.
MONTH_LOOKUP: dict[str, int] = {
    **IT_MONTHS_SHORT,
    **IT_MONTHS_FULL,
    **ENG_MONTHS_SHORT,
    "genn": 1, "febb": 2, "marz": 3, "giug": 6, "lugl": 7,
    "agos": 8, "sett": 9,
}

IT_WEEKDAYS = {"lun", "mar", "mer", "gio", "ven", "sab", "dom"}


# ── Severity helpers (round 7: estratti da blocks/outstanding) ────────────────
_SEVERITY_COLOR: dict[str, str] = {
    "P0":        RED,
    "P1":        ORANGE,
    "P2":        ORANGE,
    "info":      DIM,
    "info-lime": LIME,
}

_SEVERITY_ICON: dict[str, str] = {
    "P0":        "🔴",
    "P1":        "🟡",
    "P2":        "🟡",
    "info":      "·",
    "info-lime": "·",
}


def severity_color(sev: str) -> str:
    """Colore esadecimale per una severity. Default → DIM."""
    return _SEVERITY_COLOR.get(sev, DIM)


def severity_icon(sev: str) -> str:
    """Icona Unicode per una severity. Default → '·'."""
    return _SEVERITY_ICON.get(sev, "·")


# ── File reader (round 7: graceful read pattern condiviso) ────────────────────
def read_text(path: Path) -> str:
    """Read graceful — restituisce '' su qualsiasi errore I/O."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return ""


# ── Frontmatter parser (round 7: estratto da traps/vectors/outstanding) ───────
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_FM_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parser YAML frontmatter minimale (key: value), tollerante quote/numeric.

    Restituisce dict di stringhe (no coercion). Quote singole/doppie eliminate.
    Multiline values non supportati — i moduli che ne hanno bisogno (es.
    outstanding_note) usano pattern dedicato.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}

    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        kv = _FM_LINE_RE.match(line)
        if not kv:
            continue
        key, raw = kv.group(1), kv.group(2).strip()
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        out[key] = raw
    return out


# ── Numeric parser (round 7: estratto da traps/outstanding) ───────────────────
_INT_PREFIX_RE = re.compile(r"^-?\d+")


def parse_int_eur(raw: str | None) -> int | None:
    """Estrae primo intero da stringa, tollerante a separatori (€, ',', '.', spazi).

    Esempi:
        '€1,500'   → 1500
        '€1.159'   → 1159
        '4124'     → 4124
        ''         → None
        'abc'      → None
    """
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace(".", "").replace("€", "").strip()
    m = _INT_PREFIX_RE.match(cleaned)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


# ── Cache decorator (round 7: estratto da boilerplate _CACHE in tutti moduli) ─
def cached(ttl: float) -> Callable:
    """Decorator: caches a zero-arg function result per ``ttl`` seconds.

    Per funzioni con argomento ``force`` o ``force_refresh`` il decorator
    intercetta l'argomento e bypassa il cache (mantenendo backward compat con
    le API esistenti dei moduli roadmap).

    NOT thread-safe per design — i moduli roadmap sono single-thread (TUI).
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        state: dict[str, Any] = {"ts": 0.0, "data": None}

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            force = bool(
                kwargs.pop("force", False) or kwargs.pop("force_refresh", False)
            )
            now = time.monotonic()
            if not force and state["data"] is not None and (now - state["ts"]) < ttl:
                return state["data"]
            result = fn(*args, **kwargs)
            state["ts"] = now
            state["data"] = result
            return result

        wrapper._cache_state = state  # type: ignore[attr-defined]
        return wrapper

    return decorator
