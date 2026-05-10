"""M5 Max data sources — no sudo required.

Filesystem liberation (sess.1510): tutto subprocess macOS-only ha fallback
psutil per Linux/Intel-Mac. Path Mattia-only sostituibili via env / setter.
"""
from __future__ import annotations

import asyncio
import platform
import re
import resource
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

# ── Platform detection ────────────────────────────────────────────────────────
IS_MACOS = sys.platform == 'darwin'
IS_LINUX = sys.platform.startswith('linux')

# Page size autodetect (Apple Silicon = 16384, x86 / Linux = typically 4096).
PAGE_SIZE = resource.getpagesize()


def _sysctl_int(key: str, default: int) -> int:
    if not IS_MACOS:
        return default
    try:
        return int(subprocess.check_output(['sysctl', '-n', key], timeout=5).decode().strip())
    except (subprocess.CalledProcessError, OSError, ValueError, subprocess.TimeoutExpired):
        return default


def _sysctl_str(key: str, default: str = '') -> str:
    if not IS_MACOS:
        return default
    try:
        return subprocess.check_output(['sysctl', '-n', key], timeout=5).decode().strip()
    except (subprocess.CalledProcessError, OSError, ValueError, subprocess.TimeoutExpired):
        return default


def _detect_clusters() -> tuple[int, int]:
    """Auto-detect Apple Silicon (E_CORES, P_CORES). Falls back to psutil split on non-Apple."""
    p = _sysctl_int('hw.perflevel0.physicalcpu', 0)  # performance cluster (larger L2)
    e = _sysctl_int('hw.perflevel1.physicalcpu', 0)  # efficiency cluster
    if p > 0 and e > 0:
        return e, p
    # Fallback: non-Apple-Silicon (Intel Mac, Linux CI). All cores treated as P-cores
    # (no asymmetric cluster detection). E_CORES=0 indica "no efficiency cluster".
    total = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 8
    return 0, total


def _detect_chip_name() -> str:
    """User-friendly chip name for TitleBar. Tries macOS sysctl first, then platform."""
    brand = _sysctl_str('machdep.cpu.brand_string')
    if brand:
        # Examples: "Apple M5 Max", "Apple M2", "Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz"
        if brand.startswith('Apple '):
            return brand[len('Apple '):]
        return brand
    proc = platform.processor() or platform.machine() or 'Unknown CPU'
    return proc


# Apple Silicon clusters — M5 Max (6+12=18), M4 Max (4+10=14), M1 Pro (2+6/2+8), etc.
E_CORES, P_CORES = _detect_clusters()

# Top-level identity for TitleBar (consumed by app.py).
CHIP_NAME = _detect_chip_name()
TOTAL_RAM_GB = psutil.virtual_memory().total // (1024 ** 3)

POLPO_PROCS: dict[str, str] = {
    'ollama': '🧠', 'claude': '🐙', 'python3': '🐍', 'python': '🐍',
    'node': '📦', 'n8n': '⚡', 'redis': '🔴', 'postgres': '🐘',
    'comfyui': '🎨', 'warp': '🚀', 'ngrok': '🌐', 'railway': '🚂',
}


def set_polpo_procs(names: list[tuple[str, str]]) -> None:
    """Append (keyword, emoji) pairs to POLPO_PROCS — never overwrites existing keys.

    Pensato per app.py: l'utente può estendere la lista processi tracciati senza
    forkare data_sources.
    """
    for kw, emoji in names:
        POLPO_PROCS.setdefault(kw, emoji)


def _pressure_label(free_ratio: float, swap_pct: float) -> tuple[str, str]:
    """Pressione memoria condivisa fra macOS e fallback Linux."""
    if free_ratio < 0.05 or swap_pct > 0.90:
        return ('CRITICAL', 'error')
    if free_ratio < 0.15 or swap_pct > 0.70:
        return ('HIGH', 'warning')
    if free_ratio < 0.35 or swap_pct > 0.40:
        return ('MODERATE', 'info')
    return ('NORMAL', 'ok')


def _unified_memory_macos() -> dict:
    # sess.1568b code-review fix: timeout=5 — vm_stat hang freezerebbe TUI refresh loop.
    out = subprocess.check_output(['vm_stat'], timeout=5).decode()
    pages: dict[str, int] = {}
    for line in out.splitlines():
        m = re.match(r'(.+?):\s+([\d]+)', line.strip())
        if m:
            pages[m.group(1).strip()] = int(m.group(2)) * PAGE_SIZE

    total = psutil.virtual_memory().total or 1
    wired      = pages.get('Pages wired down', 0)
    active     = pages.get('Pages active', 0)
    inactive   = pages.get('Pages inactive', 0)
    compressed = pages.get('Pages stored in compressor', 0)
    free       = pages.get('Pages free', 0) + pages.get('Pages speculative', 0)
    swap_info  = psutil.swap_memory()
    swap       = swap_info.used
    swap_pct   = swap / swap_info.total if swap_info.total > 0 else 0.0

    used = total - free
    return {
        'total': total, 'used': used, 'free': free,
        'wired': wired, 'active': active, 'inactive': inactive,
        'compressed': compressed, 'swap': swap,
        'pct': used / total * 100,
        'pressure': _pressure_label(free / total, swap_pct),
    }


def _unified_memory_psutil() -> dict:
    """Fallback Linux/altri — stessa shape del dict macOS.

    Mapping: wired ← used kernel mem, compressed ← buffers, active/inactive ← psutil.
    Mantiene tutte le chiavi consumate da render_mem.
    """
    vm = psutil.virtual_memory()
    swap_info = psutil.swap_memory()
    total = vm.total or 1
    free = vm.available  # equivalente operativo del "free" macOS
    used = total - free
    swap_pct = swap_info.used / swap_info.total if swap_info.total > 0 else 0.0

    # Campi opzionali su Linux/Windows — getattr con default 0 evita AttributeError.
    wired      = getattr(vm, 'used', used)
    active     = getattr(vm, 'active', 0)
    inactive   = getattr(vm, 'inactive', 0)
    compressed = getattr(vm, 'buffers', 0)

    return {
        'total': total, 'used': used, 'free': free,
        'wired': wired, 'active': active, 'inactive': inactive,
        'compressed': compressed, 'swap': swap_info.used,
        'pct': used / total * 100,
        'pressure': _pressure_label(free / total, swap_pct),
    }


def unified_memory() -> dict:
    if IS_MACOS:
        try:
            return _unified_memory_macos()
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            # vm_stat assente / TCC blocca subprocess / hang → degrada a psutil.
            return _unified_memory_psutil()
    return _unified_memory_psutil()


def battery() -> dict:
    """Stato batteria. Su non-macOS usa psutil.sensors_battery() o AC fallback."""
    if IS_MACOS:
        try:
            # sess.1568b code-review fix: timeout=5 evita freeze su pmset hang.
            out = subprocess.check_output(['pmset', '-g', 'ps'], timeout=5).decode()
            charging = 'AC Power' in out or 'charged' in out
            m = re.search(r'(\d+)%', out)
            return {'pct': int(m.group(1)) if m else 100, 'charging': charging}
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            return {'pct': 100, 'charging': True}

    sensors = getattr(psutil, 'sensors_battery', None)
    if sensors is None:
        return {'pct': 100, 'charging': True, 'time_left': 'AC'}
    info = sensors()
    if info is None:
        return {'pct': 100, 'charging': True, 'time_left': 'AC'}
    return {
        'pct': int(info.percent),
        'charging': bool(info.power_plugged),
        'time_left': 'AC' if info.power_plugged else f'{info.secsleft // 60}m',
    }


async def cpu_per_core() -> list[float]:
    """Non-blocking sample — delta dal precedente call.

    sess.1541: era `interval=0.5` (blocking) → 500ms/call → frame_p50 514ms.
    Seed esiste già in M5Watcher.on_mount (psutil.cpu_percent percpu=True
    interval=None), quindi il primo call utile ritorna delta accurato.
    Cicatrice radice del CPU runaway osservato dalla TUI stessa.
    """
    return await asyncio.to_thread(psutil.cpu_percent, percpu=True, interval=None)


def top_processes(n: int = 10) -> list[dict]:
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
        try:
            mi = p.info['memory_info']
            if mi is None:
                continue
            mem_mb = mi.rss / 1024 / 1024
            cpu    = p.info['cpu_percent'] or 0.0
            if mem_mb > 20 or cpu > 0.3:
                procs.append({'pid': p.info['pid'], 'name': p.info['name'][:28],
                               'cpu': cpu, 'mem_mb': mem_mb})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    procs.sort(key=lambda x: x['cpu'], reverse=True)
    return procs[:n]


def tentacoli() -> list[dict]:
    found = []
    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'cmdline']):
        try:
            mi = p.info['memory_info']
            if mi is None:
                continue
            name_lc = p.info['name'].lower()
            cmd = ' '.join(p.info.get('cmdline') or [])
            cmd_lc = cmd.lower()
            # Claude Code renames process to its version (es. '2.1.123') —
            # extend search to cmdline so 🐙 claude tentacoli vengono visti.
            haystack = name_lc + ' ' + cmd_lc
            for kw, emoji in POLPO_PROCS.items():
                if kw in haystack:
                    display_name = p.info['name']
                    if kw == 'claude' and re.match(r'^\d+\.\d+\.\d+$', name_lc):
                        display_name = f'claude {name_lc}'
                    found.append({
                        'pid': p.info['pid'], 'emoji': emoji,
                        'name': display_name[:22],
                        'cpu': p.info['cpu_percent'] or 0.0,
                        'mem_mb': mi.rss / 1024 / 1024,
                        'cmd': cmd[:55],
                    })
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    found.sort(key=lambda x: x['mem_mb'], reverse=True)
    return found[:12]


_disk_snap: object = None
_disk_time: float  = 0.0
_disk_lock = threading.Lock()
_net_snap:  object = None
_net_time:  float  = 0.0
_net_lock  = threading.Lock()


def disk_io_rate() -> dict[str, float]:
    """MB/s read + write since last call."""
    global _disk_snap, _disk_time
    now = time.monotonic()
    curr = psutil.disk_io_counters()
    if curr is None:
        return {'read': 0.0, 'write': 0.0}
    with _disk_lock:
        dt = now - _disk_time if _disk_time else 1.0
        if _disk_snap and dt > 0:
            r = (curr.read_bytes  - _disk_snap.read_bytes)  / 1024 / 1024 / dt
            w = (curr.write_bytes - _disk_snap.write_bytes) / 1024 / 1024 / dt
        else:
            r = w = 0.0
        _disk_snap = curr
        _disk_time = now
    return {'read': max(0.0, r), 'write': max(0.0, w)}


def net_io_rate() -> dict[str, float]:
    """MB/s sent + recv since last call (all interfaces, excluding loopback)."""
    global _net_snap, _net_time
    now = time.monotonic()
    curr = psutil.net_io_counters(pernic=False)
    if curr is None:
        return {'sent': 0.0, 'recv': 0.0}
    with _net_lock:
        dt = now - _net_time if _net_time else 1.0
        if _net_snap and dt > 0:
            s = (curr.bytes_sent - _net_snap.bytes_sent) / 1024 / 1024 / dt
            r = (curr.bytes_recv - _net_snap.bytes_recv) / 1024 / 1024 / dt
        else:
            s = r = 0.0
        _net_snap = curr
        _net_time = now
    return {'sent': max(0.0, s), 'recv': max(0.0, r)}


def load_avg() -> tuple[float, float, float]:
    return psutil.getloadavg()


# ── Polpo Process Triage ───────────────────────────────────────────────────────

import time as _time

BUCKET_SAFE     = 'KILL_SAFE'
BUCKET_CAUTIOUS = 'CAUTIOUS'
BUCKET_KEEP     = 'KEEP'

# MCP servers — figli diretti di Claude Code (node/python)
_MCP_PATTERNS: list[tuple[str, str]] = [
    ('whatsapp-mcp-ts',    'WhatsApp MCP'),
    ('whatsapp',           'WhatsApp MCP'),
    ('hostinger-api-mcp',  'Hostinger MCP'),
    ('context7-mcp',       'Context7 MCP'),
    ('youtube-transcript', 'YouTube MCP'),
    ('mcp-servers/ghl',    'GHL MCP'),
    ('ghl/index.js',       'GHL MCP'),
    ('telegram-mcp',       'Telegram MCP'),
    ('stripe-mcp',         'Stripe MCP'),
    ('firecrawl-mcp',      'Firecrawl MCP'),
    ('sentry-mcp',         'Sentry MCP'),
    ('figma',              'Figma MCP'),
    ('supabase',           'Supabase MCP'),
    ('obsidian-mcp',       'Obsidian MCP'),
    ('cloudinary',         'Cloudinary MCP'),
    ('fal-ai',             'fal.ai MCP'),
    ('n8n-mcp',            'n8n MCP'),
    ('railway-mcp',        'Railway MCP'),
    ('windsor',            'Windsor MCP'),
    ('linkedin-mcp',       'LinkedIn MCP'),
    ('claude_ai_',         'Claude MCP bridge'),
    ('fathom',             'Fathom MCP'),
    ('obsidian',           'Obsidian MCP'),
]

