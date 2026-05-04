"""
roadmap_traps.py — Sess.1534

Trap detector cross-orizzonte sempre attivo per M5 Watcher Textual TUI cockpit.

5 trap pattern detection (read-only, no disk write):

1. Sì blanket multi-azione (cic. #226) — >5 commit /24h cross-repo senza /trap-check
2. Build-and-abandon — N progetti aperti senza chiusura DONE/COMPLETE/FINAL
3. Memory/Real drift — session_current updated >6h ago o numerico vs KPI.md
4. Consensus vault vs ground truth (cic. #231) — memory_sentinel conflitti/stale
5. Event-stuffing migrato — >40 eventi GCal nelle prossime 4 settimane

Self-contained: solo stdlib + Rich markup inline (NO Textual import).

Public API:
    detect_active_traps() -> list[dict]
    render_traps_banner() -> str

Cache TTL 300s (trap detection è carcere — non per ogni refresh).

Cicatrici onorate:
- read-only detection, mai scrivere su disco
- subprocess timeout max 3s + graceful fallback su tutti i sorgenti
- session_current, calendar_cache, sentinel script possono essere assenti senza crash
- KPI.md frontmatter parsing tollerante (quote/non-quote, numeric con commas)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Round 5: palette + path centralizzati in roadmap_common
from roadmap_common import (
    RED, ORANGE, LIME, DIM, TEAL,
    VAULT_BASE, SESSION_CURRENT, KPI_FILE,
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

# === Soglie ===
TRAP1_COMMIT_THRESHOLD = 5            # >5 commit /24h
TRAP2_PROJECT_THRESHOLD = 3           # >3 progetti aperti senza chiusura
TRAP2_OPEN_DAYS = 7                   # modificati ultimi 7gg
TRAP2_CLOSE_DAYS = 14                 # commit DONE/COMPLETE/FINAL ultimi 14gg
TRAP3_VAULT_FRESH_HOURS = 6           # session_current updated max 6h fa
TRAP3_MRR_DRIFT_TOLERANCE = 50        # delta MRR tollerato
TRAP4_STALE_THRESHOLD = 100           # >100 stale memory
TRAP5_EVENT_THRESHOLD = 40            # >40 eventi futuri 4 settimane

# === Cache TTL 300s (trap detection è carcere) ===
_CACHE: dict = {"ts": 0.0, "data": None}
_TTL_S = 300.0

# Subprocess timeout max
_PROC_TIMEOUT = 3.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

    Round 5 optimization (sess.1534): TRAP 1+2 lanciavano 4-24 git log
    seriali (~480ms cumulativi). Parallelo con max_workers=8 → ~60-100ms.

    Args:
        jobs: list of (cmd, cwd) tuples
    Returns:
        list of stdout strings, same order as input. '' on failure.
    """
    if not jobs:
        return []
    from concurrent.futures import ThreadPoolExecutor
    max_workers = min(8, len(jobs))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(lambda j: _run(j[0], j[1]), jobs))
    return results


def _read_text(path: Path) -> str:
    """Read graceful — '' su qualsiasi errore."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return ""


def _parse_frontmatter(text: str) -> dict:
    """Parser YAML frontmatter minimale (key: value), tollerante a quote/numeric."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)$", line)
        if not kv:
            continue
        key, raw = kv.group(1), kv.group(2).strip()
        # Strip quote
        if (raw.startswith('"') and raw.endswith('"')) or (
            raw.startswith("'") and raw.endswith("'")
        ):
            raw = raw[1:-1]
        out[key] = raw
    return out


def _parse_int(raw: str) -> int | None:
    """Estrae primo intero da stringa, tollerante a separatori (€, ,, .)."""
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace(".", "").replace("€", "").strip()
    m = re.match(r"^-?\d+", cleaned)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# TRAP 1 — Sì blanket multi-azione
# ---------------------------------------------------------------------------

def _check_trap1_si_blanket() -> dict | None:
    total_commits = 0
    repos_with_commits: list[str] = []

    # Round 5 (sess.1534): parallel subprocess su TRACKED_REPOS
    valid_repos = [r for r in TRACKED_REPOS if (r / ".git").exists()]
    jobs = [
        (["git", "log", "--since=24 hours ago", "--oneline"], repo)
        for repo in valid_repos
    ]
    outputs = _run_parallel(jobs)
    for repo, out in zip(valid_repos, outputs):
        n = len([ln for ln in out.splitlines() if ln.strip()])
        if n > 0:
            total_commits += n
            repos_with_commits.append(f"{repo.name}={n}")

    # Trap-check fresh? Se /tmp/last_trap_check.txt modificato ultime 24h → ok
    trap_check_fresh = False
    if TRAP_CHECK_FILE.exists():
        try:
            age_s = time.time() - TRAP_CHECK_FILE.stat().st_mtime
            trap_check_fresh = age_s < 86400  # 24h
        except OSError:
            trap_check_fresh = False

    if total_commits > TRAP1_COMMIT_THRESHOLD and not trap_check_fresh:
        repos_str = ", ".join(repos_with_commits) if repos_with_commits else "?"
        return {
            "trap": "Sì blanket",
            "evidence": (
                f"{total_commits} commit /24h senza /trap-check ({repos_str})"
            ),
            "severity": "P1",
            "mitigation": "Premortem dedicato prima del prossimo batch transition",
            "cicatrice_ref": "cicatrice #226",
        }
    return None


