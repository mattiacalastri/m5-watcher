"""Roadmap Filaments parser & renderer for M5 Watcher TUI cockpit.

Reads the "### Filamenti attivi" markdown table from Mattia's roadmap_q2_2026.md
in the Obsidian vault, classifies each filament by severity (P0/P1/info), detects
date drift (Italian short dates like "9 Apr", "29 Apr", "ven 11", "sab 11 15:30"),
and emits a Rich-markup section block ready to render in the Textual TUI.

Self-contained: NO Textual import. Only stdlib.

sess.1534 — Polpo M5 Cockpit augmentation.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants — palette (Polpo design tokens) + paths
# ---------------------------------------------------------------------------

# Round 5: palette + path centralizzati in roadmap_common
from roadmap_common import RED, ORANGE, LIME, DIM, TEAL, ROADMAP_Q2 as ROADMAP_PATH, SESSION_CURRENT as SESSION_PATH, KPI_FILE as KPI_PATH

# Severity dot glyphs
DOT_P0 = "\U0001f534"  # red circle
DOT_P1 = "\U0001f7e1"  # yellow circle
DOT_INFO = "\U0001f7e2"  # green circle (used sparingly for info)

# Today — driven by env var for stress test reproducibility, defaults to real today.
def _today() -> date:
    override = os.environ.get("ROADMAP_FILAMENTS_TODAY")
    if override:
        try:
            return datetime.strptime(override, "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


# Italian month abbreviations (3-letter, lowercase) → month index
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

# English fallback month abbreviations (the table mixes "9 Apr" English-style)
_ENG_MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MONTH_LOOKUP = {**_ITA_MONTHS, **_ENG_MONTHS}

# Italian weekday abbreviations (used in stato like "ven 11", "sab 11 15:30")
_ITA_WEEKDAYS = {"lun", "mar", "mer", "gio", "ven", "sab", "dom"}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_CACHE: dict[str, object] = {"ts": 0.0, "data": None, "mtime": None}
_CACHE_TTL_SEC = 60


# ---------------------------------------------------------------------------
# Session cache (separate from roadmap cache)
# ---------------------------------------------------------------------------

_SESSION_CACHE: dict[str, object] = {"ts": 0.0, "content": None, "mtime": None}


def _read_session_content(force: bool = False) -> str:
    """Read session_current.md + KPI.md with 60s TTL cache.

    Concatenates both files so drift rules can match ground-truth facts that
    live in KPI.md (e.g. "Bressan #24 SALDATA tutto", "Kongline #22 PAGATA").
    Returns empty string when both files are missing.
    """
    global _SESSION_CACHE
    now = time.time()
    try:
        mtime_sess = SESSION_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime_sess = 0.0
    try:
        mtime_kpi = KPI_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime_kpi = 0.0
    mtime = (mtime_sess, mtime_kpi)

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
#
# Each rule is a tuple:
#   (name_pattern, resolved_patterns, still_active_patterns, keep_drift_note)
#
# name_pattern   — regex matched against filament name (case-insensitive)
# resolved_pats  — list of regexes; if ANY matches session body → roadmap_stale
# active_pats    — list of regexes; if ANY matches session body → still-active
# keep_drift_note— human note to append when status cannot be resolved
#
# Matching order: resolved_pats takes priority over active_pats.
# If neither matches → unknown.

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
    (
        r"Fondi",
        [],  # always active — skip detection
        [],
        "skip — always active",
    ),
    (
        r"Benincasa.Ads",
        [r"Benincasa.{0,80}(credenziali|tracking.{0,20}ok|installato)"],
        [r"Benincasa.{0,60}BRUCIA"],
        None,
    ),
]


def _match_rule(
    rule: tuple, session_body: str
) -> tuple[str, str]:
    """Return (status, evidence) for a single rule against the session body."""
    _, resolved_pats, active_pats, _ = rule
    flags = re.IGNORECASE | re.DOTALL

    for pat in resolved_pats:
        m = re.search(pat, session_body, flags)
        if m:
            start = max(0, m.start() - 10)
            end = min(len(session_body), m.end() + 60)
            snippet = session_body[start:end].replace("\n", " ").strip()
            if len(snippet) > 120:
                snippet = snippet[:119] + "…"
            return "roadmap_stale", snippet

    for pat in active_pats:
        m = re.search(pat, session_body, flags)
        if m:
            start = max(0, m.start() - 10)
            end = min(len(session_body), m.end() + 60)
            snippet = session_body[start:end].replace("\n", " ").strip()
            if len(snippet) > 120:
                snippet = snippet[:119] + "…"
            return "session_still_active", snippet

    return "unknown", "no match in session_current.md"


def detect_session_drift(filaments: list[dict]) -> dict[str, dict]:
    """Cross-reference each filament against session_current.md.

    Returns mapping name -> {
        'status': 'in_sync' | 'roadmap_stale' | 'session_still_active' | 'skip' | 'unknown',
        'evidence': str,        # excerpt from session_current proving the state
        'roadmap_says': str,    # filament stato field (abbreviated)
        'severity_override': str | None,  # 'info' when roadmap_stale (downgrade red noise)
    }

    Graceful: if SESSION_PATH is missing or unreadable, returns {} (no drift info).
    """
    session_body = _read_session_content()
    if not session_body:
        return {}

    result: dict[str, dict] = {}

    for fil in filaments:
        name: str = fil.get("name", "")
        stato: str = fil.get("stato", "")
        stato_short = stato[:100] + "…" if len(stato) > 100 else stato

        # Find matching rule
        matched_rule = None
        for rule in _DRIFT_RULES:
            name_pat = rule[0]
            if re.search(name_pat, name, re.IGNORECASE):
                matched_rule = rule
                break

        if matched_rule is None:
            result[name] = {
                "status": "unknown",
                "evidence": "no rule defined for this filament",
                "roadmap_says": stato_short,
                "severity_override": None,
            }
            continue

        # Skip rule (always-active filaments like Fondi)
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

        # Map session_still_active → in_sync (roadmap is consistent with session)
        if status == "session_still_active":
            status = "in_sync"

        sev_override = "info" if status == "roadmap_stale" else None

        result[name] = {
            "status": status,
            "evidence": evidence,
            "roadmap_says": stato_short,
            "severity_override": sev_override,
        }

    return result


# ---------------------------------------------------------------------------
# Drift report writer
# ---------------------------------------------------------------------------


def write_drift_report(filaments: list[dict], drift: dict[str, dict], path: str | None = None) -> str:
    """Write a markdown drift summary to /tmp/drift_filamenti_report_sess1534.md.

    Returns the path written to.
    """
    from datetime import datetime as _dt

    out_path = path or "/tmp/drift_filamenti_report_sess1534.md"
    now_ts = _dt.now().isoformat(timespec="seconds")

    lines: list[str] = [
        "# Drift Filamenti Report — sess.1534",
        f"",
        f"Generated: {now_ts}",
        f"session_current.md: {SESSION_PATH}",
        f"roadmap_q2_2026.md: {ROADMAP_PATH}",
        "",
        "## Status Table",
        "",
        "| Filamento | Status | Roadmap dice | Evidenza sessione |",
        "|-----------|--------|--------------|-------------------|",
    ]

    stale_count = 0
    sync_count = 0
    skip_count = 0
    unknown_count = 0

    for fil in filaments:
        name = fil.get("name", "")
        d = drift.get(name, {})
        status = d.get("status", "no-rule")
        roadmap_says = d.get("roadmap_says", fil.get("stato", ""))[:60]
        evidence = d.get("evidence", "")[:80]

        if status == "roadmap_stale":
            stale_count += 1
            status_icon = "STALE"
        elif status == "in_sync":
            sync_count += 1
            status_icon = "in-sync"
        elif status == "skip":
            skip_count += 1
            status_icon = "skip"
        else:
            unknown_count += 1
            status_icon = "unknown"

        # Escape pipes in cells
        roadmap_says = roadmap_says.replace("|", "|")
        evidence = evidence.replace("|", "|")

        lines.append(f"| {name} | {status_icon} | {roadmap_says} | {evidence} |")

    lines += [
        "",
        "## Azioni suggerite per Mattia",
        "",
    ]

    for fil in filaments:
        name = fil.get("name", "")
        d = drift.get(name, {})
        if d.get("status") == "roadmap_stale":
            evidence = d.get("evidence", "")
            lines.append(
                f"- **{name}**: aggiornare roadmap_q2_2026.md — segnare filamento CHIUSO/RISOLTO. ",
            )
            lines.append(f"  Evidenza: _{evidence[:100]}_")
            lines.append("")
        elif d.get("status") == "unknown":
            lines.append(
                f"- **{name}**: nessun segnale in session_current — verificare stato manualmente.",
            )
            lines.append("")

    session_hit = "HIT" if _SESSION_CACHE.get("content") else "MISS"
    lines += [
        "",
        "## Footer",
        "",
        f"- Timestamp: {now_ts}",
        f"- Session cache: {session_hit}",
        f"- Stale: {stale_count} | In-sync: {sync_count} | Skip: {skip_count} | Unknown: {unknown_count}",
        f"- Total filaments: {len(filaments)}",
    ]

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        return f"ERROR writing {out_path}: {exc}"

    return out_path



# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class Filament:
    name: str
    segnale_vita: str
    segnale_morte: str
    stato: str
    severity: str = "info"  # P0 | P1 | info
    days_drift: Optional[int] = None  # positive = days past deadline
    deadline: Optional[date] = None  # parsed deadline (if any)

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
    # backticks wrapping the whole field
    if s.startswith("`") and s.endswith("`") and len(s) >= 2:
        s = s[1:-1].strip()
    # bold **...**
    if s.startswith("**") and s.endswith("**") and len(s) >= 4:
        s = s[2:-2].strip()
    # italic *...*
    elif s.startswith("*") and s.endswith("*") and len(s) >= 2:
        s = s[1:-1].strip()
    return s


def _resolve_year(month: int, today: date) -> int:
    """If a date in the recent past would be >=180 days ago, assume next year.
    Otherwise assume current year. The stato field rarely references >6 months back.
    """
    candidate = date(today.year, month, 1)
    delta = (today - candidate).days
    if delta < -200:  # candidate is far in the future → it's last year
        return today.year - 1
    if delta > 200:  # candidate is far in the past → it's next year
        return today.year + 1
    return today.year


_DATE_PATTERNS = [
    # "9 Apr", "29 Apr", "5 Mag", "11 Giu" (with optional trailing time/comma)
    re.compile(r"\b(\d{1,2})\s+([A-Za-zÀ-ÿ]{3,})\b"),
    # "Apr 9", "May 5"
    re.compile(r"\b([A-Za-z]{3,})\s+(\d{1,2})\b"),
]

_WEEKDAY_DAY_RE = re.compile(
    r"\b(lun|mar|mer|gio|ven|sab|dom)\s+(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?\b",
    re.IGNORECASE,
)


def parse_italian_date(text: str, today: Optional[date] = None) -> Optional[date]:
    """Best-effort parse of the *first* date found in the stato string.

    Returns ``None`` when nothing parseable is detected.
    Supports:
      - "9 Apr", "29 Apr" (English/Italian month abbrev mixed)
      - "Apr 9"
      - "ven 11", "sab 11 15:30" (weekday + day-of-month → resolves nearest matching date)
    """
    if not text:
        return None
    today = today or _today()

    # 1) Try "<day> <month>" or "<month> <day>"
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            g1, g2 = m.group(1), m.group(2)
            if g1.isdigit():
                day_s, mon_s = g1, g2
            else:
                day_s, mon_s = g2, g1
            mon_key = mon_s.lower().strip(".")
            # Avoid matching "scade 29" without month — month must be alpha
            if not mon_key.isalpha():
                continue
            # Skip weekday tokens that accidentally matched "<digit> <weekday>"
            if mon_key[:3] in _ITA_WEEKDAYS and mon_key not in _MONTH_LOOKUP:
                continue
            month = _MONTH_LOOKUP.get(mon_key) or _MONTH_LOOKUP.get(mon_key[:3])
            if not month:
                continue
            try:
                day = int(day_s)
                year = _resolve_year(month, today)
                return date(year, month, day)
            except (ValueError, TypeError):
                continue

    # 2) Try "<weekday> <day>" — find the nearest date matching that weekday/day combo
    m = _WEEKDAY_DAY_RE.search(text)
    if m:
        try:
            from datetime import timedelta
            weekday_name = m.group(1).lower()
            day = int(m.group(2))
            ita_weekday_idx = {"lun": 0, "mar": 1, "mer": 2, "gio": 3, "ven": 4, "sab": 5, "dom": 6}
            target_wd = ita_weekday_idx.get(weekday_name)
            # Walk by absolute offset (closest first), prefer weekday match
            best: Optional[date] = None
            best_score = (10**9, 10**9)  # (weekday_mismatch, |offset|)
            for ad in range(0, 91):
                for sign in (1, -1):
                    if ad == 0 and sign == -1:
                        continue
                    offset = ad * sign
                    candidate = today + timedelta(days=offset)
                    if candidate.day != day:
                        continue
                    wd_mismatch = 0 if (target_wd is None or candidate.weekday() == target_wd) else 1
                    score = (wd_mismatch, abs(offset))
                    if score < best_score:
                        best_score = score
                        best = candidate
                        # Early exit on perfect match
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


def classify_severity(stato: str, deadline: Optional[date], today: date) -> tuple[str, Optional[int]]:
    """Return (severity, days_drift). days_drift positive = past deadline."""
    days_drift: Optional[int] = None
    if deadline:
        delta = (today - deadline).days
        if delta > 0:
            days_drift = delta

    s = stato or ""
    s_upper = s.upper()

    # P0 patterns
    p0_markers = ["\U0001f525", "KIT PRONTO"]  # 🔥
    if any(mk in s for mk in p0_markers):
        return "P0", days_drift
    if "⚡" in s and "PRIORIT" in s_upper:  # ⚡ PRIORITÀ / PRIORITA
        return "P0", days_drift
    if re.search(r"\bCALL\b", s_upper) and not re.search(r"DOPO\s+CALL", s_upper):
        # CALL imminent (skip purely retrospective mentions)
        return "P0", days_drift

    # P1 patterns: ⚠️, WAITING, FOLLOW-UP, DRIFT, ⏳ + drift
    if "⚠" in s:  # ⚠️
        return "P1", days_drift
    if "WAITING" in s_upper or "DRIFT" in s_upper or "FOLLOW-UP" in s_upper or "FOLLOW UP" in s_upper:
        return "P1", days_drift

    if "⏳" in s:  # ⏳ hourglass
        if days_drift is not None and days_drift > 0:
            return "P1", days_drift
        return "info", days_drift

    # Past deadline alone (no special emoji) → P1 drift
    if days_drift is not None and days_drift > 0:
        return "P1", days_drift

    return "info", days_drift


# ---------------------------------------------------------------------------
# Markdown table parser
# ---------------------------------------------------------------------------


def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row into cells, preserving emoji and special chars.

    Drops the leading/trailing pipe and trims each cell.
    """
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

    # Locate "### Filamenti attivi"
    start_idx: Optional[int] = None
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("### filamenti attivi"):
            start_idx = i
            break
    if start_idx is None:
        return []

    # From start_idx, find the next markdown table (header row + separator)
    table_lines: list[str] = []
    in_table = False
    header_seen = False
    for ln in lines[start_idx + 1:]:
        stripped = ln.strip()
        # Stop at next section header
        if stripped.startswith("### ") or stripped.startswith("## "):
            if in_table:
                break
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = _split_table_row(stripped)
            if not header_seen:
                # First pipe row = header
                header_seen = True
                in_table = True
                continue
            if _is_separator_row(cells):
                continue
            table_lines.append(stripped)
            in_table = True
        elif in_table and not stripped:
            # blank line ends the table
            break

    filaments: list[Filament] = []
    for row in table_lines:
        cells = _split_table_row(row)
        if len(cells) < 4:
            continue
        name = _strip_md_emph(cells[0])
        if not name:
            continue
        segnale_vita = _strip_md_emph(cells[1])
        segnale_morte = _strip_md_emph(cells[2])
        stato = _strip_md_emph(cells[3])
        deadline = parse_italian_date(stato, today=today)
        severity, drift = classify_severity(stato, deadline, today)
        filaments.append(
            Filament(
                name=name,
                segnale_vita=segnale_vita,
                segnale_morte=segnale_morte,
                stato=stato,
                severity=severity,
                days_drift=drift,
                deadline=deadline,
            )
        )
    return filaments