# LaunchAgent daemons Polpo (parent = launchd PID 1)
_LAUNCHAGENT_PATTERNS: list[tuple[str, str, str]] = [
    ('polpo_sentinella',   'Polpo Sentinella',  'com.astra.polpo-sentinella'),
    ('polpo_dream',        'Polpo Dream',        'com.astra.polpo-dream'),
    ('voice_briefing',     'Voice Briefing',     'com.astra.polpo-voice'),
    ('polpo_researcher',   'Researcher',         'com.astra.polpo-researcher'),
    ('polpo_social',       'Polpo Social',       'com.astra.polpo-social'),
    ('sollecitatore',      'Sollecitatore',      'com.astra.polpo-sollecitatore'),
    ('kpi_updater',        'KPI Updater',        'com.astra.kpi-updater'),
    ('session_claim',      'Session Claim',      'com.astra.session-sync'),
    ('memory_backup',      'Memory Backup',      'com.astra.claude-memory-backup'),
    ('daily_briefing',     'Daily Briefing',     'com.astra.daily-briefing'),
    ('infra_healthcheck',  'Infra Health',       'com.astra.infra-healthcheck'),
    ('sites_health',       'Sites Health',       'com.astra.sites-health'),
    ('statusline',         'Statusline',         'com.astra.statusline'),
    ('dream_sync',         'Dream Sync',         'com.astra.dream-sync'),
    ('spore',              'Spore',              'com.astra.spore'),
    ('garden_server',      'Garden Server',      'com.astra.garden-server'),
    ('credential_health',  'Cred Health',        'com.astra.credential-health'),
    ('security_push',      'Security Push',      'com.astra.security-push'),
    ('pipeline_audit',     'Pipeline Audit',     'com.astra.pipeline-audit'),
    ('outreach',           'Outreach Daemon',    'com.astra.outreach.daemon'),
]


def _parent_alive(ppid: int) -> bool:
    try:
        return psutil.Process(ppid).status() != 'zombie'
    except psutil.NoSuchProcess:
        return False


def _child_count(pid: int) -> int:
    try:
        return len(psutil.Process(pid).children())
    except psutil.NoSuchProcess:
        return 0


def _is_claude_code(name: str) -> bool:
    return bool(re.match(r'^\d+\.\d+\.\d+$', name.lower()))


def _match_mcp(haystack: str) -> str | None:
    for pat, label in _MCP_PATTERNS:
        if pat in haystack:
            return label
    return None


def _match_launchagent(haystack: str) -> tuple[str, str] | None:
    for pat, label, la_id in _LAUNCHAGENT_PATTERNS:
        if pat in haystack:
            return label, la_id
    return None


def triage_processes() -> list[dict]:
    """Classifica processi Polpo in KILL_SAFE / CAUTIOUS / KEEP.

    Ogni entry ha: pid, name, label, mem_mb, cpu, bucket, reason, kill_cmd, proc_type
    """
    results: list[dict] = []
    seen: set[int] = set()
    now = _time.time()

    for p in psutil.process_iter([
        'pid', 'ppid', 'name', 'status', 'cpu_percent',
        'memory_info', 'cmdline', 'create_time', 'terminal',
    ]):
        try:
            mi = p.info['memory_info']
            if mi is None:
                continue
            pid     = p.info['pid']
            ppid    = p.info['ppid']
            name    = p.info['name']
            name_lc = name.lower()
            status  = p.info['status']
            cmd     = ' '.join(p.info.get('cmdline') or [])
            haystack = name_lc + ' ' + cmd.lower()
            mem_mb  = mi.rss / 1024 / 1024
            cpu     = p.info['cpu_percent'] or 0.0
            has_tty = p.info.get('terminal') is not None
            age_min = (now - (p.info['create_time'] or now)) / 60

            if mem_mb < 30 or pid in seen:
                continue

            # ── Zombie ────────────────────────────────────────────
            if status == 'zombie':
                results.append({
                    'pid': pid, 'name': name, 'label': 'ZOMBIE',
                    'mem_mb': mem_mb, 'cpu': cpu,
                    'bucket': BUCKET_SAFE,
                    'reason': f'Zombie — parent {ppid} non ha fatto wait()',
                    'kill_cmd': f'kill -9 {pid}',
                    'proc_type': 'zombie',
                })
                seen.add(pid)
                continue

            # ── Claude Code session ────────────────────────────────
            if _is_claude_code(name):
                n_ch = _child_count(pid)
                if has_tty:
                    bucket = BUCKET_KEEP
                    reason = 'Sessione interattiva con TTY attivo'
                elif n_ch > 0:
                    bucket = BUCKET_CAUTIOUS
                    reason = f'Sessione con {n_ch} MCP figli vivi — chiudi da CLI'
                else:
                    bucket = BUCKET_CAUTIOUS
                    reason = f'Sessione non interattiva, idle {age_min:.0f}min'
                results.append({
                    'pid': pid, 'name': name, 'label': f'Claude {name}',
                    'mem_mb': mem_mb, 'cpu': cpu,
                    'bucket': bucket, 'reason': reason,
                    'kill_cmd': f'kill {pid}',
                    'proc_type': 'claude_session',
                })
                seen.add(pid)
                continue

            # ── MCP server ─────────────────────────────────────────
            mcp_label = _match_mcp(haystack)
            is_mcp_node = (
                mcp_label or
                ('mcp' in haystack and name_lc in ('node', 'python', 'python3'))
            )
            if is_mcp_node:
                label = mcp_label or 'MCP (unknown)'
                if not _parent_alive(ppid):
                    bucket = BUCKET_SAFE
                    reason = f'Orfano — parent PID {ppid} non esiste più'
                elif ppid == 1:
                    # Parent è launchd: bridge standalone, non figlio di sessione Claude
                    bucket = BUCKET_CAUTIOUS
                    reason = 'MCP standalone (LaunchAgent) — non legato a sessione Claude'
                else:
                    try:
                        parent_name = psutil.Process(ppid).name()
                    except psutil.NoSuchProcess:
                        parent_name = '?'
                    bucket = BUCKET_KEEP
                    reason = f'Parent PID {ppid} ({parent_name}) vivo'
                results.append({
                    'pid': pid, 'name': name, 'label': label,
                    'mem_mb': mem_mb, 'cpu': cpu,
                    'bucket': bucket, 'reason': reason,
                    'kill_cmd': f'kill {pid}',
                    'proc_type': 'mcp',
                })
                seen.add(pid)
                continue

            # ── LaunchAgent daemon ─────────────────────────────────
            la_match = _match_launchagent(haystack)
            if la_match or ('com.astra' in haystack) or ('polpo' in haystack and ppid == 1):
                label, la_id = la_match or ('Polpo Daemon', 'com.astra.unknown')
                if IS_MACOS:
                    reason = f'LaunchAgent — meglio: launchctl stop {la_id}'
                    kill_cmd = f'launchctl stop {la_id}'
                else:
                    # Linux: niente launchctl. systemctl --user è il pattern equivalente,
                    # con kill -TERM <pid> come fallback universale.
                    reason = f'Daemon — systemctl --user stop {la_id} (fallback: kill {pid})'
                    kill_cmd = f'systemctl --user stop {la_id} || kill -TERM {pid}'
                results.append({
                    'pid': pid, 'name': name, 'label': label,
                    'mem_mb': mem_mb, 'cpu': cpu,
                    'bucket': BUCKET_CAUTIOUS,
                    'reason': reason,
                    'kill_cmd': kill_cmd,
                    'proc_type': 'launchagent',
                })
                seen.add(pid)
                continue

            # ── Ollama ─────────────────────────────────────────────
            if 'ollama' in haystack:
                bucket = BUCKET_CAUTIOUS if cpu < 1.0 else BUCKET_KEEP
                reason = (
                    f'LLM server idle — {mem_mb:.0f}MB, CPU {cpu:.1f}%'
                    if cpu < 1.0 else
                    f'LLM server in uso — CPU {cpu:.1f}%'
                )
                results.append({
                    'pid': pid, 'name': name, 'label': 'Ollama',
                    'mem_mb': mem_mb, 'cpu': cpu,
                    'bucket': bucket, 'reason': reason,
                    'kill_cmd': f'kill {pid}',
                    'proc_type': 'llm',
                })
                seen.add(pid)

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    _order = {BUCKET_SAFE: 0, BUCKET_CAUTIOUS: 1, BUCKET_KEEP: 2}
    results.sort(key=lambda x: (_order.get(x['bucket'], 3), -x['mem_mb']))
    return results


_FOCUS_CACHE: dict = {}
_FOCUS_TIME: float = 0.0
_FOCUS_TTL: float  = 10.0   # seconds

_SESSION_CURRENT_PATHS = [
    Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Astra Digital Marketing/session_current.md",
    Path.home() / "projects/claude-memory/session_current.md",
    Path.home() / "graphify-polpo-core/session_current.md",
]

_DONE_RE   = re.compile(r'^- .{0,40}?\bDONE\b', re.IGNORECASE)
_STATUS_RE = re.compile(r'\b(DONE|LOOP APERTO|IN_PROGRESS|WAITING|PENDING|FIXED|READY|VERIFIED|CREATED)\b')


def current_focus() -> dict:
    """Parse session_current.md to extract active task + last session tesi.

    Returns {session_str, tesi, active_task, active_label, updated_ts}.
    TTL-cached at 10s — safe for asyncio.to_thread.
    """
    global _FOCUS_CACHE, _FOCUS_TIME
    now = time.monotonic()
    if _FOCUS_CACHE and (now - _FOCUS_TIME) < _FOCUS_TTL:
        return _FOCUS_CACHE

    for path in _SESSION_CURRENT_PATHS:
        if not path.exists():
            continue
        try:
            text = path.read_text(errors='ignore')
        except OSError:
            continue

        lines = text.splitlines()

        # ── session number from frontmatter ───────────────────────────────
        session_str = '—'
        for ln in lines[:10]:
            m = re.search(r'session[:\s]+(\d{3,5})', ln, re.I)
            if m:
                session_str = m.group(1)
                break

        # ── updated timestamp ─────────────────────────────────────────────
        updated_ts = ''
        for ln in lines[:10]:
            m = re.search(r'updated[:\s]+(.+)', ln, re.I)
            if m:
                updated_ts = m.group(1).strip().strip("'\"")
                break

        # ── ultima sessione tesi ──────────────────────────────────────────
        tesi = ''
        in_ultima = False
        for ln in lines:
            if ln.startswith('## Ultima sessione'):
                in_ultima = True
                continue
            if in_ultima and ln.startswith('## '):
                break
            if in_ultima:
                m = re.search(r'Tesi:\s*["“]?(.+?)["”]?\s*$', ln)
                if m:
                    tesi = m.group(1).strip()
                    break

        # ── first active (non-DONE) task from "Task in corso" ────────────
        active_task  = ''
        active_label = ''
        in_tasks = False
        for ln in lines:
            if ln.startswith('## Task in corso'):
                in_tasks = True
                continue
            if in_tasks and ln.startswith('## '):
                break
            if in_tasks and ln.startswith('- '):
                body = ln[2:].strip()
                # Skip tasks that are simply DONE with no pending action
                if _DONE_RE.match(ln) and 'PENDING' not in body and 'PUSH PENDING' not in body:
                    continue
                m = _STATUS_RE.search(body)
                active_label = m.group(1) if m else ''
                # Truncate at first ' ---' separator to get clean title
                title = body.split(' ---')[0].strip()
                active_task = title[:72]
                break

        # ── P0 actions from "Prossime azioni" ────────────────────────────
        p0_actions: list[str] = []
        in_prossime = False
        for ln in lines:
            if ln.startswith('## Prossime azioni'):
                in_prossime = True
                continue
            if in_prossime and ln.startswith('## '):
                break
            if in_prossime and ln.startswith('- P0'):
                body = ln[2:].strip()
                p0_actions.append(body[:80])
                if len(p0_actions) >= 4:
                    break

        # ── Key blockers from "Blocchi attivi" ───────────────────────────
        blocchi: list[str] = []
        in_blocchi = False
        for ln in lines:
            if ln.startswith('## Blocchi attivi'):
                in_blocchi = True
                continue
            if in_blocchi and ln.startswith('## '):
                break
            if in_blocchi and ln.startswith('- '):
                body = ln[2:].strip().split(' ---')[0].strip()
                blocchi.append(body[:72])
                if len(blocchi) >= 3:
                    break

        result = {
            'session_str':  session_str,
            'tesi':         tesi[:90] if tesi else '',
            'active_task':  active_task,
            'active_label': active_label,
            'updated_ts':   updated_ts,
            'p0_actions':   p0_actions,
            'blocchi':      blocchi,
        }
        _FOCUS_CACHE = result
        _FOCUS_TIME  = now
        return result

    return {'session_str': '—', 'tesi': '', 'active_task': '', 'active_label': '', 'updated_ts': '', 'p0_actions': [], 'blocchi': []}


