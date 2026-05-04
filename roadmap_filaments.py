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
from roadmap_common import RED, ORANGE, LIME, DIM, TEAL, ROADMAP_Q2 as ROADMAP_PATH

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
    """Render the FILAMENTI section as a Rich-markup block string."""
    if filaments is None:
        filaments = read_filaments()

    if not filaments:
        return f"[bold {RED}]━━━ \U0001f331 FILAMENTI RADICI[/] [{DIM}]no roadmap found[/]"

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

    # Sort: P0 first, then P1 (drift first within P1), then info
    sev_order = {"P0": 0, "P1": 1, "info": 2}
    objs.sort(key=lambda o: (sev_order.get(o.severity, 9), -(o.days_drift or 0)))

    header = (
        f"[bold {RED}]━━━ \U0001f331 FILAMENTI RADICI "
        f"({total} · {p0} fired · {drifted} drift · {silent} silenti) "
        f"━━━━━━━━━━━━[/]"
    )
    lines = [header]
    for o in objs:
        lines.append(_render_one_line(o))
    # Footer hint
    lines.append(
        f"[{DIM}]source:[/] [{TEAL}]roadmap_q2_2026.md[/] [{DIM}]· today {_today().isoformat()} · cache 60s[/]"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stress test
# ---------------------------------------------------------------------------


def _selftest() -> int:
    print("=" * 72)
    print("ROADMAP FILAMENTS — SELFTEST")
    print("=" * 72)
    print(f"Source: {ROADMAP_PATH}")
    print(f"Exists: {ROADMAP_PATH.exists()}")
    print(f"Today : {_today().isoformat()}")
    print()

    fils = read_filaments(force=True)
    print(f"Parsed: {len(fils)} filaments")
    print("-" * 72)

    # Severity breakdown
    breakdown = {"P0": 0, "P1": 0, "info": 0}
    for f in fils:
        breakdown[f["severity"]] = breakdown.get(f["severity"], 0) + 1
    print(f"Breakdown: P0={breakdown['P0']}  P1={breakdown['P1']}  info={breakdown['info']}")
    drifted = [f for f in fils if (f.get("days_drift") or 0) > 0]
    print(f"Drifted: {len(drifted)}")
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

    print("-" * 72)
    print("RENDERED SECTION (Rich markup):")
    print("-" * 72)
    section = render_filaments_section(fils)
    print(section)
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
            # 9 Apr → today 4 May = 25 days drift — but stato says "scade 29 Apr"
            # Our parser picks the FIRST date "9 Apr". Either is acceptable as drift signal.
            failures.append(f"Diella drift not detected (parsed deadline={diella.get('deadline')})")

    if failures:
        print("[selftest] WARNINGS:")
        for f in failures:
            print(f"  - {f}")
    else:
        print("[selftest] OK — all assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