# ---------------------------------------------------------------------------
# Public API: read_filaments + render_filaments_section
# ---------------------------------------------------------------------------


def read_filaments(force: bool = False, path: Optional[Path] = None) -> list[dict]:
    """Read & parse filaments from the roadmap. Cached for 60s.

    Returns list of dicts (see Filament.to_dict). On missing file → [].
    """
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
    """Compact a stato cell for single-line render: strip backticks, collapse runs of spaces."""
    s = re.sub(r"\s+", " ", stato).strip()
    # Drop redundant prefixes like the same name repeated, etc — keep as-is mostly
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _render_one_line(f: Filament) -> str:
    """Render a single filament line in Rich markup."""
    name = f.name
    stato = _short_stato(f.stato, max_len=110)
    drift_tag = ""
    if f.days_drift and f.days_drift > 0:
        drift_tag = f" [bold {RED}](DRIFT — D+{f.days_drift})[/]"

    if f.severity == "P0":
        dot, color = DOT_P0, RED
    elif f.severity == "P1":
        dot, color = DOT_P1, ORANGE
    else:
        dot, color = DOT_INFO, LIME

    return f"[{color}]{dot} {name}[/] [{DIM}]·[/] {stato}{drift_tag}"


def render_filaments_section(filaments: Optional[list[dict]] = None) -> str:
    """Render the FILAMENTI section as a Rich-markup block string.

    Integrates detect_session_drift to annotate filaments whose roadmap entry is
    stale (already resolved per session_current.md).  Stale entries show a
    green session-fresh badge and suppress the DRIFT counter noise.

    Backward-compatible: if detect_session_drift fails for any reason the section
    renders exactly as before.
    """
    if filaments is None:
        filaments = read_filaments()

    if not filaments:
        return f"[bold {RED}]━━━ \U0001f331 FILAMENTI RADICI[/] [{DIM}]no roadmap found[/]"

    # --- Session drift cross-check (best-effort, never raises) ---
    try:
        drift_map = detect_session_drift(filaments)
    except Exception:
        drift_map = {}

    # Reconstruct Filament objects for rendering helpers
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
    p1 = sum(1 for o in objs if o.severity == "P1")
    drifted = sum(1 for o in objs if o.days_drift and o.days_drift > 0)
    silent = sum(1 for o in objs if o.severity == "info" and not (o.days_drift or 0) > 0)
    stale_resolved = sum(
        1 for o in objs
        if drift_map.get(o.name, {}).get("status") == "roadmap_stale"
    )

    # Sort: P0 first, then P1 (drift first within P1), then info; stale last
    sev_order = {"P0": 0, "P1": 1, "info": 2}
    def _sort_key(o: Filament) -> tuple:
        is_stale = drift_map.get(o.name, {}).get("status") == "roadmap_stale"
        return (
            int(is_stale),                          # stale entries pushed to end
            sev_order.get(o.severity, 9),
            -(o.days_drift or 0),
        )
    objs.sort(key=_sort_key)

    # Build header with stale-resolved count
    stale_tag = f" · {stale_resolved} stale-resolved ✓" if stale_resolved else ""
    header = (
        f"[bold {RED}]━━━ \U0001f331 FILAMENTI RADICI "
        f"({total} · {p0} fired · {drifted} drift · {silent} silenti{stale_tag}) "
        f"━━━━━━━━━━━━[/]"
    )
    lines = [header]

    for o in objs:
        d_info = drift_map.get(o.name, {})
        d_status = d_info.get("status", "unknown")

        if d_status == "roadmap_stale":
            # Suppress DRIFT marker; add session-fresh badge
            evidence = d_info.get("evidence", "")[:60]
            name_tag = f"[{LIME}]{DOT_INFO} {o.name}[/]"
            stato_short = _short_stato(o.stato, max_len=80)
            fresh_badge = f" [{LIME}]✓ session-fresh: {evidence}[/]"
            lines.append(
                f"{name_tag} [{DIM}]·[/] [{DIM}]{stato_short}[/]{fresh_badge}"
            )
        else:
            lines.append(_render_one_line(o))

    # Footer hint
    session_note = (
        f" · {stale_resolved} stale-resolved" if stale_resolved else ""
    )
    lines.append(
        f"[{DIM}]source:[/] [{TEAL}]roadmap_q2_2026.md[/] [{DIM}]· session_current crosscheck ·"
        f" today {_today().isoformat()} · cache 60s{session_note}[/]"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stress test
# ---------------------------------------------------------------------------


def _selftest() -> int:
    print("=" * 72)
    print("ROADMAP FILAMENTS — SELFTEST (with session-drift detection)")
    print("=" * 72)
    print(f"Roadmap : {ROADMAP_PATH}")
    print(f"Session : {SESSION_PATH}")
    print(f"Exists  : roadmap={ROADMAP_PATH.exists()} session={SESSION_PATH.exists()}")
    print(f"Today   : {_today().isoformat()}")
    print()

    fils = read_filaments(force=True)
    print(f"Parsed: {len(fils)} filaments")
    print("-" * 72)

    # Severity breakdown
    breakdown: dict = {"P0": 0, "P1": 0, "info": 0}
    for f in fils:
        breakdown[f["severity"]] = breakdown.get(f["severity"], 0) + 1
    print(f"Breakdown: P0={breakdown['P0']}  P1={breakdown['P1']}  info={breakdown['info']}")
    drifted = [f for f in fils if (f.get("days_drift") or 0) > 0]
    print(f"Date-drifted (roadmap deadline past): {len(drifted)}")
    print("-" * 72)

    # Per-filament dump
    for i, f in enumerate(fils, 1):
        sev = f["severity"]
        drift = f.get("days_drift")
        deadline = f.get("deadline")
        drift_s = f" D+{drift}" if drift else ""
        deadline_s = f" deadline={deadline}" if deadline else ""
        print(f"  {i:2d}. [{sev:>4}]{drift_s}{deadline_s}  {f['name']}")
        print(f"       vita : {f['segnale_vita']}")
        print(f"       morte: {f['segnale_morte']}")
        print(f"       stato: {f['stato']}")
        print()

    # --- Session drift detection ---
    print("-" * 72)
    print("SESSION DRIFT DETECTION:")
    print("-" * 72)
    drift_map = detect_session_drift(fils)
    if not drift_map:
        print("  (no session content — session_current.md missing or unreadable)")
    else:
        stale_names = []
        sync_names = []
        unknown_names = []
        skip_names = []
        col_w = max((len(f["name"]) for f in fils), default=20) + 2
        print(f"  {'Filamento':<{col_w}} {'Status':<18} {'Evidence'}")
        print(f"  {'-'*col_w} {'-'*18} {'-'*40}")
        for f in fils:
            name = f["name"]
            info = drift_map.get(name, {})
            status = info.get("status", "no-rule")
            evidence = info.get("evidence", "")[:55]
            status_label = {
                "roadmap_stale": "STALE (resolved)",
                "in_sync": "in-sync",
                "skip": "skip",
                "unknown": "unknown",
            }.get(status, status)
            print(f"  {name:<{col_w}} {status_label:<18} {evidence}")
            if status == "roadmap_stale":
                stale_names.append(name)
            elif status == "in_sync":
                sync_names.append(name)
            elif status == "skip":
                skip_names.append(name)
            else:
                unknown_names.append(name)

        print()
        print(f"  Summary: {len(stale_names)} STALE | {len(sync_names)} in-sync | "
              f"{len(skip_names)} skip | {len(unknown_names)} unknown")
        if stale_names:
            print(f"  Stale (roadmap outdated vs session): {stale_names}")

    print("-" * 72)
    print("RENDERED SECTION (Rich markup):")
    print("-" * 72)
    section = render_filaments_section(fils)
    print(section)
    print()

    # --- Write drift report ---
    report_path = write_drift_report(fils, drift_map)
    print(f"Drift report written: {report_path}")
    print()

    # Sanity asserts (non-fatal — print FAIL but exit 0 unless catastrophic)
    failures: list[str] = []
    if not fils:
        failures.append("no filaments parsed (expected ~11)")
    else:
        names = [f["name"] for f in fils]
        for expected in ("Diella", "AuraHome ads", "Marconi Falco"):
            if expected not in names:
                failures.append(f"missing filament: {expected}")
        # Diella stato should mention 9 Apr → drift expected (today=2026-05-04)
        diella = next((f for f in fils if f["name"] == "Diella"), None)
        if diella and (diella.get("days_drift") or 0) == 0:
            failures.append(f"Diella drift not detected (parsed deadline={diella.get('deadline')})")
        # Bressan should be detected as roadmap_stale (SALDATA in session)
        bressan_drift = drift_map.get("Bressan Carne Express", {})
        if bressan_drift.get("status") != "roadmap_stale":
            failures.append(
                f"Bressan Carne Express expected roadmap_stale, got: "
                f"{bressan_drift.get('status')} (evidence: {bressan_drift.get('evidence', '')[:50]})"
            )

    if failures:
        print("[selftest] WARNINGS:")
        for f in failures:
            print(f"  - {f}")
    else:
        print("[selftest] OK — all assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