# ── Log Feed — cross-system activity stream ────────────────────────────────────

_LOG_SOURCES: list[tuple[str, str, str]] = [
    ("/tmp/lead_alert.log",                                "👤", "GHL Leads"),
    ("~/Library/Logs/astra/crm_alert.log",                 "📋", "CRM Alert"),
    ("~/Library/Logs/astra/session-sync.log",              "🔄", "Session Sync"),
    ("/tmp/setter_rollup.log",                             "📞", "Setter"),
    ("~/Library/Logs/Polpo/health_bot.log",                "💚", "Health Bot"),
    ("/tmp/security-push.log",                             "🔐", "Security"),
    ("~/Library/Logs/Polpo/polpo-voice.log",               "🎤", "Voice"),
    ("/tmp/jarvis_autosend.log",                           "🤖", "Jarvis"),
    ("/tmp/astra_outreach_daemon.out.log",                 "📤", "Outreach"),
    ("~/Library/Logs/astra/baileys.log",                   "💬", "WhatsApp"),
    ("/tmp/polpo_memory_guard.log",                        "🧠", "Memory Guard"),
    ("/tmp/sites_health_server.log",                       "🌐", "Sites Health"),
    ("/tmp/com.polpo.claude-keepalive.log",                "🐙", "Claude"),
    ("~/Library/Logs/astra/vault_rag_server.log",          "🗃", "Vault RAG"),
    ("~/Library/Logs/astra/apple_notes_sync.log",          "📝", "Notes Sync"),
    ("/tmp/astra_outreach_daemon.err.log",                 "⚠️", "Outreach Err"),
]


def set_log_sources(extra: list[tuple[str, str, str]]) -> None:
    """Append (path, emoji, label) tuples a _LOG_SOURCES — non sovrascrive mai.

    Usato da app.py / utenti per registrare log custom senza forkare il modulo.
    Duplicati esatti (stessa tupla) vengono saltati.
    """
    existing = set(_LOG_SOURCES)
    for entry in extra:
        if entry not in existing:
            _LOG_SOURCES.append(entry)
            existing.add(entry)

_TS_RE = [
    re.compile(r'\[[\w_ ]+\s+(\d{2}:\d{2}:\d{2})\]'),     # [service HH:MM:SS]
    re.compile(r'\[(\d{2}:\d{2}:\d{2})\]'),                # [HH:MM:SS]
    re.compile(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})'),  # ISO
    re.compile(r'(\d{2}:\d{2}:\d{2})'),                    # bare HH:MM:SS
]
_NOISE_RE      = re.compile(r'^\[[\w_ ]*\d{2}:\d{2}:\d{2}[\w_ ]*\]\s*')
_ISO_RE        = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s|]*[|\]]*\s*')
_BRACKET_ISO_RE = re.compile(r'^\[\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\]]*\]\s*')
_LEVEL_RE      = re.compile(r'^(?:info|warn|error|debug|critical)\s*[|:]?\s*', re.IGNORECASE)
_SERVICE_RE    = re.compile(r'^\[[\w_-]+\]\s*')   # bare [service] prefix without timestamp

_SKIP_LINES = re.compile(
    r'npm error|npm warn|nohup:|A complete log|No such file or dir|at Object\.'
    r'|non modificata\)|^#{1,3} |^---+$|^-{2,}$|^={2,}$|^╭|^╰|^│\s*$'
    r'|SEZIONE GENERATA|^>\s*Aggiornato|context_detector'
    r'|^[╠╔╗╚╝╞╡╟╢╣╤╥╦╧╨╩╪╫╬├┼┤┬┴─│]+$'   # box-drawing only lines
    r'|^\s*[┤├]\s*$',
    re.IGNORECASE,
)

# CRM Alert: lines without inline timestamp — detect tabular rows (stage breakdown)
# Pattern: "  StageName       N     %" or "  1° Tentativo   15   4%"
_CRM_TABULAR_RE = re.compile(
    r'^\s{1,4}(.{3,30}?)\s{2,}(\d+)\s{2,}(\d+%?)\s*$'
)
# CRM Alert: table header/separator lines to skip
_CRM_TABLE_SKIP = re.compile(
    r'^\s*Stage\s+N\s+%\s*$'
    r'|^[\s─\-═]+$'
    r'|Stage breakdown'
    r'|Setter.*Pipeline.*Audit'
    r'|^[╭╰│╞╡╟╠╣╤╥╦╧╨╩╪╫╬├┼┤┬┴]+',
    re.IGNORECASE,
)

# Sources considered "dead/empty" at startup — used only for audit; does not affect feed
_EMPTY_SOURCES: frozenset[str] = frozenset({
    "Voice",        # polpo-voice.log is 0B (daemon not running)
    "Sites Health", # 0B
    "Claude",       # 0B
    "Outreach Err", # 0B
})

# ── Severity classifier (sess.1534) ────────────────────────────────────────────
# Ogni entry del feed riceve una severity P0|P1|info|noise.
# P0 = revenue at risk, security incident, churn signal — sempre rosso, mai nascosto.
# P1 = lead caldo, WAITING Mattia, follow-up dovuto — giallo, attenzione operativa.
# info = telemetria viva ma non azionabile — neutro, scrolla via.
# noise = errori producer, heartbeat, scan-in-corso — nascosto di default.
_SEV_P0 = re.compile(
    r'\b(churn|TAMPERED|lockdown|CRITICAL|FATAL|panic|crash(?:ed)?|exhaust(?:ed)?|'
    r'kill(?:ed)?\s|breach|leak|expired|scaduto|insolvent|insolvenza|outstanding\s+\xa0?\d|'
    r'D\+(?:[3-9]\d|\d{3,}))'  # D+30 o più = recovery operativo
    r'|🔴|🚨|⛔|💀',
    re.IGNORECASE,
)
_SEV_P1 = re.compile(
    r'\b(nuovo\s+lead|🆕|new\s+lead|ALERT|WAITING|pending|follow[\s-]?up|'
    r'draft\s+(?:pronto|ready)|sollecito|fattura.*scaden|payment\s+due|'
    r'D\+(?:[7-9]|1\d|2\d))'  # D+7..D+29 = sollecito attivo
    r'|⚠️|🟡|🔥|💰',
    re.IGNORECASE,
)
_SEV_NOISE = re.compile(
    r'(?:ASTRA_TG_BOT_TOKEN|TOKEN)\s+non\s+trovato'  # producer fault not user-actionable
    r'|context_detector(?:\s*[:|]\s*running)?'
    r'|n8n:\s*checking'
    r'|GHL:\s*fetching'
    r'|Git:\s*scanning'
    r'|^(?:nohup|npm\s+(?:warn|error)):'
    r'|heartbeat'
    r'|💓\s*alive'                # daemon heartbeat — not user-actionable
    r'|autopilot\s+(?:ON|OFF)'     # outreach daemon polling status
    ,
    re.IGNORECASE,
)


def _classify_severity(source: str, title: str, desc: str) -> str:
    """Return P0|P1|info|noise for a feed entry. Pure-function, regex-only."""
    blob = f'{title} {desc}'.strip()
    # Source-level overrides — il rumore strutturale di un producer noto va a 'noise'
    # senza dover esaminare il contenuto.
    if source == 'Outreach Err':
        return 'P0'  # err stream is always elevated when non-empty
    if _SEV_NOISE.search(blob):
        return 'noise'
    if _SEV_P0.search(blob):
        return 'P0'
    if _SEV_P1.search(blob):
        return 'P1'
    return 'info'


# ── Severity ranking utility ──────────────────────────────────────────────────
_SEV_RANK = {'P0': 0, 'P1': 1, 'info': 2, 'noise': 3}


# ── Burst collapse policy-driven (sess.1534 round 2) ──────────────────────────
# Diversi producer scrivono N line per "evento logico". Per l'utente del cockpit
# è una sola riga. Generalizziamo: ogni source verboso ha una BURST_POLICY che
# definisce window + come fondere title/desc.
#
#   Session Sync → 12 line per run (LaunchAgent 30min)
#   Notes Sync   → 3 line per run (Lettura/Trovate/Synced)
#   Jarvis       → 2-3 line per keystroke (skip + Return inviato × N target)
#   CRM Alert    → 10 line per dump giornaliero (stage breakdown tabular)


def _picker_mrr_outstanding(burst: list[dict]) -> str:
    """Session Sync: pesca la riga Astra Agency con MRR + Outstanding."""
    for b in burst:
        d = b.get('desc') or b.get('title', '')
        if 'MRR' in d or 'Outstanding' in d:
            return d
    return burst[0].get('desc', '')


def _picker_first_non_skip(burst: list[dict]) -> str:
    """Jarvis: la prima riga che non è 'skip' è quella che porta info utili."""
    for b in burst:
        title = b.get('title', '').lower()
        if not title.startswith('skip'):
            d = b.get('desc') or b.get('title', '')
            return d
    return burst[0].get('desc', '')


def _picker_notes_sync_summary(burst: list[dict]) -> str:
    """Notes Sync: cerca la riga 'Synced N notes' (la conclusiva)."""
    for b in burst:
        t = b.get('title', '')
        if 'Synced' in t or 'synced' in t:
            return t  # 'Synced 0 notes, skipped 16 unchanged'
    return burst[0].get('title', '')


_CRM_TABULAR_TITLE_RE = re.compile(r'^(.+?)\s*[×x](\d+)\s*$')


# sess.1534 round 8: GHL Leads + Setter burst pickers
def _picker_ghl_leads_summary(burst: list[dict]) -> str:
    """GHL Leads: pesca il primo title con nome lead, omette ALERT count."""
    for b in burst:
        t = b.get('title', '')
        # Skip ALERT pure ("ALERT", "1 lead nuovo trovato!"); preferisci named lead
        if '🆕' in t or 'lead GHL' in t.lower() or 'Nuovo' in t:
            d = b.get('desc') or t
            return d
    return burst[0].get('desc') or burst[0].get('title', '')


def _picker_setter_rollup_summary(burst: list[dict]) -> str:
    """Setter: aggrega title (telegram OK / snapshot path) in 1 riga compatta."""
    parts: list[str] = []
    for b in burst:
        t = b.get('title', '').strip()
        if t and t.lower() not in ('snapshot',):  # skip path dump
            parts.append(t)
    if parts:
        return ' · '.join(parts[:3])  # max 3 segnali
    return burst[0].get('desc', '')


def _picker_crm_pipeline_totals(burst: list[dict]) -> str:
    """CRM Alert: aggrega count totali da title 'Stage ×N' → riassunto."""
    total = 0
    demo = disco = interest = 0
    for b in burst:
        t = b.get('title', '')
        m = _CRM_TABULAR_TITLE_RE.match(t)
        if not m:
            continue
        stage = m.group(1).strip().lower()
        try:
            n = int(m.group(2))
        except ValueError:
            continue
        total += n
        if 'demo' in stage:
            demo += n
        elif 'disco' in stage:  # discovery / disco fissata
            disco += n
        elif 'interess' in stage or '🔥' in stage:
            interest += n
    parts = [f"{total} lead totali"]
    if interest:
        parts.append(f"🔥 {interest} interessati")
    if demo:
        parts.append(f"⚔️ {demo} demo")
    if disco:
        parts.append(f"🗓️ {disco} discovery")
    return ' · '.join(parts)