# ---------------------------------------------------------------------------
# TRAP 2 — Build-and-abandon
# ---------------------------------------------------------------------------

def _git_last_commit_ts(repo: Path) -> float | None:
    out = _run(["git", "log", "-1", "--format=%ct"], cwd=repo)
    out = out.strip()
    if not out:
        return None
    try:
        return float(out)
    except ValueError:
        return None


def _git_has_close_commit(repo: Path, since_days: int) -> bool:
    out = _run(
        [
            "git",
            "log",
            f"--since={since_days} days ago",
            "--grep=DONE",
            "--grep=COMPLETE",
            "--grep=FINAL",
            "-i",
            "--oneline",
        ],
        cwd=repo,
    )
    return any(ln.strip() for ln in out.splitlines())


def _check_trap2_build_abandon() -> dict | None:
    if not PROJECTS_DIR.exists():
        return None

    now = time.time()
    open_threshold_s = TRAP2_OPEN_DAYS * 86400

    open_projects: list[str] = []
    closed_projects: list[str] = []

    try:
        subdirs = [p for p in PROJECTS_DIR.iterdir() if p.is_dir()]
    except OSError:
        return None

    # Round 5 (sess.1534): pre-filter via mtime + parallel batch.
    # Stage A: only repos with .git AND mtime entro 7gg → recent_repos
    recent_repos: list[Path] = []
    for sub in subdirs:
        git_dir = sub / ".git"
        if not git_dir.exists():
            continue
        try:
            # mtime di .git/HEAD = ultimo HEAD update (commit/checkout)
            head_path = git_dir / "HEAD"
            ref_mtime = head_path.stat().st_mtime if head_path.exists() else git_dir.stat().st_mtime
            if (now - ref_mtime) > open_threshold_s * 2:
                continue  # filesystem dice "fermo da >14gg" → skip subprocess
        except OSError:
            continue
        recent_repos.append(sub)

    # Stage B: parallel git log -1 per ottenere last_commit_ts
    last_ts_jobs = [
        (["git", "log", "-1", "--format=%ct"], r) for r in recent_repos
    ]
    last_ts_outputs = _run_parallel(last_ts_jobs)
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
    for repo, out in zip(in_window, close_outputs):
        if any(ln.strip() for ln in out.splitlines()):
            closed_projects.append(repo.name)
        else:
            open_projects.append(repo.name)

    n_open = len(open_projects)
    n_closed = len(closed_projects)

    if n_open > TRAP2_PROJECT_THRESHOLD:
        evidence = f"{n_open} progetti aperti, {n_closed} chiusi questa settimana"
        # Aggiungi top 3 nomi per contesto (max compact)
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

def _parse_session_updated(text: str) -> datetime | None:
    """Parse `updated: ISO8601` dal frontmatter session_current."""
    fm = _parse_frontmatter(text)
    raw = fm.get("updated", "")
    if not raw:
        return None
    # Accetta "2026-05-04T21:05" o "2026-05-04T21:05:00" o con offset
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(raw[:19] if len(raw) >= 19 else raw, fmt)
        except ValueError:
            continue
    return None


def _extract_session_mrr(text: str) -> int | None:
    """Cerca pattern MRR €N o mrr: N nel body session_current."""
    fm = _parse_frontmatter(text)
    if "mrr" in fm:
        return _parse_int(fm["mrr"])
    # Pattern body: MRR €4.124 o MRR: 4124 o MRR 4124
    m = re.search(r"MRR[:\s€]*([0-9][0-9.,]*)", text, re.IGNORECASE)
    if m:
        return _parse_int(m.group(1))
    return None


def _check_trap3_memory_drift() -> dict | None:
    text = _read_text(SESSION_CURRENT)
    if not text:
        return None  # session_current assente → skip (non un trap, è un missing source)

    reasons: list[str] = []

    # Sub-check A: vault freshness
    updated = _parse_session_updated(text)
    if updated is not None:
        # Heuristic: assume locale (no tz) → confronto naive con now() locale
        now = datetime.now()
        delta = now - updated
        hours_ago = delta.total_seconds() / 3600
        if hours_ago > TRAP3_VAULT_FRESH_HOURS:
            reasons.append(f"session_current updated {hours_ago:.0f}h ago")
    # Se updated non parsabile, non è di per sé trap (graceful)

    # Sub-check B: drift numerico MRR session vs KPI.md
    session_mrr = _extract_session_mrr(text)
    kpi_text = _read_text(KPI_FILE)
    kpi_fm = _parse_frontmatter(kpi_text)
    kpi_mrr = _parse_int(kpi_fm.get("mrr", ""))
    if session_mrr is not None and kpi_mrr is not None:
        if abs(session_mrr - kpi_mrr) > TRAP3_MRR_DRIFT_TOLERANCE:
            reasons.append(
                f"MRR session={session_mrr} vs KPI={kpi_mrr} (Δ={abs(session_mrr-kpi_mrr)})"
            )

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


