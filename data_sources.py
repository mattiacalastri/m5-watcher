"""M5 Max data sources — no sudo required."""
from __future__ import annotations

import asyncio
import re
import subprocess
import threading
import time
from pathlib import Path

import psutil

PAGE_SIZE = 16384  # Apple Silicon page size (bytes)


def _sysctl_int(key: str, default: int) -> int:
    try:
        return int(subprocess.check_output(['sysctl', '-n', key]).decode().strip())
    except (subprocess.CalledProcessError, OSError, ValueError):
        return default


def _detect_clusters() -> tuple[int, int]:
    """Auto-detect Apple Silicon (E_CORES, P_CORES). Falls back to psutil split on non-Apple."""
    p = _sysctl_int('hw.perflevel0.physicalcpu', 0)  # performance cluster (larger L2)
    e = _sysctl_int('hw.perflevel1.physicalcpu', 0)  # efficiency cluster
    if p > 0 and e > 0:
        return e, p
    # Fallback: non-Apple-Silicon (Intel Mac, Linux CI). Split physical cores 50/50.
    total = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 8
    half = max(1, total // 2)
    return half, total - half


# Apple Silicon clusters — M5 Max (6+12=18), M4 Max (4+10=14), M1 Pro (2+6/2+8), etc.
E_CORES, P_CORES = _detect_clusters()

POLPO_PROCS = {
    'ollama': '🧠', 'claude': '🐙', 'python3': '🐍', 'python': '🐍',
    'node': '📦', 'n8n': '⚡', 'redis': '🔴', 'postgres': '🐘',
    'comfyui': '🎨', 'warp': '🚀', 'ngrok': '🌐', 'railway': '🚂',
}


def unified_memory() -> dict:
    out = subprocess.check_output(['vm_stat']).decode()
    pages: dict[str, int] = {}
    for line in out.splitlines():
        m = re.match(r'(.+?):\s+([\d]+)', line.strip())
        if m:
            pages[m.group(1).strip()] = int(m.group(2)) * PAGE_SIZE

    total = psutil.virtual_memory().total
    if total == 0:
        total = 1
    wired      = pages.get('Pages wired down', 0)
    active     = pages.get('Pages active', 0)
    inactive   = pages.get('Pages inactive', 0)
    compressed = pages.get('Pages stored in compressor', 0)
    free       = pages.get('Pages free', 0) + pages.get('Pages speculative', 0)
    swap_info  = psutil.swap_memory()
    swap       = swap_info.used
    swap_pct   = swap / swap_info.total if swap_info.total > 0 else 0.0

    used = total - free
    pct  = used / total * 100

    if free / total < 0.05 or swap_pct > 0.90:
        pressure = ('CRITICAL', 'error')
    elif free / total < 0.15 or swap_pct > 0.70:
        pressure = ('HIGH', 'warning')
    elif free / total < 0.35 or swap_pct > 0.40:
        pressure = ('MODERATE', 'info')
    else:
        pressure = ('NORMAL', 'ok')

    return {
        'total': total, 'used': used, 'free': free,
        'wired': wired, 'active': active, 'inactive': inactive,
        'compressed': compressed, 'swap': swap,
        'pct': pct, 'pressure': pressure,
    }


def battery() -> dict:
    try:
        out = subprocess.check_output(['pmset', '-g', 'ps']).decode()
        charging = 'AC Power' in out or 'charged' in out
        m = re.search(r'(\d+)%', out)
        return {'pct': int(m.group(1)) if m else 100, 'charging': charging}
    except Exception:
        return {'pct': 100, 'charging': True}


async def cpu_per_core() -> list[float]:
    """Blocking 0.5 s sample — run in thread to avoid blocking event loop."""
    return await asyncio.to_thread(psutil.cpu_percent, percpu=True, interval=0.5)


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
                results.append({
                    'pid': pid, 'name': name, 'label': label,
                    'mem_mb': mem_mb, 'cpu': cpu,
                    'bucket': BUCKET_CAUTIOUS,
                    'reason': f'LaunchAgent — meglio: launchctl stop {la_id}',
                    'kill_cmd': f'launchctl stop {la_id}',
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
    r'|SEZIONE GENERATA|^>\s*Aggiornato|context_detector',
    re.IGNORECASE,
)


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


def _make_title(msg: str) -> str:
    """Extract short human-readable title — avoid splitting on timestamps."""
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
    if not ts:
        return fallback
    try:
        h, m, s = ts.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
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

        for i, line in enumerate(reversed(lines)):
            line = line.rstrip()
            if not line or len(line) < 4 or _SKIP_LINES.search(line):
                continue
            ts = _extract_ts(line)
            msg = _clean_msg(line)
            if not msg or len(msg) < 3:
                continue
            title = _make_title(msg)
            desc = msg[len(title):].lstrip(':— |-').strip()[:72] if msg != title else ''
            # Use file mtime for sort fallback, offset by line position so ordering is stable
            sort_key = _ts_float(ts, file_ts) - i * 0.001
            entries.append({
                'ts':        ts or '—',
                'time_sort': sort_key,
                'emoji':     emoji,
                'title':     title,
                'source':    label,
                'desc':      desc,
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

    _log_feed_cache = deduped[:150]
    _log_feed_ts = now
    return _log_feed_cache