# Policy: (source_label, window_sec, title_template, desc_picker, min_count)
# min_count = soglia minima per applicare il collapse (es. 2 = collassa solo se ≥2)
_BURST_POLICIES: list[tuple[str, float, str, callable, int]] = [
    ('Session Sync', 90.0, 'sync run · {n} segnali',           _picker_mrr_outstanding,     2),
    ('Notes Sync',    30.0, 'notes sync · {n} segnali',         _picker_notes_sync_summary,  2),
    ('Jarvis',         5.0, 'autosend · {n} keystroke',         _picker_first_non_skip,      2),
    ('CRM Alert',     60.0, 'pipeline rollup · {n} stages',     _picker_crm_pipeline_totals, 3),
    # sess.1534 round 8: GHL Leads gemelli (🆕 + ALERT) e Setter daily rollup
    ('GHL Leads',     30.0, '{n} lead nuovi GHL',                _picker_ghl_leads_summary,  2),
    ('Setter',        60.0, 'setter rollup · {n} segnali',       _picker_setter_rollup_summary, 2),
]


def _apply_burst_policies(entries: list[dict]) -> list[dict]:
    """Apply each burst policy in order. Entries are pre-sorted time_sort desc.

    Per ogni source con policy attiva, raggruppa entries consecutive (stesso source,
    nella finestra temporale) e le sostituisce con UNA entry consolidata.
    """
    policy_by_source = {p[0]: p for p in _BURST_POLICIES}
    out: list[dict] = []
    burst: list[dict] = []
    burst_source: str | None = None

    def _flush():
        nonlocal burst, burst_source
        if not burst:
            return
        if burst_source is None or burst_source not in policy_by_source:
            out.extend(burst)
            burst = []
            burst_source = None
            return
        _src, _win, tmpl, picker, min_count = policy_by_source[burst_source]
        n = len(burst)
        if n < min_count:
            # Sotto soglia: lascia passare le entry singole senza fondere.
            out.extend(burst)
        else:
            head = dict(burst[0])  # most recent
            head['title'] = tmpl.format(n=n)
            try:
                head['desc'] = picker(burst) or head.get('desc', '')
            except Exception:
                head['desc'] = head.get('desc', '')
            # Eredita worst severity del burst
            worst = min((_SEV_RANK.get(b.get('severity', 'info'), 2) for b in burst), default=2)
            head['severity'] = next((k for k, v in _SEV_RANK.items() if v == worst), 'info')
            out.append(head)
        burst = []
        burst_source = None

    for e in entries:
        src = e.get('source')
        # Source senza policy → flush e pass-through immediato
        if src not in policy_by_source:
            _flush()
            out.append(e)
            continue
        # Source con policy: apri o estendi il burst
        _src, win_sec, _tmpl, _picker, _min = policy_by_source[src]
        if not burst:
            burst.append(e)
            burst_source = src
            continue
        if burst_source == src and abs(burst[0]['time_sort'] - e['time_sort']) <= win_sec:
            burst.append(e)
        else:
            _flush()
            burst.append(e)
            burst_source = src
    _flush()
    return out


# Backward-compat alias — alcune chiamate esterne potrebbero referenziare il
# nome originale. Lasciato come thin wrapper.
def _collapse_session_sync_burst(entries: list[dict]) -> list[dict]:
    return _apply_burst_policies(entries)


# ── Drift sentinel (sess.1534) ────────────────────────────────────────────────
# Sources con mtime > 24h sono "stale" — segnale di tentacolo morto silente.
# Caso noto: baileys.log fermo dal 22 Apr 2026 = canale clienti WA muto.
def source_drift_audit() -> dict:
    """Return {label: {path, age_hours, size_b, status}} for every _LOG_SOURCES.

    status ∈ {'live', 'stale', 'dead', 'missing'}
      live    = mtime < 6h
      stale   = 6h ≤ mtime < 24h
      dead    = mtime ≥ 24h (drift conclamato)
      missing = file inesistente / 0B
    """
    now = _time.time()
    audit: dict[str, dict] = {}
    for raw_path, _emoji, label in _LOG_SOURCES:
        p = Path(raw_path).expanduser()
        info: dict[str, object] = {'path': str(p), 'status': 'missing', 'age_hours': None, 'size_b': 0}
        if p.exists() and p.is_file():
            try:
                st = p.stat()
                age_h = (now - st.st_mtime) / 3600.0
                info['age_hours'] = round(age_h, 1)
                info['size_b']    = st.st_size
                if st.st_size == 0:
                    info['status'] = 'missing'
                elif age_h < 6:
                    info['status'] = 'live'
                elif age_h < 24:
                    info['status'] = 'stale'
                else:
                    info['status'] = 'dead'
            except OSError:
                pass
        audit[label] = info
    return audit


def log_feed_meta() -> dict:
    """Synthesize a metadata snapshot for the ACTIVITY STREAM header.

    Used by app.py to render a dynamic subtitle: '14/16 sources · last 30s ago ·
    2 P0 · 1 stale'. Purely derived from log_feed() + source_drift_audit().
    """
    feed = log_feed()
    audit = source_drift_audit()
    sources_total = len(_LOG_SOURCES)
    sources_live  = sum(1 for v in audit.values() if v['status'] == 'live')
    sources_stale = sum(1 for v in audit.values() if v['status'] == 'stale')
    sources_dead  = sum(1 for v in audit.values() if v['status'] == 'dead')
    sources_missing = sum(1 for v in audit.values() if v['status'] == 'missing')
    p0_count = sum(1 for e in feed if e.get('severity') == 'P0')
    p1_count = sum(1 for e in feed if e.get('severity') == 'P1')
    last_age_sec: float | None = None
    if feed:
        # feed is sorted desc by time_sort — first entry is the most recent
        try:
            last_age_sec = max(0.0, _time.time() - float(feed[0]['time_sort']))
        except (KeyError, TypeError, ValueError):
            last_age_sec = None
    # Lista nomi dead sources per tooltip / log
    drift_labels = sorted(
        [lbl for lbl, v in audit.items() if v['status'] == 'dead']
    )
    return {
        'sources_total':   sources_total,
        'sources_live':    sources_live,
        'sources_stale':   sources_stale,
        'sources_dead':    sources_dead,
        'sources_missing': sources_missing,
        'p0_count':        p0_count,
        'p1_count':        p1_count,
        'last_age_sec':    last_age_sec,
        'drift_labels':    drift_labels,
        'total_entries':   len(feed),
    }


def _tail(path: Path, n: int) -> list[str]:
    """Read last n lines without loading full file."""
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        buf = min(size, n * 250)
        f.seek(max(0, size - buf))
        data = f.read().decode('utf-8', errors='ignore')
    return data.splitlines()[-n:]


def _extract_ts(line: str) -> str:
    for pat in _TS_RE:
        m = pat.search(line)
        if m:
            raw = m.group(1)
            if 'T' in raw or (' ' in raw and '-' in raw):
                return raw.split('T')[-1].split(' ')[-1][:8]
            return raw[:8]
    return ''


def _clean_msg(line: str) -> str:
    """Strip timestamps, level tags, brackets, leading noise from a log line."""
    msg = _BRACKET_ISO_RE.sub('', line).strip()  # [2026-05-03T17:37:46.208901]
    msg = _NOISE_RE.sub('', msg).strip()          # [service HH:MM:SS] prefix
    msg = _ISO_RE.sub('', msg).strip()             # 2026-05-03T20:45:24|
    msg = _LEVEL_RE.sub('', msg).strip()           # info| warn| error|
    msg = _SERVICE_RE.sub('', msg).strip()         # bare [autosend] prefix
    msg = re.sub(r'^[→\-|•✓✗⚠️\[\]\s]+', '', msg).strip()
    return msg


def _make_title(msg: str, source: str = '') -> str:
    """Extract short human-readable title — avoid splitting on timestamps."""
    # CRM Alert tabular rows: "  2° Tentativo   15   4%" → "2° Tentativo ×15"
    if source == 'CRM Alert':
        m = _CRM_TABULAR_RE.match(msg)
        if m:
            stage = m.group(1).strip().rstrip('.')
            count = m.group(2)
            return f"{stage} ×{count}"[:38]
    # Avoid splitting if the first token looks like a timestamp or log metadata
    for sep in (' — ', ' - ', ' | ', ': ', ':'):
        parts = msg.split(sep, 1)
        candidate = parts[0].strip()
        if (len(candidate) >= 4 and len(candidate) <= 40
                and not re.search(r'\d{2}:\d{2}', candidate)
                and len(parts) > 1 and parts[1].strip()):
            return candidate[:38]
    return msg[:38]


def _ts_float(ts: str, fallback: float) -> float:
    """Convert HH:MM:SS into absolute timestamp using file mtime as day anchor.

    Round 8 fix (sess.1534): cross-day entries (ts > file mtime time-of-day)
    are inferred to be from the previous day. Avoids "23:02 of yesterday"
    appearing before "10:00 of today" in desc sort because old impl returned
    second-of-day (0..86400) while fallback was Unix epoch (~10^9 seconds).
    """
    if not ts:
        return fallback
    try:
        h, m, s = ts.split(':')
        sec_of_day = int(h) * 3600 + int(m) * 60 + float(s)
        anchor_dt = _time.localtime(fallback)
        anchor_sec = anchor_dt.tm_hour * 3600 + anchor_dt.tm_min * 60 + anchor_dt.tm_sec
        anchor_midnight = fallback - anchor_sec
        # Allow a tiny grace (~5 min) for clock skew before deciding "yesterday"
        if sec_of_day > anchor_sec + 300:
            return anchor_midnight - 86400 + sec_of_day  # entry da ieri
        return anchor_midnight + sec_of_day              # entry da oggi
    except (ValueError, AttributeError):
        return fallback


_log_feed_cache: list[dict] = []
_log_feed_ts: float = 0.0
_LOG_FEED_TTL = 5.0


def log_feed(max_per_source: int = 25) -> list[dict]:
    """Aggregate log entries from all Polpo sources, newest-first.

    Each entry: {ts, time_sort, emoji, title, source, desc}
    """
    global _log_feed_cache, _log_feed_ts
    now = time.monotonic()
    if now - _log_feed_ts < _LOG_FEED_TTL:
        return _log_feed_cache

    entries: list[dict] = []
    fallback_t = _time.time()

    for raw_path, emoji, label in _LOG_SOURCES:
        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_file():
            continue
        try:
            lines = _tail(path, max_per_source)
            file_ts = path.stat().st_mtime
        except OSError:
            continue

        wall_now = _time.time()
        for i, line in enumerate(reversed(lines)):
            line = line.rstrip()
            if not line or len(line) < 4 or _SKIP_LINES.search(line):
                continue
            # CRM Alert: skip table header/separator lines
            if label == 'CRM Alert' and _CRM_TABLE_SKIP.search(line):
                continue
            ts = _extract_ts(line)
            msg = _clean_msg(line)
            if not msg or len(msg) < 3:
                continue
            title = _make_title(msg, source=label)
            desc = msg[len(title):].lstrip(':— |-').strip()[:72] if msg != title else ''
            # Use file mtime for sort fallback, offset by line position so ordering is stable
            sort_key = _ts_float(ts, file_ts) - i * 0.001
            if not ts:
                ts = _time.strftime('%H:%M:%S', _time.localtime(file_ts))
            # NEW badge: entry is considered "recent" if file was modified within 5 min
            # and this is one of the latest lines (first few reversed, i.e. i < 3)
            is_new = (wall_now - file_ts) < 300 and i < 3
            severity = _classify_severity(label, title, desc)
            # Round 8 (sess.1534): label temporale relativo per entry cross-day.
            # Se sort_key è di ieri+ rispetto a wall_now → display "ieri 23:02"
            # invece di "23:02:15" (che sembra futuro senza data).
            age_sec = wall_now - sort_key
            if age_sec > 86400:
                ts_display = f"{int(age_sec / 86400)}gg fa"
            elif age_sec > 43200:  # > 12h → "ieri" (semantica utente)
                ts_display = f"ieri {ts[:5]}" if ts else f"~{int(age_sec/3600)}h fa"
            else:
                ts_display = ts or _time.strftime('%H:%M:%S', _time.localtime(file_ts))
            entries.append({
                'ts':        ts_display,
                'time_sort': sort_key,
                'emoji':     emoji,
                'is_new':    is_new,
                'title':     title,
                'source':    label,
                'desc':      desc,
                'severity':  severity,
            })

    entries.sort(key=lambda e: e['time_sort'], reverse=True)

    # Deduplicate: keep only the latest entry per (source, title) pair
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for e in entries:
        key = (e['source'], e['title'][:30])
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    # sess.1534 round 2: burst collapse policy-driven — 4 source verbosi
    # (Session Sync, Notes Sync, Jarvis, CRM Alert) collassati in 1 entry/burst.
    deduped = _apply_burst_policies(deduped)

    # sess.1534: noise hidden by default — drop entries classified as 'noise'
    # unless they're the only source still alive (defensive: never empty feed).
    visible = [e for e in deduped if e.get('severity') != 'noise']
    if visible:
        deduped = visible

    _log_feed_cache = deduped[:150]
    _log_feed_ts = now
    return _log_feed_cache