def _check_trap4_consensus() -> dict | None:
    if not MEMORY_SENTINEL.exists():
        return None
    out = _run([str(MEMORY_SENTINEL), "--silent"])
    if not out:
        # Try python explicit fallback (sentinel può non avere shebang exec)
        out = _run(["python3", str(MEMORY_SENTINEL), "--silent"])
    if not out:
        return None

    m = _SENTINEL_RX.search(out)
    if not m:
        return None
    files_n = int(m.group(1))
    stale = int(m.group(2))
    conflitti = int(m.group(3))

    if conflitti > 0 or stale > TRAP4_STALE_THRESHOLD:
        parts = []
        if conflitti > 0:
            parts.append(f"{conflitti} conflitti")
        if stale > TRAP4_STALE_THRESHOLD:
            parts.append(f"{stale} stale")
        evidence = f"sentinel: {', '.join(parts)} (su {files_n} file)"
        return {
            "trap": "Consensus vault vs ground truth",
            "evidence": evidence,
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
    # Normalizza Z → +00:00
    s = raw.replace("Z", "+00:00")
    # Tronca microseconds se presenti
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Fallback formati comuni
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[:19] if len(s) >= 19 else s, fmt)
            except ValueError:
                continue
    return None


def _check_trap5_event_stuffing() -> dict | None:
    if not CALENDAR_CACHE.exists():
        return None
    raw = _read_text(CALENDAR_CACHE)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    # Accetta forme comuni: list of events, dict with 'events', dict with 'items'
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
        # Cerca campo start (vari layout)
        start_raw = None
        if "start" in ev:
            s = ev["start"]
            if isinstance(s, dict):
                start_raw = s.get("dateTime") or s.get("date")
            elif isinstance(s, str):
                start_raw = s
        elif "dateTime" in ev:
            start_raw = ev["dateTime"]
        elif "start_time" in ev:
            start_raw = ev["start_time"]

        dt = _parse_event_dt(start_raw)
        if dt is None:
            continue
        # Normalize tz (treat naive as UTC for comparison)
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

def detect_active_traps() -> list[dict]:
    """Esegue tutti i 5 detector e ritorna lista trap attivi.

    Cached 300s. Read-only — nessuna scrittura su disco.
    """
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < _TTL_S:
        return _CACHE["data"]  # type: ignore[return-value]

    traps: list[dict] = []
    for fn in (
        _check_trap1_si_blanket,
        _check_trap2_build_abandon,
        _check_trap3_memory_drift,
        _check_trap4_consensus,
        _check_trap5_event_stuffing,
    ):
        try:
            res = fn()
        except Exception:  # graceful: detector fail ≠ crash banner
            res = None
        if res:
            traps.append(res)

    _CACHE["ts"] = now
    _CACHE["data"] = traps
    return traps


def render_traps_banner() -> str:
    """Banner Rich-markup multi-riga. Stringa vuota se 0 trap attivi.

    Layout:
        [bold #ff3366]🪤 TRAP ACTIVE (N/5)[/]
        [<color>]🪤 <trap> · <evidence>[/] [#8a98ad]→ <mitigation>[/]
        ...
    """
    traps = detect_active_traps()
    if not traps:
        return ""

    n = len(traps)
    # Severity → color (P1=RED, P2=ORANGE, fallback=ORANGE)
    sev_color = {"P1": RED, "P2": ORANGE}

    lines = [f"[bold {RED}]🪤 TRAP ACTIVE ({n}/5)[/]"]
    for t in traps:
        color = sev_color.get(t.get("severity", "P2"), ORANGE)
        trap_name = t.get("trap", "?")
        evidence = t.get("evidence", "?")
        mitig = t.get("mitigation", "")
        line = (
            f"[{color}]🪤 {trap_name} · {evidence}[/]"
            f" [{DIM}]→ {mitig}[/]"
        )
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stress test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("roadmap_traps.py — Stress test sess.1534")
    print("=" * 70)

    # Force fresh detection (bypass cache)
    _CACHE["ts"] = 0.0
    _CACHE["data"] = None

    t0 = time.time()
    active = detect_active_traps()
    elapsed = time.time() - t0

    print(f"\nDetection time: {elapsed:.2f}s")
    print(f"Active traps: {len(active)}/5\n")

    if not active:
        print("  (nessun trap attivo — clean cockpit)")
    else:
        for i, trap in enumerate(active, 1):
            print(f"  [{i}] {trap['trap']} · {trap['severity']}")
            print(f"      Evidence:    {trap['evidence']}")
            print(f"      Mitigation:  {trap['mitigation']}")
            print(f"      Cicatrice:   {trap['cicatrice_ref']}")
            print()

    print("-" * 70)
    print("Rich-markup banner output:")
    print("-" * 70)
    banner = render_traps_banner()
    if banner:
        print(banner)
    else:
        print("(empty — no banner rendered)")
    print("=" * 70)
