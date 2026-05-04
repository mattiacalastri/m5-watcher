"""Roadmap Filaments parser & renderer for M5 Watcher TUI cockpit.

Reads the "### Filamenti attivi" markdown table from roadmap_q2_2026.md,
classifies each filament by severity (P0/P1/info), detects date drift
(Italian short dates like "9 Apr", "ven 11", "sab 11 15:30"), cross-checks
session_current.md to flag stale entries, and emits a Rich-markup section.

Self-contained: NO Textual import. Only stdlib.

Public API:
    read_filaments(force=False, path=None) -> list[dict]
    render_filaments_section(filaments=None) -> str
    parse_italian_date(text, today=None) -> date | None
    classify_severity(stato, deadline, today) -> tuple[str, int | None]
    detect_session_drift(filaments) -> dict[str, dict]
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from roadmap_common import (
    RED, ORANGE, LIME, DIM, TEAL,
    ROADMAP_Q2 as ROADMAP_PATH,
    SESSION_CURRENT as SESSION_PATH,
    KPI_FILE as KPI_PATH,
)

# Severity dot glyphs
DOT_P0   = "\U0001f534"  # red circle
DOT_P1   = "\U0001f7e1"  # yellow circle
DOT_INFO = "\U0001f7e2"  # green circle


def _today() -> date:
    """Today date driven by env var ROADMAP_FILAMENTS_TODAY for test reproducibility."""
    override = os.environ.get("ROADMAP_FILAMENTS_TODAY")
    if override:
        try:
            return datetime.strptime(override, "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


# Italian + English month abbreviations → month index
_ITA_MONTHS: dict[str, int] = {
    "gen": 1, "genn": 1, "gennaio": 1,
    "feb": 2, "febb": 2, "febbraio": 2,
    "mar": 3, "marz": 3, "marzo": 3,
    "apr": 4, "aprile": 4,
    "mag": 5, "maggio": 5,
    "giu": 6, "giug": 6, "giugno": 6,
    "lug": 7, "lugl": 7, "luglio": 7,
    "ago": 8, "agos": 8, "agosto": 8,
    "set": 9, "sett": 9, "settembre": 9,
    "ott": 10, "ottobre": 10,
    "nov": 11, "novembre": 11,
    "dic": 12, "dicembre": 12,
}
_ENG_MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_LOOKUP = {**_ITA_MONTHS, **_ENG_MONTHS}
_ITA_WEEKDAYS = {"lun", "mar", "mer", "gio", "ven", "sab", "dom"}


# ---------------------------------------------------------------------------
# Caches (separate roadmap + session caches; mtime-aware)
# ---------------------------------------------------------------------------

_CACHE: dict[str, object] = {"ts": 0.0, "data": None, "mtime": None}
_SESSION_CACHE: dict[str, object] = {"ts": 0.0, "content": None, "mtime": None}
_CACHE_TTL_SEC = 60


def _stat_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _read_session_content(force: bool = False) -> str:
    """Read session_current.md + KPI.md with 60s TTL cache.

    Concatenates both files so drift rules can match ground-truth facts that
    live in KPI.md (e.g. "Bressan #24 SALDATA tutto").
    """
    now = time.time()
    mtime = (_stat_mtime(SESSION_PATH), _stat_mtime(KPI_PATH))

    cached = _SESSION_CACHE.get("content")
    if (
        not force
        and cached is not None
        and (now - float(_SESSION_CACHE.get("ts") or 0)) < _CACHE_TTL_SEC
        and _SESSION_CACHE.get("mtime") == mtime
    ):
        return str(cached)

    parts: list[str] = []
    for src in (SESSION_PATH, KPI_PATH):
        try:
            parts.append(src.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    text = "\n\n".join(parts)

    _SESSION_CACHE["content"] = text
    _SESSION_CACHE["ts"] = now
    _SESSION_CACHE["mtime"] = mtime
    return text


# ---------------------------------------------------------------------------
# Session-drift detection rules
# ---------------------------------------------------------------------------
# Each rule: (name_pattern, resolved_patterns, still_active_patterns, keep_drift_note)
# Matching order: resolved_pats → roadmap_stale; active_pats → still_active;
# else → unknown. Empty resolved+active = always-active (skip detection).

_DRIFT_RULES: list[tuple] = [
    (
        r"Bressan|Carne.?Express",
        [r"Bressan.{0,80}(SALDATA|saldata|chiuso|completato|incassato|saldato)"],
        [r"Bressan.{0,40}pending"],
        None,
    ),
    (
        r"Diella",
        [r"Diella.{0,80}(firmato|chiuso|risposta|contratto)"],
        [r"Diella.{0,40}(follow.?up|dormiente|archive)"],
        None,
    ),
    (
        r"Outstanding.*(attivo|Kongline|Adrian)",
        [
            r"Kongline.{0,80}(PAGATA|pagata|pagato|incassato|saldato)",
            r"Adrian.{0,80}(pagato|saldato|incassato)",
        ],
        [r"Kongline.{0,40}sollecito", r"Adrian.{0,60}silenzio"],
        "Kongline: verificare stato pagamento",
    ),
    (
        r"Guccione",
        [r"Guccione.{0,80}(firmato|attivato|retainer.{0,20}attivo)"],
        [r"Guccione.{0,60}scomparso"],
        "KEEP DRIFT — ghost confermato",
    ),
    (
        r"Eletron24|Adrian",
        [r"Adrian.{0,80}(pagato|saldato|incassato)"],
        [r"Adrian.{0,60}silenzio"],
        None,
    ),
    (
        r"AuraHome.ads",
        [r"AuraHome.{0,80}(ROAS|ordini.{0,20}live|fatturato)"],
        [r"AuraHome.{0,60}ZERO"],
        None,
    ),
    (
        r"Marconi.Falco",
        [r"Marconi.{0,80}(go.?live|saldata|saldato|chiuso)"],
        [r"Marconi.{0,60}KIT"],
        None,
    ),
    (
        r"Merli.Setter",
        [r"Merli.{0,80}(firmato|chiuso|contratto.{0,20}sign)"],
        [r"Merli.{0,60}orfani"],
        None,
    ),
    (
        r"Francesco.Guerra|(?<![a-z])FG(?![a-z-])",
        [r"(Francesco.?Guerra|(?<![a-z])FG(?![a-z-])).{0,80}(incassato|pagato|saldato)"],
        [r"(Francesco.?Guerra|(?<![a-z])FG(?![a-z-])).{0,60}(ci.pensa|silenzio)"],
        None,
    ),
    (r"Fondi", [], [], "skip — always active"),
    (
        r"Benincasa.Ads",
        [r"Benincasa.{0,80}(credenziali|tracking.{0,20}ok|installato)"],
        [r"Benincasa.{0,60}BRUCIA"],
        None,
    ),
]


def _snippet_around(body: str, m: re.Match) -> str:
    start = max(0, m.start() - 10)
    end = min(len(body), m.end() + 60)
    snippet = body[start:end].replace("\n", " ").strip()
    return snippet[:119] + "…" if len(snippet) > 120 else snippet


def _match_rule(rule: tuple, session_body: str) -> tuple[str, str]:
    """Return (status, evidence) for a single rule against the session body."""
    _, resolved_pats, active_pats, _ = rule
    flags = re.IGNORECASE | re.DOTALL

    for pat in resolved_pats:
        m = re.search(pat, session_body, flags)
        if m:
            return "roadmap_stale", _snippet_around(session_body, m)
    for pat in active_pats:
        m = re.search(pat, session_body, flags)
        if m:
            return "session_still_active", _snippet_around(session_body, m)

    return "unknown", "no match in session_current.md"


def detect_session_drift(filaments: list[dict]) -> dict[str, dict]:
    """Cross-reference each filament against session_current.md.

    Returns mapping name → {status, evidence, roadmap_says, severity_override}.
    Status ∈ {in_sync, roadmap_stale, skip, unknown}. Graceful: empty dict if
    session content unreadable.
    """
    session_body = _read_session_content()
    if not session_body:
        return {}

    result: dict[str, dict] = {}
    for fil in filaments:
        name = fil.get("name", "")
        stato = fil.get("stato", "")
        stato_short = stato[:100] + "…" if len(stato) > 100 else stato

        matched_rule = next(
            (r for r in _DRIFT_RULES if re.search(r[0], name, re.IGNORECASE)),
            None,
        )

        if matched_rule is None:
            result[name] = {
                "status": "unknown",
                "evidence": "no rule defined for this filament",
                "roadmap_says": stato_short,
                "severity_override": None,
            }
            continue

        _, resolved_pats, active_pats, keep_note = matched_rule
        if not resolved_pats and not active_pats:
            result[name] = {
                "status": "skip",
                "evidence": keep_note or "always active",
                "roadmap_says": stato_short,
                "severity_override": None,
            }
            continue

        status, evidence = _match_rule(matched_rule, session_body)
        # session_still_active → in_sync (roadmap consistent with session)
        if status == "session_still_active":
            status = "in_sync"

        result[name] = {
            "status": status,
            "evidence": evidence,
            "roadmap_says": stato_short,
            "severity_override": "info" if status == "roadmap_stale" else None,
        }

    return result


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class Filament:
    name: str
    segnale_vita: str
    segnale_morte: str
    stato: str
    severity: str = "info"               # P0 | P1 | info
    days_drift: Optional[int] = None     # positive = days past deadline
    deadline: Optional[date] = None      # parsed deadline (if any)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "segnale_vita": self.segnale_vita,
            "segnale_morte": self.segnale_morte,
            "stato": self.stato,
            "severity": self.severity,
            "days_drift": self.days_drift,
            "deadline": self.deadline.isoformat() if self.deadline else None,
        }


# ---------------------------------------------------------------------------
# Date parser
# ---------------------------------------------------------------------------

def _strip_md_emph(s: str) -> str:
    """Strip surrounding markdown bold/italic markers and backticks from a cell."""
    s = s.strip()
    if s.startswith("`") and s.endswith("`") and len(s) >= 2:
        s = s[1:-1].strip()
    if s.startswith("**") and s.endswith("**") and len(s) >= 4:
        s = s[2:-2].strip()
    elif s.startswith("*") and s.endswith("*") and len(s) >= 2:
        s = s[1:-1].strip()
    return s


def _resolve_year(month: int, today: date) -> int:
    """Picks year: <-200 days delta → previous year, >200 → next year, else this year."""
    candidate = date(today.year, month, 1)
    delta = (today - candidate).days
    if delta < -200:
        return today.year - 1
    if delta > 200:
        return today.year + 1
    return today.year


_DATE_PATTERNS = (
    re.compile(r"\b(\d{1,2})\s+([A-Za-zÀ-ÿ]{3,})\b"),  # "9 Apr", "5 Mag"
    re.compile(r"\b([A-Za-z]{3,})\s+(\d{1,2})\b"),     # "Apr 9", "May 5"
)
_WEEKDAY_DAY_RE = re.compile(
    r"\b(lun|mar|mer|gio|ven|sab|dom)\s+(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?\b",
    re.IGNORECASE,
)
_ITA_WEEKDAY_IDX = {"lun": 0, "mar": 1, "mer": 2, "gio": 3, "ven": 4, "sab": 5, "dom": 6}


def parse_italian_date(text: str, today: Optional[date] = None) -> Optional[date]:
    """Best-effort parse of the *first* date found in the stato string.

    Supports: "9 Apr", "Apr 9", "ven 11", "sab 11 15:30". Returns None when
    nothing parseable is detected.
    """
    if not text:
        return None
    today = today or _today()

    # 1) "<day> <month>" or "<month> <day>"
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            g1, g2 = m.group(1), m.group(2)
            day_s, mon_s = (g1, g2) if g1.isdigit() else (g2, g1)
            mon_key = mon_s.lower().strip(".")
            if not mon_key.isalpha():
                continue
            # Skip weekday tokens that accidentally matched
            if mon_key[:3] in _ITA_WEEKDAYS and mon_key not in _MONTH_LOOKUP:
                continue
            month = _MONTH_LOOKUP.get(mon_key) or _MONTH_LOOKUP.get(mon_key[:3])
            if not month:
                continue
            try:
                return date(_resolve_year(month, today), month, int(day_s))
            except (ValueError, TypeError):
                continue

    # 2) "<weekday> <day>" — find nearest matching date
    m = _WEEKDAY_DAY_RE.search(text)
    if m:
        try:
            target_wd = _ITA_WEEKDAY_IDX.get(m.group(1).lower())
            day = int(m.group(2))
            best: Optional[date] = None
            best_score = (10**9, 10**9)  # (weekday_mismatch, |offset|)
            for ad in range(0, 91):
                for sign in (1, -1):
                    if ad == 0 and sign == -1:
                        continue
                    candidate = today + timedelta(days=ad * sign)
                    if candidate.day != day:
                        continue
                    wd_mismatch = (
                        0 if (target_wd is None or candidate.weekday() == target_wd)
                        else 1
                    )
                    score = (wd_mismatch, abs(ad * sign))
                    if score < best_score:
                        best_score = score
                        best = candidate
                        if wd_mismatch == 0:
                            return best
            if best:
                return best
        except (ValueError, TypeError):
            pass

    return None


# ---------------------------------------------------------------------------
# Severity classifier
# ---------------------------------------------------------------------------

# Round 7: revenue burn pattern → P0 (AuraHome ZERO ORDINI €50/day)
_BURN_RATE_RE = re.compile(
    r"brucia\s+€\d+|ZERO\s+ORDINI|€\d+\s*/\s*(?:giorno|day)\s+attivi",
    re.IGNORECASE,
)


def classify_severity(
    stato: str, deadline: Optional[date], today: date,
) -> tuple[str, Optional[int]]:
    """Return (severity, days_drift). days_drift positive = past deadline."""
    days_drift: Optional[int] = None
    if deadline:
        delta = (today - deadline).days
        if delta > 0:
            days_drift = delta

    s = stato or ""
    s_upper = s.upper()

    # P0 patterns: 🔥, KIT PRONTO, ⚡ PRIORITÀ, CALL imminente, revenue burn
    if "\U0001f525" in s or "KIT PRONTO" in s:
        return "P0", days_drift
    if "⚡" in s and "PRIORIT" in s_upper:
        return "P0", days_drift
    if re.search(r"\bCALL\b", s_upper) and not re.search(r"DOPO\s+CALL", s_upper):
        return "P0", days_drift
    if _BURN_RATE_RE.search(s):
        return "P0", days_drift

    # P1 patterns: ⚠️, WAITING, FOLLOW-UP, DRIFT, ⏳ + drift
    if "⚠" in s:
        return "P1", days_drift
    if any(token in s_upper for token in ("WAITING", "DRIFT", "FOLLOW-UP", "FOLLOW UP")):
        return "P1", days_drift
    if "⏳" in s:
        return ("P1" if days_drift else "info"), days_drift

    # Past deadline alone (no special emoji) → P1 drift
    if days_drift is not None and days_drift > 0:
        return "P1", days_drift

    return "info", days_drift


# ---------------------------------------------------------------------------
# Markdown table parser
# ---------------------------------------------------------------------------

def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row into cells; drop leading/trailing pipe."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    """A markdown separator row contains only dashes/colons/whitespace per cell."""
    if not cells:
        return False
    return all(re.fullmatch(r":?-+:?\s*", c) for c in cells if c)


def parse_filaments(content: str, today: Optional[date] = None) -> list[Filament]:
    """Parse the '### Filamenti attivi' table from a roadmap markdown blob."""
    today = today or _today()
    lines = content.splitlines()

    start_idx: Optional[int] = None
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("### filamenti attivi"):
            start_idx = i
            break
    if start_idx is None:
        return []

    table_lines: list[str] = []
    in_table = False
    header_seen = False
    for ln in lines[start_idx + 1:]:
        stripped = ln.strip()
        if stripped.startswith("### ") or stripped.startswith("## "):
            if in_table:
                break
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = _split_table_row(stripped)
            if not header_seen:
                header_seen = True
                in_table = True
                continue
            if _is_separator_row(cells):
                continue
            table_lines.append(stripped)
            in_table = True
        elif in_table and not stripped:
            break

    filaments: list[Filament] = []
    for row in table_lines:
        cells = _split_table_row(row)
        if len(cells) < 4:
            continue
        name = _strip_md_emph(cells[0])
        if not name:
            continue
        stato = _strip_md_emph(cells[3])
        deadline = parse_italian_date(stato, today=today)
        severity, drift = classify_severity(stato, deadline, today)
        filaments.append(
            Filament(
                name=name,
                segnale_vita=_strip_md_emph(cells[1]),
                segnale_morte=_strip_md_emph(cells[2]),
                stato=stato,
                severity=severity,
                days_drift=drift,
                deadline=deadline,
            )
        )
    return filaments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_filaments(force: bool = False, path: Optional[Path] = None) -> list[dict]:
    """Read & parse filaments from the roadmap. Cached 60s (mtime-aware)."""
    src = path or ROADMAP_PATH
    now = time.time()
    try:
        mtime = src.stat().st_mtime
    except FileNotFoundError:
        return []

    cached = _CACHE.get("data")
    if (
        not force
        and cached is not None
        and (now - float(_CACHE.get("ts") or 0)) < _CACHE_TTL_SEC
        and _CACHE.get("mtime") == mtime
    ):
        return list(cached)  # type: ignore[arg-type]

    try:
        content = src.read_text(encoding="utf-8")
    except OSError:
        return []
    fils = parse_filaments(content)
    data = [f.to_dict() for f in fils]
    _CACHE["data"] = data
    _CACHE["ts"] = now
    _CACHE["mtime"] = mtime
    return data


def _short_stato(stato: str, max_len: int = 80) -> str:
    """Compact a stato cell for single-line render: collapse whitespace, truncate."""
    s = re.sub(r"\s+", " ", stato).strip()
    return s[: max_len - 1] + "…" if len(s) > max_len else s


_SEV_DOT_COLOR = {
    "P0":   (DOT_P0, RED),
    "P1":   (DOT_P1, ORANGE),
    "info": (DOT_INFO, LIME),
}


def _render_one_line(f: Filament) -> str:
    """Render a single filament line in Rich markup."""
    stato = _short_stato(f.stato, max_len=110)
    drift_tag = (
        f" [bold {RED}](DRIFT — D+{f.days_drift})[/]"
        if f.days_drift and f.days_drift > 0 else ""
    )
    dot, color = _SEV_DOT_COLOR.get(f.severity, (DOT_INFO, LIME))
    return f"[{color}]{dot} {f.name}[/] [{DIM}]·[/] {stato}{drift_tag}"


_SEV_ORDER_RENDER = {"P0": 0, "P1": 1, "info": 2}


def render_filaments_section(filaments: Optional[list[dict]] = None) -> str:
    """Render the FILAMENTI section as a Rich-markup block string.

    Integrates detect_session_drift to annotate stale entries with a green
    session-fresh badge and suppress DRIFT counter noise. Backward-compatible:
    if drift detection fails the section renders normally.
    """
    if filaments is None:
        filaments = read_filaments()

    if not filaments:
        return f"[bold {RED}]━━━ \U0001f331 FILAMENTI RADICI[/] [{DIM}]no roadmap found[/]"

    try:
        drift_map = detect_session_drift(filaments)
    except Exception:
        drift_map = {}

    objs: list[Filament] = []
    for d in filaments:
        deadline_d: Optional[date] = None
        if d.get("deadline"):
            try:
                deadline_d = datetime.strptime(d["deadline"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                pass
        objs.append(
            Filament(
                name=d["name"],
                segnale_vita=d.get("segnale_vita", ""),
                segnale_morte=d.get("segnale_morte", ""),
                stato=d.get("stato", ""),
                severity=d.get("severity", "info"),
                days_drift=d.get("days_drift"),
                deadline=deadline_d,
            )
        )

    total = len(objs)
    p0 = sum(1 for o in objs if o.severity == "P0")
    drifted = sum(1 for o in objs if o.days_drift and o.days_drift > 0)
    silent = sum(
        1 for o in objs
        if o.severity == "info" and not (o.days_drift or 0) > 0
    )
    stale_resolved = sum(
        1 for o in objs
        if drift_map.get(o.name, {}).get("status") == "roadmap_stale"
    )

    # Sort: P0 first, then P1 (drift first within P1), then info; stale last
    def _sort_key(o: Filament) -> tuple:
        is_stale = drift_map.get(o.name, {}).get("status") == "roadmap_stale"
        return (
            int(is_stale),
            _SEV_ORDER_RENDER.get(o.severity, 9),
            -(o.days_drift or 0),
        )
    objs.sort(key=_sort_key)

    stale_tag = f" · {stale_resolved} stale-resolved ✓" if stale_resolved else ""
    header = (
        f"[bold {RED}]━━━ \U0001f331 FILAMENTI RADICI "
        f"({total} · {p0} fired · {drifted} drift · {silent} silenti{stale_tag}) "
        f"━━━━━━━━━━━━[/]"
    )
    lines = [header]

    for o in objs:
        d_info = drift_map.get(o.name, {})
        if d_info.get("status") == "roadmap_stale":
            evidence = d_info.get("evidence", "")[:60]
            stato_short = _short_stato(o.stato, max_len=80)
            lines.append(
                f"[{LIME}]{DOT_INFO} {o.name}[/] [{DIM}]·[/] "
                f"[{DIM}]{stato_short}[/] [{LIME}]✓ session-fresh: {evidence}[/]"
            )
        else:
            lines.append(_render_one_line(o))

    session_note = f" · {stale_resolved} stale-resolved" if stale_resolved else ""
    lines.append(
        f"[{DIM}]source:[/] [{TEAL}]roadmap_q2_2026.md[/] [{DIM}]· session_current crosscheck ·"
        f" today {_today().isoformat()} · cache 60s{session_note}[/]"
    )
    return "\n".join(lines)