# ═══════════════════════════════════════════════════════════════════════════
# Voice Agents feed — sess.1602 (Polpo Outbound Voice · Marco GEO)
# Sorgenti:
#   - ~/.local/share/polpo_voice_agent/calls/*.json    (1 file per call)
#   - ~/.local/share/polpo_voice_agent/optout.jsonl    (registry permanente)
#   - ~/.local/share/polpo_voice_agent/call_history.jsonl  (frequency cap)
#   - ~/.config/astra/voice_agent_hybrid_policy.yaml   (caps + kill switch)
#   - ~/.config/astra/setters.yaml ai_setter block     (agent_id + enabled)
# ═══════════════════════════════════════════════════════════════════════════
import json as _json
from datetime import datetime as _dt, timezone as _tz, timedelta as _td

_VOICE_TELEMETRY_DIR = Path.home() / ".local" / "share" / "polpo_voice_agent"
_VOICE_CALLS_DIR     = _VOICE_TELEMETRY_DIR / "calls"
_VOICE_OUTBOX_DIR    = _VOICE_TELEMETRY_DIR / "outbox"      # sess.1683 — call dispatched in volo
_VOICE_DLQ_DIR       = _VOICE_TELEMETRY_DIR / "dlq"         # sess.1683 — dead letter queue
_VOICE_DIGESTS_DIR   = _VOICE_TELEMETRY_DIR / "digests"     # sess.1683 — daily JSON digests
_VOICE_OPTOUT_FILE   = _VOICE_TELEMETRY_DIR / "optout.jsonl"
_VOICE_HISTORY_FILE  = _VOICE_TELEMETRY_DIR / "call_history.jsonl"
_VOICE_POLICY_FILE   = Path.home() / ".config" / "astra" / "voice_agent_hybrid_policy.yaml"
_VOICE_SETTERS_FILE  = Path.home() / ".config" / "astra" / "setters.yaml"
_VOICE_COST_CACHE    = Path("/tmp/polpo_voice_agent_cost.json")    # sess.1683 — cost_watchdog
_VOICE_HEALTH_CACHE  = Path("/tmp/polpo_voice_agent_health.json")  # sess.1683 — health_check.py
_VOICE_LIVE_CALL     = Path("/tmp/polpo_marco_live.json")          # sess.1683 — call_watcher_llm.py (live, NO cache)
_VOICE_ACTIVE_CACHE  = Path("/tmp/polpo_voice_agent.json")         # sess.1683 — statusline live

_VOICE_FEED_CACHE: dict = {}
_VOICE_FEED_TS:    float = 0.0
_VOICE_FEED_TTL:   float = 5.0   # 5s — coerente con _refresh_slow tick


def _voice_load_yaml(path: Path) -> dict:
    """Load YAML safely. Richiede pyyaml (in requirements.txt sess.1602).
    Returns {} se file non esiste o yaml non installato (caller deve essere
    defensive sui type — usare _safe_get* helpers).
    """
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
        with path.open() as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except (ImportError, Exception):
        return {}


def _safe_dict(obj, key: str) -> dict:
    """obj.get(key) → dict, sempre. Se manca o non è dict → {}."""
    if not isinstance(obj, dict):
        return {}
    v = obj.get(key)
    return v if isinstance(v, dict) else {}


def _safe_list(obj, key: str) -> list:
    """obj.get(key) → list, sempre. Se manca o non è list → []."""
    if not isinstance(obj, dict):
        return []
    v = obj.get(key)
    return v if isinstance(v, list) else []


def _voice_mask_phone(phone: str) -> str:
    """Maschera numero per privacy: ultimi 4 visibili. '+393331234567' → '+39 *** ***4567'."""
    if not phone:
        return "—"
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) < 4:
        return "+" + "*" * len(digits)
    last4 = digits[-4:]
    if phone.startswith('+39'):
        return f"+39 *** *** {last4}"
    if phone.startswith('+'):
        prefix_end = min(3, len(phone) - 4)
        return f"{phone[:prefix_end]} *** *** {last4}"
    return f"*** *** {last4}"


def _voice_parse_iso(ts_str: str) -> _dt | None:
    """Parse ISO8601 con o senza tz; ritorna datetime aware in UTC. None se invalid."""
    if not ts_str:
        return None
    try:
        # fromisoformat 3.11+ accetta 'Z'; per compatibilità sostituiamo
        s = ts_str.replace('Z', '+00:00')
        d = _dt.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_tz.utc)
        return d.astimezone(_tz.utc)
    except (ValueError, TypeError):
        return None


def _voice_sparkline(counts: list[int]) -> str:
    """Sparkline Unicode 8-step da serie int. Empty → bullets dim."""
    bars = "▁▂▃▄▅▆▇█"
    if not counts:
        return "·" * 7
    mn, mx = min(counts), max(counts)
    if mn == mx:
        # Tutti uguali: se 0 → bottom flat, altrimenti mid
        return (bars[0] if mx == 0 else bars[3]) * len(counts)
    return "".join(bars[int((c - mn) / (mx - mn) * 7)] for c in counts)


def _voice_relative_age(seconds: float) -> str:
    """Human-friendly age: '2s ago', '14m ago', '3h ago'."""
    if seconds < 0:
        return "now"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


def _voice_load_json(path: Path) -> dict:
    """Load JSON file safely → {} se manca/invalid."""
    if not path.exists():
        return {}
    try:
        return _json.loads(path.read_text()) or {}
    except (OSError, ValueError):
        return {}


# sess.1758: ElevenLabs API ground truth per zombie detection live call.
_VOICE_EL_CACHE: dict = {}
_VOICE_EL_CACHE_TS: dict = {}
_VOICE_EL_TTL: float = 60.0  # 60s — call zombie sono lente, no urgenza
_VOICE_EL_KEY_FILE = Path.home() / "claude_voice" / ".env"


def _voice_get_el_key() -> str:
    """Legge ELEVENLABS_API_KEY via grep subprocess (pattern obbligato dal hook).
    Cache in memoria una volta letta. Empty string se file/var mancante.
    """
    cached = _VOICE_EL_CACHE.get('_api_key')
    if cached is not None:
        return cached
    if not _VOICE_EL_KEY_FILE.exists():
        _VOICE_EL_CACHE['_api_key'] = ''
        return ''
    try:
        import subprocess as _sp
        out = _sp.check_output(
            ['grep', '-E', '^ELEVENLABS_API_KEY|^XI_API_KEY', str(_VOICE_EL_KEY_FILE)],
            stderr=_sp.DEVNULL, timeout=2.0,
        ).decode().strip()
        if not out:
            _VOICE_EL_CACHE['_api_key'] = ''
            return ''
        key = out.split('\n')[0].split('=', 1)[1].strip().strip('"').strip("'")
        _VOICE_EL_CACHE['_api_key'] = key
        return key
    except (_sp.CalledProcessError, _sp.TimeoutExpired, OSError, IndexError):
        _VOICE_EL_CACHE['_api_key'] = ''
        return ''


def _voice_check_conv_via_el(conv_id: str) -> dict:
    """Query EL API /convai/conversations/{conv_id} → ground truth start time.

    Returns dict {'found', 'start_unix', 'status', 'duration_secs',
    'call_successful', '_age_s'} o {} su errore. Cache 60s per conv_id.
    """
    if not conv_id:
        return {}
    cache_key = f'conv:{conv_id}'
    now = _time.monotonic()
    cached_ts = _VOICE_EL_CACHE_TS.get(cache_key, 0.0)
    if now - cached_ts < _VOICE_EL_TTL:
        cached = _VOICE_EL_CACHE.get(cache_key)
        if cached is not None:
            return cached
    key = _voice_get_el_key()
    if not key:
        return {}
    try:
        import urllib.request as _ur
        req = _ur.Request(
            f'https://api.elevenlabs.io/v1/convai/conversations/{conv_id}',
            headers={'xi-api-key': key},
        )
        with _ur.urlopen(req, timeout=3.0) as resp:
            payload = _json.loads(resp.read())
        meta = payload.get('metadata') or {}
        start_unix = int(meta.get('start_time_unix_secs') or 0)
        age_s = (_time.time() - start_unix) if start_unix else 0.0
        out = {
            'found': True,
            'start_unix': start_unix,
            'status': str(payload.get('status') or '').lower(),
            'duration_secs': int(meta.get('call_duration_secs') or 0),
            'call_successful': str((payload.get('analysis') or {}).get('call_successful') or 'unknown'),
            '_age_s': max(0.0, age_s),
        }
        _VOICE_EL_CACHE[cache_key] = out
        _VOICE_EL_CACHE_TS[cache_key] = now
        return out
    except Exception:
        return {}


def _voice_list_el_conversations(agent_id: str, limit: int = 50) -> list[dict]:
    """Query EL API /convai/conversations?agent_id=X — ground truth conta call.

    Sostituisce daily_buckets derivati da file disco (calls/*.json) che perdono
    le call `failed`/`busy` (Twilio terminate prima che EL scriva file completo).
    Cache 60s. Return [] su errore o no key.
    """
    if not agent_id:
        return []
    cache_key = f'list:{agent_id}:{limit}'
    now = _time.monotonic()
    cached_ts = _VOICE_EL_CACHE_TS.get(cache_key, 0.0)
    if now - cached_ts < _VOICE_EL_TTL:
        cached = _VOICE_EL_CACHE.get(cache_key)
        if isinstance(cached, list):
            return cached
    key = _voice_get_el_key()
    if not key:
        return []
    try:
        import urllib.request as _ur
        req = _ur.Request(
            f'https://api.elevenlabs.io/v1/convai/conversations'
            f'?agent_id={agent_id}&page_size={int(limit)}',
            headers={'xi-api-key': key},
        )
        with _ur.urlopen(req, timeout=4.0) as resp:
            payload = _json.loads(resp.read())
        convs = payload.get('conversations') or []
        out: list[dict] = []
        for c in convs:
            if not isinstance(c, dict):
                continue
            ts_unix = int(c.get('start_time_unix_secs') or 0)
            out.append({
                'conversation_id': str(c.get('conversation_id') or ''),
                'agent_id':        str(c.get('agent_id') or ''),
                'agent_name':      str(c.get('agent_name') or ''),
                'status':          str(c.get('status') or '').lower(),
                'duration_secs':   int(c.get('call_duration_secs') or 0),
                'start_unix':      ts_unix,
                'call_successful': str(c.get('call_successful') or 'unknown'),
                'message_count':   int(c.get('message_count') or 0),
                'call_summary_title': str(c.get('call_summary_title') or ''),
            })
        _VOICE_EL_CACHE[cache_key] = out
        _VOICE_EL_CACHE_TS[cache_key] = now
        return out
    except Exception:
        return []


# sess.1758 — Intent classifier EL-aware (sostituisce lexicon italiano povero
# che su trascritti reali confonde "no" / "non" con rejection forte).
_EL_BOOKED_KW = (
    'appuntamento', 'fissato', 'fissata', 'discovery', 'prenotato',
    'calendarizzato', 'booking', 'booked', 'scheduled', 'meeting set',
)
_EL_REJECT_KW = (
    'non interessato', 'non chiamatemi', 'rifiut', 'non mi interessa',
    'lasciate stare', 'non chiamate', 'opt-out', 'opt out', 'cancell',
)


def _voice_intent_from_el(ec: dict) -> tuple[str, str]:
    """Classifica intent da una conversation EL (list response schema).

    Sorgenti: call_successful + call_summary_title + duration + status.
    Più affidabile del lexicon-based locale che fallisce su transcript IT.
    """
    cs = (ec.get('call_successful') or '').lower()
    title = (ec.get('call_summary_title') or '').lower()
    dur = int(ec.get('duration_secs') or 0)
    status = (ec.get('status') or '').lower()

    # Booked: success + title contains booking keyword
    if cs == 'success' and any(k in title for k in _EL_BOOKED_KW):
        return ('booked', '✅')
    # Rejected esplicito nel title
    if any(k in title for k in _EL_REJECT_KW):
        return ('rejected', '❌')
    # Failure → rejected o noise in base alla durata
    if cs == 'failure':
        return ('noise', '❌') if dur < 10 else ('rejected', '❌')
    # Initiated mai partita → noise
    if status == 'initiated' and dur == 0:
        return ('noise', '❌')
    # Success: qualified se conversazione vera (>=30s), altrimenti hangup
    if cs == 'success':
        if dur >= 30:
            return ('qualified', '🔥')
        return ('hangup', '⚠')
    # Unknown
    if dur < 10:
        return ('noise', '❌')
    if dur < 60:
        return ('hangup', '⚠')
    return ('unknown', '·')


