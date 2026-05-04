"""roadmap_traps — Trap detector cross-orizzonte sempre attivo per M5 cockpit.

5 trap pattern detection (read-only, no disk write):
1. Sì blanket multi-azione — >5 commit /24h cross-repo senza /trap-check
2. Build-and-abandon — N progetti aperti senza chiusura DONE/COMPLETE/FINAL
3. Memory/Real drift — session_current updated >6h ago o numerico vs KPI.md
4. Consensus vault vs ground truth — memory_sentinel conflitti/stale
5. Event-stuffing migrato — >40 eventi GCal nelle prossime 4 settimane

Self-contained: solo stdlib + Rich markup inline. Cache TTL 300s.

Public API:
    detect_active_traps() -> list[dict]
    render_traps_banner() -> str
"""
from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from roadmap_common import (
    RED, ORANGE, DIM,
    SESSION_CURRENT, KPI_FILE,
    TrapAlert,
    cached,
    parse_frontmatter as _common_parse_frontmatter,
    parse_int_eur as _common_parse_int,
    read_text as _common_read_text,
)

HOME = Path.home()

# Repo cross-repo per Trap 1 + 2
TRACKED_REPOS = [
    HOME / "scripts",
    HOME / "projects" / "m5-watcher",
    HOME / "btc_predictions",
    HOME / "aurahome",
]
PROJECTS_DIR = HOME / "projects"

# Memory sentinel (Trap 4)
MEMORY_SENTINEL = HOME / "scripts" / "memory_sentinel.py"

# Calendar cache (Trap 5)
CALENDAR_CACHE = HOME / ".local" / "share" / "polpo" / "calendar_cache.json"

# Trap-check sentinel (Trap 1)
TRAP_CHECK_FILE = Path("/tmp/last_trap_check.txt")

# Soglie
TRAP1_COMMIT_THRESHOLD = 5            # >5 commit /24h
TRAP2_PROJECT_THRESHOLD = 3           # >3 progetti aperti senza chiusura
TRAP2_OPEN_DAYS = 7                   # modificati ultimi 7gg
TRAP2_CLOSE_DAYS = 14                 # commit DONE/COMPLETE/FINAL ultimi 14gg
TRAP3_VAULT_FRESH_HOURS = 6           # session_current updated max 6h fa
TRAP3_MRR_DRIFT_TOLERANCE = 50        # delta MRR tollerato
TRAP4_STALE_THRESHOLD = 100           # >100 stale memory
TRAP5_EVENT_THRESHOLD = 40            # >40 eventi futuri 4 settimane

_PROC_TIMEOUT = 3.0


# ---------------------------------------------------------------------------
# Helpers (compat aliases per tests che importano direttamente)
# ---------------------------------------------------------------------------