def _voice_normalize_el_conv(ec: dict) -> dict:
    """Normalizza una conversation EL nello schema usato da calls_norm.

    Compatibile con render `voiceagents-table` + funnel_stats. Nota: phone
    masked è mancante (non in list response), si lascia '—'. Lead/azienda
    sarebbero in dynamic_vars del singolo conv (richiede fetch puntuale,
    non incluso qui per cost).
    """
    ts_unix = int(ec.get('start_unix') or 0)
    if ts_unix > 0:
        ts_dt = _dt.fromtimestamp(ts_unix, tz=_tz.utc)
        time_str = ts_dt.astimezone().strftime('%H:%M')
        ts_sort = float(ts_unix)
    else:
        time_str = '—'
        ts_sort = 0.0
    dur = int(ec.get('duration_secs') or 0)
    cs = (ec.get('call_successful') or '').lower()
    if cs == 'success':
        cs_label = 'OK'
    elif cs == 'failure':
        cs_label = 'NO'
    else:
        cs_label = '?'
    intent_label, intent_emoji = _voice_intent_from_el(ec)
    # Sentiment proxy coerente con override 7gg
    if cs == 'success':
        sent_v = 0.5
    elif cs == 'failure':
        sent_v = -0.5
    else:
        sent_v = 0.0
    title = str(ec.get('call_summary_title') or '')[:80]
    return {
        'time':         time_str,
        'ts_sort':      ts_sort,
        'agent':        str(ec.get('agent_id', ''))[:8] or '—',
        'to_masked':    '—',  # non disponibile in list response
        'lead':         '—',
        'azienda':      '—',
        'duration_s':   dur,
        'status':       (ec.get('status') or '').lower() or '—',
        'outcome':      cs_label,
        'cost_usd':     round(dur * (0.10 / 60.0), 4),  # stima EL pricing
        'summary':      title,
        'intent_label': intent_label,
        'intent_emoji': intent_emoji,
        'sentiment':    sent_v,
        'term_reason':  '',
        'conversation_id': str(ec.get('conversation_id') or ''),
    }


def _voice_read_live_call() -> dict:
    """Lettura LIVE call snapshot (sess.1683) — `/tmp/polpo_marco_live.json`.

    Scritto ogni 2s da `~/scripts/voice_agent/call_watcher_llm.py`. NON cached
    nel _VOICE_FEED_CACHE: deve essere fresh per renderizzare durata che scorre
    e transcript live in m5-watcher.

    Returns:
      - dict raw da JSON se file presente e valido
      - {is_live: False, status: 'watcher_offline', message: ...} se assente/corrotto
    """
    if not _VOICE_LIVE_CALL.exists():
        return {
            'is_live': False,
            'status': 'watcher_offline',
            'message': 'call_watcher_llm.py not running — no /tmp/polpo_marco_live.json',
        }
    try:
        data = _json.loads(_VOICE_LIVE_CALL.read_text())
        if not isinstance(data, dict):
            return {'is_live': False, 'status': 'idle', 'message': 'malformed snapshot'}
        # Stamp ts_age_s per render "X ago" robust
        ts_str = data.get('ts', '')
        ts_dt = _voice_parse_iso(ts_str) if ts_str else None
        if ts_dt:
            age = (_dt.now(_tz.utc) - ts_dt).total_seconds()
            data['_age_s'] = max(0.0, age)
            data['_stale'] = age > 3600  # > 60min
        else:
            data['_age_s'] = 0.0
            data['_stale'] = False

        # sess.1758 ZOMBIE DETECTION — il watcher EL spesso resta incantato
        # su conv `initiated` che Twilio ha terminato (busy/failed): il TUI
        # mostra "live · 0m00s" indefinitamente. Cicatrice madre sess.1758
        # (Francesco Guerra · conv_6701kr441 · 47h fantasma).
        if data.get('is_live'):
            dur = int(data.get('duration_sec') or 0)
            turns = int(data.get('turns_count') or 0)
            status_l = str(data.get('status') or '').lower()
            local_age = float(data.get('_age_s') or 0.0)
            conv_id = str(data.get('conversation_id') or '')

            # PRIMARY: EL API ground truth (start_time_unix_secs).
            # Bypassa watcher locale che riscrive ts ogni 2s mantenendo conv stale.
            zombie = False
            zombie_reason = ''
            el = _voice_check_conv_via_el(conv_id) if conv_id else {}
            if el and el.get('found'):
                el_age = float(el.get('_age_s') or 0.0)
                el_status = el.get('status') or ''
                el_dur = int(el.get('duration_secs') or 0)
                # Zombie se EL dice call vecchia >5min E ancora `initiated`
                # (mai vera connessione), oppure status terminale.
                if el_age > 300 and el_status == 'initiated' and el_dur == 0:
                    zombie = True
                    zombie_reason = (
                        f'EL conv {el_status} da {int(el_age/60)}min · '
                        f'duration={el_dur}s · likely Twilio busy/failed'
                    )
                elif el_status in ('done', 'failed') and el_age > 60:
                    zombie = True
                    zombie_reason = (
                        f'EL conv {el_status} (succ={el.get("call_successful")}) '
                        f'da {int(el_age/60)}min — watcher locale stuck'
                    )
            else:
                # FALLBACK euristico locale (no API key o EL down).
                if status_l == 'initiated' and dur == 0 and turns == 0 and local_age > 90:
                    zombie = True
                    zombie_reason = (
                        f'initiated · 0s · 0 turns · snapshot age {int(local_age)}s '
                        f'(no EL ground truth)'
                    )

            if zombie:
                data['is_live'] = False
                data['_zombie'] = True
                data['_zombie_reason'] = zombie_reason
                data['status'] = 'zombie'
                # sess.1758: età vera EL (47h) per render — file disco è
                # riscritto ogni 2s dal watcher, _age_s locale è fuorviante.
                if el and el.get('_age_s'):
                    data['_el_age_s'] = float(el.get('_age_s'))
                    data['_el_status'] = el.get('status') or ''
                    data['_el_call_successful'] = el.get('call_successful') or ''
        return data
    except (OSError, ValueError) as e:
        return {
            'is_live': False,
            'status': 'idle',
            'message': f'snapshot read error: {e}',
        }


# ── sess.1683 enrichment: intent detection + sentiment lexicon ────────────────
_INTENT_BOOKED_KW   = ("appuntamento", "fissato", "fissata", "booked", "scheduled",
                       "discovery", "prenotato", "calendarizzato")
_INTENT_QUAL_KW     = ("interessato", "qualified", "interested", "booking",
                       "interesse", "valuteremo", "approfondire", "richiamare")
_INTENT_REJECT_KW   = ("non interessato", "non chiamatemi", "rifiuta", "rifiutato",
                       "non mi interessa", "lasciate stare", "non chiamate")
_INTENT_OBJECTION_KW = ("troppo caro", "costo", "obiezione", "ci penso", "non ho tempo",
                        "mandate email", "mandate mail", "no grazie")
_INTENT_CALLBACK_KW = ("callback", "richiamare", "richiami", "richiamatemi",
                       "richiamare più tardi", "call back")

_SENT_POS_KW = ("sì", "perfetto", "bene", "interessante", "ottimo", "certo",
                "va bene", "grazie", "molto bene", "fantastico", "esatto",
                "d'accordo", "volentieri")
_SENT_NEG_KW = ("no", "non", "basta", "lasci", "scocciatura", "sbagli",
                "infastidito", "spam", "non chiami", "non mi interessa")


def _detect_intent(call: dict) -> tuple[str, str]:
    """Classifica intent call → (label, emoji). Lexicon-based, no LLM.

    Priorità: booked > qualified > rejected > objection > callback > noise/hangup.
    """
    summary = (call.get("summary") or "").lower()
    term = (call.get("termination_reason") or "").lower()
    dur = int(call.get("duration_seconds") or call.get("duration_s") or 0)
    status = (call.get("status") or "").lower()

    # Booked: end_call tool + summary contiene booking keyword
    if "end_call" in term and any(k in summary for k in _INTENT_BOOKED_KW):
        return ("booked", "✅")
    # Rejected esplicito
    if any(k in summary for k in _INTENT_REJECT_KW):
        return ("rejected", "❌")
    # Qualified
    if any(k in summary for k in _INTENT_QUAL_KW):
        return ("qualified", "🔥")
    # Objection
    if any(k in summary for k in _INTENT_OBJECTION_KW):
        return ("objection", "⚠")
    # Callback
    if any(k in summary for k in _INTENT_CALLBACK_KW):
        return ("callback", "📞")
    # Hard fail: quota / errore upstream → noise
    if status == "failed" and dur < 10:
        return ("noise", "❌")
    if dur < 10:
        return ("noise", "❌")
    if dur < 60:
        return ("hangup", "⚠")
    return ("unknown", "·")


def _sentiment(call: dict) -> float:
    """Sentiment lexicon-based dai messaggi user del transcript.

    Returns float ~ [-1.0, +1.0] (amplifica × 10 normalizzato per len).
    Transcript vuoto → 0.0.
    """
    transcript = call.get("transcript") or []
    if not isinstance(transcript, list) or not transcript:
        return 0.0
    user_msgs: list[str] = []
    for t in transcript:
        if isinstance(t, dict) and t.get("role") == "user":
            msg = t.get("message") or ""
            if msg:
                user_msgs.append(str(msg).lower())
    if not user_msgs:
        return 0.0
    text = " ".join(user_msgs)
    pos = sum(text.count(w) for w in _SENT_POS_KW)
    neg = sum(text.count(w) for w in _SENT_NEG_KW)
    total_words = max(1, len(text.split()))
    raw = (pos - neg) / total_words * 10.0
    # Clamp [-1.0, +1.0]
    return round(max(-1.0, min(1.0, raw)), 2)


def _sentiment_glyph(s: float | None) -> str:
    """Format sentiment per cella tabella. None / 0 → '·'."""
    if s is None:
        return "·"
    if abs(s) < 0.05:
        return "·"
    sign = "+" if s > 0 else ""
    return f"{sign}{s:.1f}"


def voice_agents_feed() -> dict:
    """Aggrega telemetria voice agent per la TabPane Voice Agents (sess.1602+1683).

    Sorgenti live (sess.1683 fix dati stale):
      - ~/.config/astra/setters.yaml (ai_setter.{enabled, killed_by, killed_at, batch_daily_limit})
      - ~/.local/share/polpo_voice_agent/outbox/*.json (call IN-FLIGHT)
      - ~/.local/share/polpo_voice_agent/calls/*.json (telemetry COMPLETED)
      - ~/.local/share/polpo_voice_agent/dlq/*.json (dead letter queue)
      - ~/.local/share/polpo_voice_agent/optout.jsonl
      - /tmp/polpo_voice_agent_cost.json (cost_watchdog cache)
      - /tmp/polpo_voice_agent_health.json (health_check)

    Returns dict normalizzato — vedi `out` block in fondo.
    """
    global _VOICE_FEED_CACHE, _VOICE_FEED_TS
    now_mono = _time.monotonic()
    if now_mono - _VOICE_FEED_TS < _VOICE_FEED_TTL and _VOICE_FEED_CACHE:
        # Cache hit: ricicla payload pesante MA refresh `live_call` SEMPRE
        # (call_watcher_llm.py scrive ogni 2s, deve apparire fresco anche
        # quando il resto del feed è ancora valido a TTL).
        cached = dict(_VOICE_FEED_CACHE)
        cached['live_call'] = _voice_read_live_call()
        return cached

    errors: list[str] = []
    now_utc = _dt.now(_tz.utc)
    today_utc = now_utc.date()
    month_first_utc = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = today_utc - _td(days=6)

    # ── 1. Setters config (ai_setter block) — kill switch ground truth ────────
    setters = _voice_load_yaml(_VOICE_SETTERS_FILE)
    ai_setter = _safe_dict(setters, 'ai_setter')
    agent_id     = str(ai_setter.get('agent_id') or '').strip()
    phone_id     = str(ai_setter.get('phone_number_id') or '').strip()
    setter_enabled = bool(ai_setter.get('enabled', False))
    enabled = setter_enabled and bool(agent_id)
    agent_id_short = (agent_id[:20] + '…') if len(agent_id) > 21 else (agent_id or '—')
    phone_masked   = _voice_mask_phone(phone_id) if phone_id else '—'
    killed_by = str(ai_setter.get('killed_by') or '').strip()
    killed_at = str(ai_setter.get('killed_at') or '').strip()
    yaml_daily_limit = ai_setter.get('batch_daily_limit')

    # ── 2. Policy YAML (caps + kill switch triggers) ──────────────────────────
    policy = _voice_load_yaml(_VOICE_POLICY_FILE)
    volume_cfg = _safe_dict(policy, 'volume')
    budget_cfg = _safe_dict(policy, 'budget')
    kill_cfg   = _safe_dict(policy, 'kill_switch')
    # Priorità: setters.yaml batch_daily_limit > policy.volume.max_calls_per_day > 80
    if isinstance(yaml_daily_limit, (int, float)) and yaml_daily_limit > 0:
        cap_calls_per_day = int(yaml_daily_limit)
    else:
        cap_calls_per_day = int(volume_cfg.get('max_calls_per_day', 80) or 80)
    budget_max_usd    = float(budget_cfg.get('max_monthly_usd', 500) or 500)
    budget_alert_pct  = float(budget_cfg.get('alert_at_pct', 75) or 75)
    budget_hard_pct   = float(budget_cfg.get('hard_stop_at_pct', 100) or 100)

    # ── 3. Outbox (IN-FLIGHT call dispatched) — sess.1683 ground truth ────────
    in_flight: list[dict] = []
    if _VOICE_OUTBOX_DIR.exists() and _VOICE_OUTBOX_DIR.is_dir():
        try:
            for p in _VOICE_OUTBOX_DIR.glob('*.json'):
                try:
                    rec = _json.loads(p.read_text())
                    if not isinstance(rec, dict):
                        continue
                    status = str(rec.get('status') or '').lower()
                    if status not in ('dispatched', 'spawned', 'pending'):
                        continue
                    ts = _voice_parse_iso(rec.get('updated_at') or rec.get('created_at') or '')
                    age_s = (now_utc - ts).total_seconds() if ts else 0.0
                    dyn = rec.get('dynamic_vars') or {}
                    lead_name = str(dyn.get('NOME_LEAD') or '').strip()
                    azienda = str(dyn.get('NOME_AZIENDA') or '').strip()
                    label = (azienda or lead_name or '—')[:24]
                    in_flight.append({
                        'call_id':   str(rec.get('call_id') or '')[:24],
                        'phone_masked': _voice_mask_phone(str(rec.get('phone') or '')),
                        'lead':      label,
                        'status':    status,
                        'age':       _voice_relative_age(age_s),
                        'age_s':     age_s,
                        'reason':    str(rec.get('reason') or ''),
                        'retry':     int(rec.get('retry_count', 0) or 0),
                    })
                except (OSError, ValueError) as e:
                    errors.append(f"outbox {p.name}: {e}")
        except OSError as e:
            errors.append(f"outbox dir scan: {e}")
    in_flight.sort(key=lambda r: r['age_s'])  # newest first
    in_flight = in_flight[:8]

    # ── 4. Calls telemetry (COMPLETED — calls/*.json) ─────────────────────────
    calls_raw: list[dict] = []
    if _VOICE_CALLS_DIR.exists() and _VOICE_CALLS_DIR.is_dir():
        try:
            for p in _VOICE_CALLS_DIR.glob('*.json'):
                try:
                    rec = _json.loads(p.read_text())
                    if isinstance(rec, dict):
                        calls_raw.append(rec)
                except (OSError, ValueError) as e:
                    errors.append(f"call file {p.name}: {e}")
        except OSError as e:
            errors.append(f"calls dir scan: {e}")

    calls_norm: list[dict] = []
    today_count = 0
    budget_mtd_from_calls = 0.0
    daily_buckets: dict = {}
    sent_buckets: dict = {}    # date → list[float] (per trend 7gg sentiment)

    for rec in calls_raw:
        # Schema retro-compat: dispatch_ts_utc o created_at o ended_at
        ts = (
            _voice_parse_iso(rec.get('dispatch_ts_utc') or '')
            or _voice_parse_iso(rec.get('ended_at') or '')
            or _voice_parse_iso(rec.get('created_at') or '')
        )
        cost = 0.0
        try:
            cost = float((rec.get('cost_estimate') or {}).get('total_usd', 0) or 0)
            if cost == 0.0:
                cost = float(rec.get('cost_usd', 0) or 0)
        except (TypeError, ValueError):
            cost = 0.0

        if ts:
            ts_local_str = ts.astimezone().strftime('%H:%M')
            ts_sort = ts.timestamp()
            d = ts.date()
            if d == today_utc:
                today_count += 1
            if ts >= month_first_utc:
                budget_mtd_from_calls += cost
            if d >= seven_days_ago:
                key = d.isoformat()
                daily_buckets[key] = daily_buckets.get(key, 0) + 1
        else:
            ts_local_str = '—'
            ts_sort = 0.0

        outcome_raw = (rec.get('status') or '').strip().lower()
        cs = rec.get('call_successful')
        if isinstance(cs, bool):
            cs_label = 'OK' if cs else 'NO'
        elif isinstance(cs, str) and cs.lower() in ('true', 'yes', 'success', 'booked'):
            cs_label = 'OK'
        elif isinstance(cs, str) and cs.lower() in ('false', 'no', 'fail', 'failed'):
            cs_label = 'NO'
        else:
            # Fallback: deduci da `status`
            if outcome_raw in ('booked', 'success', 'completed_ok'):
                cs_label = 'OK'
            elif outcome_raw in ('failed', 'error', 'no_answer'):
                cs_label = 'NO'
            else:
                cs_label = '?'

        # Lead name: prima cerca `lead_name`, poi `dynamic_vars.NOME_LEAD`
        lead_str = str(rec.get('lead_name') or '').strip()
        if not lead_str:
            dyn = rec.get('dynamic_vars') or {}
            lead_str = str(dyn.get('NOME_LEAD') or '').strip()
        # Azienda: company → dynamic_vars.NOME_AZIENDA
        azienda_str = str(rec.get('company') or '').strip()
        if not azienda_str:
            dyn = rec.get('dynamic_vars') or {}
            azienda_str = str(dyn.get('NOME_AZIENDA') or '').strip()

        phone_field = rec.get('to_number') or rec.get('phone') or ''
        # Skip phone se è un user_xxx ID ElevenLabs (non un numero E.164)
        if isinstance(phone_field, str) and phone_field.startswith('user_'):
            phone_field = ''

        # Duration: schema-tolerant — duration_seconds (canonical) o duration_s (legacy)
        dur_int = int(rec.get('duration_seconds') or rec.get('duration_s') or 0)

        # Intent + sentiment enrichment (sess.1683)
        intent_label, intent_emoji = _detect_intent(rec)
        sent_val = _sentiment(rec)

        calls_norm.append({
            'time':         ts_local_str,
            'ts_sort':      ts_sort,
            'agent':        str(rec.get('agent_id', ''))[:8] or '—',
            'to_masked':    _voice_mask_phone(str(phone_field)),
            'lead':         (lead_str or '—')[:24],
            'azienda':      (azienda_str or '—')[:18],
            'duration_s':   dur_int,
            'status':       outcome_raw or '—',
            'outcome':      cs_label,
            'cost_usd':     cost,
            'summary':      str(rec.get('summary', '') or '')[:80],
            'intent_label': intent_label,
            'intent_emoji': intent_emoji,
            'sentiment':    sent_val,
            'term_reason':  str(rec.get('termination_reason') or '')[:80],
        })

        # Sentiment bucket per trend 7gg
        if ts and ts.date() >= seven_days_ago:
            key = ts.date().isoformat()
            sent_buckets.setdefault(key, []).append(sent_val)

    calls_norm.sort(key=lambda c: c['ts_sort'], reverse=True)
    recent_calls = calls_norm[:5]  # last 5 completed for "RECENT" panel

    # ── sess.1758: GROUND TRUTH OVERRIDE da ElevenLabs API ────────────────────
    # I file calls/*.json perdono le call `failed`/`busy` (Twilio le termina
    # prima che EL completi il write). Drift osservato: disco 9 vs EL 14.
    # Override emette: today_count, daily_buckets, sentiment proxy 7gg,
    # budget MTD stimato da duration × pricing EL.
    #
    # Pricing: ElevenLabs Conversational AI = ~$0.10/min effective (LLM+TTS+STT).
    # Stima conservativa, sostituibile con per-conv `metadata.cost` se mai
    # esposto in /v1/convai/conversations/{id}.
    _EL_USD_PER_SEC = 0.10 / 60.0  # ~$0.00167/sec
    el_ground_truth = False
    el_today_count = 0
    el_daily_buckets: dict = {}
    el_sent_buckets: dict = {}      # date_iso → list[float] (success +0.5 / failure -0.5)
    el_budget_mtd = 0.0
    el_today_total_cost = 0.0
    el_agent_name = ''
    if agent_id:
        el_convs = _voice_list_el_conversations(agent_id, limit=80)
        if el_convs:
            el_ground_truth = True
            # Pesca agent_name human da prima conv (tutte hanno stesso name).
            for ec in el_convs:
                if ec.get('agent_name'):
                    el_agent_name = ec['agent_name']
                    break
            for ec in el_convs:
                ts_unix = ec.get('start_unix') or 0
                if ts_unix <= 0:
                    continue
                ts_dt = _dt.fromtimestamp(ts_unix, tz=_tz.utc)
                d = ts_dt.date()
                d_iso = d.isoformat()
                # Conta call
                if d == today_utc:
                    el_today_count += 1
                if d >= seven_days_ago:
                    el_daily_buckets[d_iso] = el_daily_buckets.get(d_iso, 0) + 1
                # Sentiment proxy via call_successful — più affidabile del
                # lexicon locale che su trascritti italiani sbaglia spesso.
                cs = (ec.get('call_successful') or '').lower()
                if cs == 'success':
                    sent_v = 0.5
                elif cs == 'failure':
                    sent_v = -0.5
                else:
                    sent_v = 0.0
                if d >= seven_days_ago:
                    el_sent_buckets.setdefault(d_iso, []).append(sent_v)
                # Budget: somma duration × pricing per conv del mese corrente.
                dur = int(ec.get('duration_secs') or 0)
                if dur > 0 and ts_dt >= month_first_utc:
                    el_budget_mtd += dur * _EL_USD_PER_SEC
                if d == today_utc and dur > 0:
                    el_today_total_cost += dur * _EL_USD_PER_SEC
            today_count = el_today_count
            daily_buckets = el_daily_buckets
            # Override sent_buckets globali (sopra erano lexicon-based per i
            # soli file disco — qui passiamo a EL ground truth).
            sent_buckets = el_sent_buckets
            # Override calls_norm: EL list è il record canonico (incluse
            # failed/busy). Disco resta come anagrafica lead (lead/azienda)
            # ma non come fonte conta. Match per conversation_id se disponibile.
            disk_by_id = {}
            for cn in calls_norm:
                # calls_norm dal disco non ha conversation_id esplicito;
                # cerco in raw originale via summary uniqueness — skip se
                # non matchabile. Disco arricchisce solo come fallback.
                pass
            calls_norm_el = [_voice_normalize_el_conv(ec) for ec in el_convs]
            calls_norm_el.sort(key=lambda c: c['ts_sort'], reverse=True)
            calls_norm = calls_norm_el
            recent_calls = calls_norm[:5]

            # ── #8: in_flight EL-aware ────────────────────────────────────
            # Le conv EL `initiated` con start <90s sono call live davvero;
            # quelle >90s sono zombie (Twilio busy/failed). Se EL alive,
            # sostituisce in_flight derivato da outbox/*.json (spesso fuori
            # sync col runtime ElevenLabs).
            now_unix = _time.time()
            el_in_flight: list[dict] = []
            for ec in el_convs:
                ec_status = (ec.get('status') or '').lower()
                ec_start = int(ec.get('start_unix') or 0)
                ec_dur = int(ec.get('duration_secs') or 0)
                age_s = now_unix - ec_start if ec_start else 999999
                if ec_status == 'initiated' and ec_dur == 0 and age_s < 90:
                    el_in_flight.append({
                        'call_id':       (ec.get('conversation_id') or '')[:24],
                        'phone_masked':  '—',
                        'lead':          '(EL initiated)',
                        'status':        'in-flight',
                        'age':           _voice_relative_age(age_s),
                        'age_s':         float(age_s),
                        'reason':        '',
                        'retry':         0,
                    })
            in_flight = el_in_flight  # sostituisce outbox-based

    # 7-day sparkline COUNT (oldest→newest)
    sparkline_counts = []
    for i in range(6, -1, -1):
        d = (today_utc - _td(days=i)).isoformat()
        sparkline_counts.append(daily_buckets.get(d, 0))
    sparkline_str = _voice_sparkline(sparkline_counts)

    # 7-day sparkline SENTIMENT avg (oldest→newest) — sess.1683
    sentiment_daily_avg: list[float] = []
    for i in range(6, -1, -1):
        d = (today_utc - _td(days=i)).isoformat()
        bucket = sent_buckets.get(d, [])
        sentiment_daily_avg.append(sum(bucket) / len(bucket) if bucket else 0.0)
    # Sparkline funziona su int → mappa [-1, +1] → [0, 7]
    sent_int = [int(round((v + 1.0) * 3.5)) for v in sentiment_daily_avg]
    sentiment_sparkline = _voice_sparkline(sent_int) if any(sent_buckets.values()) else "·" * 7

    # ── FUNNEL STATS oggi (sess.1683) ─────────────────────────────────────────
    today_calls_list = [
        c for c in calls_norm
        if c['ts_sort'] and _dt.fromtimestamp(c['ts_sort'], _tz.utc).date() == today_utc
    ]
    funnel_calls = len(today_calls_list)
    # Connected: dur >= 30s AND status non hard-fail
    connected_list = [
        c for c in today_calls_list
        if c['duration_s'] >= 30 and c['status'] not in ('failed', 'no_answer', 'error')
    ]
    funnel_conn = len(connected_list)
    # Qualified: intent label in {qualified, booked} (booked è subset di qualified)
    qualified_list = [
        c for c in today_calls_list
        if c['intent_label'] in ('qualified', 'booked')
    ]
    funnel_qual = len(qualified_list)
    # Booked
    booked_list = [c for c in today_calls_list if c['intent_label'] == 'booked']
    funnel_booked = len(booked_list)

    pct = lambda n, d: (n / d * 100.0) if d > 0 else 0.0  # noqa: E731
    funnel_pct_conn = pct(funnel_conn, funnel_calls)
    funnel_pct_qual = pct(funnel_qual, funnel_calls)
    funnel_pct_booked = pct(funnel_booked, funnel_calls)

    # Avg duration connected (secondi)
    funnel_avg_dur_s = (
        sum(c['duration_s'] for c in connected_list) / len(connected_list)
    ) if connected_list else 0.0
    # Avg cost per booked = sum cost oggi / max(1, booked)
    today_total_cost = sum(c['cost_usd'] for c in today_calls_list)
    funnel_avg_cost_per_booked = today_total_cost / max(1, funnel_booked)

    # Intent mix (oggi) → counts + percentuali
    intent_mix: dict[str, dict] = {}
    for label in ('booked', 'qualified', 'objection', 'rejected', 'callback',
                  'hangup', 'noise', 'unknown'):
        n = sum(1 for c in today_calls_list if c['intent_label'] == label)
        intent_mix[label] = {
            'count': n,
            'pct': pct(n, funnel_calls),
            'emoji': next(
                (c['intent_emoji'] for c in today_calls_list if c['intent_label'] == label),
                {'booked': '✅', 'qualified': '🔥', 'objection': '⚠',
                 'rejected': '❌', 'callback': '📞', 'hangup': '⚠',
                 'noise': '❌', 'unknown': '·'}[label],
            ),
        }

    # Sentiment avg oggi (solo connected)
    sent_today = [c['sentiment'] for c in connected_list if c['sentiment'] is not None]
    sentiment_avg_today = round(sum(sent_today) / len(sent_today), 2) if sent_today else 0.0

    funnel_stats = {
        'calls':              funnel_calls,
        'connected':          funnel_conn,
        'qualified':          funnel_qual,
        'booked':             funnel_booked,
        'pct_connected':      round(funnel_pct_conn, 1),
        'pct_qualified':      round(funnel_pct_qual, 1),
        'pct_booked':         round(funnel_pct_booked, 1),
        'avg_dur_s':          round(funnel_avg_dur_s, 1),
        'avg_cost_per_booked': round(funnel_avg_cost_per_booked, 2),
        'intent_mix':         intent_mix,
        'sentiment_avg':      sentiment_avg_today,
        'sentiment_trend_7gg': sentiment_sparkline,
        'sentiment_daily_avg': [round(v, 2) for v in sentiment_daily_avg],
        'today_total_cost':   round(today_total_cost, 2),
    }

    # ── 5. DLQ count + età (sess.1758: drift visibility) ─────────────────────
    dlq_count = 0
    dlq_oldest_age_h = 0.0
    dlq_newest_age_h = 0.0
    if _VOICE_DLQ_DIR.exists() and _VOICE_DLQ_DIR.is_dir():
        try:
            dlq_files = list(_VOICE_DLQ_DIR.glob('*.json'))
            dlq_count = len(dlq_files)
            if dlq_files:
                mtimes = [f.stat().st_mtime for f in dlq_files]
                oldest = min(mtimes)
                newest = max(mtimes)
                now_unix2 = _time.time()
                dlq_oldest_age_h = max(0.0, (now_unix2 - oldest) / 3600.0)
                dlq_newest_age_h = max(0.0, (now_unix2 - newest) / 3600.0)
        except OSError:
            pass

    # ── 6. Budget MTD — priority: EL API > cost_cache > file disco ────────────
    # sess.1758: cost_watchdog.py spesso muore (cache_fresh=false), e il
    # fallback file disco perde le call non scritte. EL API è la fonte vera.
    cost_cache = _voice_load_json(_VOICE_COST_CACHE)
    if el_ground_truth:
        budget_mtd = round(el_budget_mtd, 2)
        budget_source = 'elevenlabs_api'
    elif cost_cache:
        budget_mtd = float(cost_cache.get('spend_usd', budget_mtd_from_calls) or budget_mtd_from_calls)
        budget_max_usd = float(cost_cache.get('budget_usd', budget_max_usd) or budget_max_usd)
        budget_source = 'cost_watchdog_cache'
    else:
        budget_mtd = budget_mtd_from_calls
        budget_source = 'disk_calls_fallback'

    # ── 7. Opt-out registry + spike 24h ───────────────────────────────────────
    optout_count = 0
    if _VOICE_OPTOUT_FILE.exists():
        try:
            optout_count = sum(
                1 for line in _VOICE_OPTOUT_FILE.read_text().splitlines()
                if line.strip()
            )
        except OSError as e:
            errors.append(f"optout read: {e}")

    optout_24h = 0
    calls_24h = 0
    cutoff = now_utc - _td(hours=24)
    cutoff_ts = cutoff.timestamp()
    # Conta call 24h è SEMPRE calcolato (slegato da optout file presence —
    # sess.1758 fix: prima era 0 se optout file mancante, denominator wrong).
    for c in calls_norm:
        if c.get('ts_sort') and c['ts_sort'] >= cutoff_ts:
            calls_24h += 1
    if _VOICE_OPTOUT_FILE.exists():
        try:
            for line in _VOICE_OPTOUT_FILE.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                    ts = _voice_parse_iso(rec.get('ts', ''))
                    if ts and ts >= cutoff:
                        optout_24h += 1
                except ValueError:
                    continue
        except OSError:
            pass
    optout_spike_pct = (optout_24h / calls_24h * 100.0) if calls_24h else 0.0
    budget_pct = (budget_mtd / budget_max_usd * 100.0) if budget_max_usd > 0 else 0.0

    # ── 8. Health alerts (health_check.py cache) ──────────────────────────────
    # sess.1758: filtra alerts cost_watchdog se EL ground truth è attivo —
    # il budget viene da EL API live, la cache è irrilevante. Inoltre
    # `Cost cache stale: Nonem old` è bug formato in health_check.py
    # (age=None concatenato a 'm old'); finché non si fixa upstream, mute.
    health = _voice_load_json(_VOICE_HEALTH_CACHE)
    health_alerts: list[str] = []
    if health:
        for a in (health.get('alerts') or []):
            if not isinstance(a, str):
                continue
            a_l = a.lower()
            if el_ground_truth and ('cost cache' in a_l or 'cost_watchdog' in a_l):
                continue
            if 'nonem old' in a_l:  # bug formato upstream — sempre filtrato
                continue
            health_alerts.append(a.strip())

    # ── 9. Kill switch status (5 trigger canonici sess.1683) ──────────────────
    # Se policy.yaml ha triggers usa quelli, altrimenti default 5-condition stub.
    triggers = _safe_list(kill_cfg, 'triggers')
    if not triggers:
        triggers = [
            {'reason': '3+ Garante Privacy in 7gg'},
            {'reason': 'Trustpilot 1-star >100 view in 24h'},
            {'reason': 'spike opt-out >10% in 24h'},
            {'reason': 'PEC complaint formale'},
            {'reason': 'budget cap raggiunto'},
        ]
    # sess.1758: hard-coded "0/3 / 0 menzioni / 0 PEC" rimossi —
    # senza data source reale lo stato è 'unknown', non 'ok'.
    # Solo opt-out e budget hanno ground truth (file disco / cost cache).
    kill_status: list[dict] = []
    for trig in triggers:
        if not isinstance(trig, dict):
            continue
        reason = str(trig.get('reason', '?'))
        state = 'unknown'
        detail = 'no data source'
        rl = reason.lower()
        if 'garante' in rl or 'segnalazion' in rl:
            # TODO sess.1758: connettere a registro reclami / RPA Garante.
            detail = 'no data source (manual check)'
            state = 'unknown'
        elif 'trustpilot' in rl or 'virale' in rl:
            # TODO sess.1758: scraping Trustpilot / Reddit / X mentions.
            detail = 'no data source (manual check)'
            state = 'unknown'
        elif 'opt-out' in rl or 'optout' in rl or 'opt out' in rl:
            detail = f"{optout_24h}/{calls_24h or 0} in 24h ({optout_spike_pct:.0f}%)"
            if optout_spike_pct >= 10.0:
                state = 'fired'
            elif optout_spike_pct >= 7.0:
                state = 'warn'
            else:
                state = 'ok'
        elif 'pec' in rl or 'complaint' in rl:
            # TODO sess.1758: IMAP probe PEC mailbox.
            detail = 'no data source (manual check)'
            state = 'unknown'
        elif 'budget' in rl:
            detail = f"${budget_mtd:.2f}/${budget_max_usd:.0f} ({budget_pct:.0f}%)"
            if budget_pct >= budget_hard_pct:
                state = 'fired'
            elif budget_pct >= budget_alert_pct:
                state = 'warn'
            else:
                state = 'ok'
        kill_status.append({'reason': reason, 'state': state, 'detail': detail})

    out = {
        'enabled':           enabled,
        'agent_id':          agent_id,
        'agent_id_short':    agent_id_short,
        'el_ground_truth':   el_ground_truth,
        'agent_name':        el_agent_name,
        'budget_source':     budget_source,
        'phone_masked':      phone_masked,
        'killed_by':         killed_by,
        'killed_at':         killed_at,
        'today_calls':       today_count,
        'cap_calls_per_day': cap_calls_per_day,
        'budget_mtd_usd':    round(budget_mtd, 2),
        'budget_max_usd':    budget_max_usd,
        'budget_pct':        round(budget_pct, 1),
        'budget_alert_pct':  budget_alert_pct,
        'budget_hard_pct':   budget_hard_pct,
        'optout_count':      optout_count,
        'optout_24h':        optout_24h,
        'calls_24h':         calls_24h,
        'optout_spike_pct':  round(optout_spike_pct, 1),
        'in_flight':         in_flight,
        'in_flight_count':   len(in_flight),
        'recent_calls':      recent_calls,
        'dlq_count':         dlq_count,
        'dlq_oldest_age_h':  round(dlq_oldest_age_h, 1),
        'dlq_newest_age_h':  round(dlq_newest_age_h, 1),
        'health_alerts':     health_alerts,
        'calls':             calls_norm[:16],
        'sparkline_7d':      sparkline_str,
        'sparkline_counts':  sparkline_counts,
        'sentiment_sparkline_7d': sentiment_sparkline,
        'sentiment_daily_avg':    [round(v, 2) for v in sentiment_daily_avg],
        'kill_switch_status': kill_status,
        'funnel_stats':      funnel_stats,
        'errors':            errors,
    }
    _VOICE_FEED_CACHE = out
    _VOICE_FEED_TS = now_mono
    # sess.1683 LIVE CALL: NON entra nel cache — letto sempre fresh.
    # Su cache hit (early return sopra) lo iniettiamo dopo lookup.
    out_live = dict(out)
    out_live['live_call'] = _voice_read_live_call()
    return out_live