# Alias re-exported per backward compat — tests importano questi simboli.
_parse_frontmatter = _common_parse_frontmatter
_parse_int = _common_parse_int
_read_text = _common_read_text


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    """Subprocess wrapper graceful — return stdout o '' su qualsiasi errore."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=_PROC_TIMEOUT,
            check=False,
        )
        return result.stdout or ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, PermissionError):
        return ""


def _run_parallel(jobs: list[tuple]) -> list[str]:
    """Run multiple subprocess jobs in parallel via ThreadPoolExecutor.

    Round 5 optimization: TRAP 1+2 lanciavano 4-24 git log seriali (~480ms
    cumulativi). Parallelo con max_workers=8 → ~60-100ms.
    """
    if not jobs:
        return []
    max_workers = min(8, len(jobs))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return list(ex.map(lambda j: _run(j[0], j[1]), jobs))


# ---------------------------------------------------------------------------
# TRAP 1 — Sì blanket multi-azione
# ---------------------------------------------------------------------------

def _check_trap1_si_blanket() -> TrapAlert | None:
    valid_repos = [r for r in TRACKED_REPOS if (r / ".git").exists()]
    jobs = [
        (["git", "log", "--since=24 hours ago", "--oneline"], repo)
        for repo in valid_repos
    ]
    outputs = _run_parallel(jobs)

    total_commits = 0
    repos_with_commits: list[str] = []
    for repo, out in zip(valid_repos, outputs):
        n = sum(1 for ln in out.splitlines() if ln.strip())
        if n > 0:
            total_commits += n
            repos_with_commits.append(f"{repo.name}={n}")

    # Trap-check fresh? /tmp/last_trap_check.txt mtime <24h → ok
    trap_check_fresh = False
    if TRAP_CHECK_FILE.exists():
        try:
            import time as _time
            trap_check_fresh = (_time.time() - TRAP_CHECK_FILE.stat().st_mtime) < 86400
        except OSError:
            trap_check_fresh = False

    if total_commits > TRAP1_COMMIT_THRESHOLD and not trap_check_fresh:
        repos_str = ", ".join(repos_with_commits) if repos_with_commits else "?"
        return {
            "trap": "Sì blanket",
            "evidence": f"{total_commits} commit /24h senza /trap-check ({repos_str})",
            "severity": "P1",
            "mitigation": "Premortem dedicato prima del prossimo batch transition",
            "cicatrice_ref": "cicatrice #226",
        }
    return None


# ---------------------------------------------------------------------------
# TRAP 2 — Build-and-abandon
# ---------------------------------------------------------------------------

def _check_trap2_build_abandon() -> TrapAlert | None:
    if not PROJECTS_DIR.exists():
        return None

    import time as _time
    now = _time.time()
    open_threshold_s = TRAP2_OPEN_DAYS * 86400

    try:
        subdirs = [p for p in PROJECTS_DIR.iterdir() if p.is_dir()]
    except OSError:
        return None

    # Stage A: filtra repos con .git AND mtime entro 14gg (2x soglia)
    recent_repos: list[Path] = []
    for sub in subdirs:
        git_dir = sub / ".git"
        if not git_dir.exists():
            continue
        try:
            head_path = git_dir / "HEAD"
            ref_mtime = (
                head_path.stat().st_mtime if head_path.exists()
                else git_dir.stat().st_mtime
            )
            if (now - ref_mtime) > open_threshold_s * 2:
                continue
        except OSError:
            continue
        recent_repos.append(sub)

    # Stage B: parallel git log -1 → keep solo repos in finestra 7gg
    last_ts_outputs = _run_parallel(
        [(["git", "log", "-1", "--format=%ct"], r) for r in recent_repos]
    )
    in_window: list[Path] = []
    for repo, out in zip(recent_repos, last_ts_outputs):
        out = out.strip()
        if not out:
            continue
        try:
            last_ts = float(out)
        except ValueError:
            continue
        if (now - last_ts) <= open_threshold_s:
            in_window.append(repo)

    # Stage C: parallel git log --grep DONE/COMPLETE/FINAL su soli in_window
    close_jobs = [
        (
            [
                "git", "log",
                f"--since={TRAP2_CLOSE_DAYS} days ago",
                "--grep=DONE", "--grep=COMPLETE", "--grep=FINAL",
                "-i", "--oneline",
            ],
            repo,
        )
        for repo in in_window
    ]
    close_outputs = _run_parallel(close_jobs)
    open_projects: list[str] = []
    closed_projects: list[str] = []
    for repo, out in zip(in_window, close_outputs):
        if any(ln.strip() for ln in out.splitlines()):
            closed_projects.append(repo.name)
        else:
            open_projects.append(repo.name)

    n_open = len(open_projects)
    if n_open > TRAP2_PROJECT_THRESHOLD:
        evidence = (
            f"{n_open} progetti aperti, {len(closed_projects)} chiusi questa settimana"
        )
        top = ", ".join(open_projects[:3])
        if top:
            evidence += f" (es. {top})"
        return {
            "trap": "Build-and-abandon",
            "evidence": evidence,
            "severity": "P1",
            "mitigation": "Kill criterion numerico per ogni progetto nuovo",
            "cicatrice_ref": "TUI Factory + Control Panel + Soul Forge",
        }
    return None


# ---------------------------------------------------------------------------
# TRAP 3 — Memory/Real drift
# ---------------------------------------------------------------------------

_SESSION_DT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
)


def _parse_session_updated(text: str) -> datetime | None:
    """Parse `updated: ISO8601` dal frontmatter session_current."""
    raw = _parse_frontmatter(text).get("updated", "")
    if not raw:
        return None
    truncated = raw[:19] if len(raw) >= 19 else raw
    for fmt in _SESSION_DT_FORMATS:
        try:
            return datetime.strptime(truncated, fmt)
        except ValueError:
            continue
    return None


def _extract_session_mrr(text: str) -> int | None:
    """Cerca pattern MRR €N o mrr: N nel session_current.

    Round 7 fix: prefer frontmatter — body può citare MRR storici nella
    narrativa causando falsi positivi su Trap 3 (drift Δ=500 vs ground truth).

    Round 10 antifragile (sess.1534): ritorna sentinel `-1` quando ENTRAMBI
    frontmatter+body parsing falliscono pur essendoci stato un match — Trap 3
    distingue cosi' "MRR_UNPARSEABLE" (config bug) da "no MRR mentioned"
    (None). Caller in `_check_trap3_memory_drift` consuma -1 per emettere
    un trap dedicato MRR_UNPARSEABLE invece di drift drift silenziato.
    """
    parse_attempted = False

    fm = _parse_frontmatter(text)
    if "mrr" in fm:
        parse_attempted = True
        parsed = _parse_int(fm["mrr"])
        if parsed is not None:
            return parsed
    m = re.search(r"MRR[:\s€]*([0-9][0-9.,]*)", text, re.IGNORECASE)
    if m:
        parse_attempted = True
        parsed = _parse_int(m.group(1))
        if parsed is not None:
            return parsed
    if parse_attempted:
        return -1  # sentinel: MRR mentioned but parsing failed
    return None


def _check_trap3_memory_drift() -> TrapAlert | None:
    text = _read_text(SESSION_CURRENT)
    if not text:
        return None

    reasons: list[str] = []

    # A: vault freshness
    updated = _parse_session_updated(text)
    if updated is not None:
        hours_ago = (datetime.now() - updated).total_seconds() / 3600
        if hours_ago > TRAP3_VAULT_FRESH_HOURS:
            reasons.append(f"session_current updated {hours_ago:.0f}h ago")

    # B: drift numerico MRR session vs KPI.md
    session_mrr = _extract_session_mrr(text)
    kpi_mrr = _parse_int(_parse_frontmatter(_read_text(KPI_FILE)).get("mrr", ""))
    if session_mrr == -1:
        # Sentinel sess.1534 round 10: MRR mentioned but parse failed.
        reasons.append("MRR_UNPARSEABLE in session_current (frontmatter+body)")
    elif session_mrr is not None and kpi_mrr is not None:
        delta = abs(session_mrr - kpi_mrr)
        if delta > TRAP3_MRR_DRIFT_TOLERANCE:
            reasons.append(f"MRR session={session_mrr} vs KPI={kpi_mrr} (Δ={delta})")

    if not reasons:
        return None

    return {
        "trap": "Memory/Real drift",
        "evidence": " · ".join(reasons),
        "severity": "P1",
        "mitigation": "/check ground truth + refresh session_current",
        "cicatrice_ref": "vault dichiara stati diversi dalla realtà",
    }


# ---------------------------------------------------------------------------
# TRAP 4 — Consensus vault vs ground truth
# ---------------------------------------------------------------------------

_SENTINEL_RX = re.compile(
    r"(\d+)\s*file\D+(\d+)\s*stale\D+(\d+)\s*conflitti(?:\D+(\d+)\s*low-conf)?",
    re.IGNORECASE,
)


def _check_trap4_consensus() -> TrapAlert | None:
    if not MEMORY_SENTINEL.exists():
        return None
    out = _run([str(MEMORY_SENTINEL), "--silent"])
    if not out:
        # Fallback: sentinel può non avere shebang exec
        out = _run(["python3", str(MEMORY_SENTINEL), "--silent"])
    if not out:
        return None

    m = _SENTINEL_RX.search(out)
    if not m:
        return None
    files_n   = int(m.group(1))
    stale     = int(m.group(2))
    conflitti = int(m.group(3))

    if conflitti > 0 or stale > TRAP4_STALE_THRESHOLD:
        parts: list[str] = []
        if conflitti > 0:
            parts.append(f"{conflitti} conflitti")
        if stale > TRAP4_STALE_THRESHOLD:
            parts.append(f"{stale} stale")
        return {
            "trap": "Consensus vault vs ground truth",
            "evidence": f"sentinel: {', '.join(parts)} (su {files_n} file)",
            "severity": "P1" if conflitti > 0 else "P2",
            "mitigation": "memory_sentinel risolve conflitti + archive stale",
            "cicatrice_ref": "cicatrice #231",
        }
    return None


# ---------------------------------------------------------------------------
# TRAP 5 — Event-stuffing
# ---------------------------------------------------------------------------

def _parse_event_dt(raw) -> datetime | None:
    """Parse ISO timestamp da entry calendar_cache."""
    if not isinstance(raw, str) or not raw:
        return None
    s = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[:19] if len(s) >= 19 else s, fmt)
            except ValueError:
                continue
    return None


def _extract_event_start(ev: dict) -> str | None:
    """Estrae il campo start da un evento calendar (vari layout)."""
    if "start" in ev:
        s = ev["start"]
        if isinstance(s, dict):
            return s.get("dateTime") or s.get("date")
        if isinstance(s, str):
            return s
    if "dateTime" in ev:
        return ev["dateTime"]
    if "start_time" in ev:
        return ev["start_time"]
    return None


def _check_trap5_event_stuffing() -> TrapAlert | None:
    if not CALENDAR_CACHE.exists():
        return None
    raw = _read_text(CALENDAR_CACHE)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    # Forme comuni: list of events, dict with 'events'/'items'/'calendar'/'data'
    events = None
    if isinstance(data, list):
        events = data
    elif isinstance(data, dict):
        for key in ("events", "items", "calendar", "data"):
            if key in data and isinstance(data[key], list):
                events = data[key]
                break
    if events is None:
        return None

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(weeks=4)
    future_count = 0

    for ev in events:
        if not isinstance(ev, dict):
            continue
        dt = _parse_event_dt(_extract_event_start(ev))
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if now <= dt <= horizon:
            future_count += 1

    if future_count > TRAP5_EVENT_THRESHOLD:
        return {
            "trap": "Event-stuffing",
            "evidence": f"{future_count} eventi futuri in 4 settimane",
            "severity": "P2",
            "mitigation": "Consolida sub-milestone GCal in batch settimanali",
            "cicatrice_ref": "roadmap fragmenta in N sub-milestone",
        }
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_TRAP_FUNCS = (
    _check_trap1_si_blanket,
    _check_trap2_build_abandon,
    _check_trap3_memory_drift,
    _check_trap4_consensus,
    _check_trap5_event_stuffing,
)


@cached(ttl=300.0)
def detect_active_traps() -> list[TrapAlert]:
    """Esegue tutti i 5 detector e ritorna lista trap attivi. Cached 300s.

    Antifragile (sess.1534 round 10): un detector che esplode diventa esso
    stesso un trap (DETECTOR_FAILED P1) invece di sparire silenziosamente.
    """
    traps: list[TrapAlert] = []
    for fn in _TRAP_FUNCS:
        try:
            res = fn()
        except Exception as e:  # detector failure = trap
            traps.append({
                "trap": "DETECTOR_FAILED",
                "evidence": f"{fn.__name__}: {type(e).__name__}: {e}"[:200],
                "severity": "P1",
                "mitigation": "Investigate detector regression",
                "cicatrice_ref": "antifragile-immune sess.1512",
            })
            res = None
        if res:
            traps.append(res)
    return traps


# Backward-compat shim per tests che fanno _CACHE['ts'] = 0.0 reset.
_CACHE = detect_active_traps._cache_state  # type: ignore[attr-defined]


_TRAP_SEVERITY_COLOR = {"P1": RED, "P2": ORANGE}


def render_traps_banner() -> str:
    """Banner Rich-markup multi-riga. Stringa vuota se 0 trap attivi."""
    traps = detect_active_traps()
    if not traps:
        return ""

    lines = [f"[bold {RED}]🪤 TRAP ACTIVE ({len(traps)}/5)[/]"]
    for t in traps:
        color = _TRAP_SEVERITY_COLOR.get(t.get("severity", "P2"), ORANGE)
        line = (
            f"[{color}]🪤 {t.get('trap', '?')} · {t.get('evidence', '?')}[/]"
            f" [{DIM}]→ {t.get('mitigation', '')}[/]"
        )
        lines.append(line)
    return "\n".join(lines)
