"""M5 Max data sources — no sudo required."""
from __future__ import annotations

import asyncio
import re
import subprocess

import psutil

PAGE_SIZE = 16384  # M5 Max page size (bytes)
E_CORES = 6        # "Super" efficiency cores  (hw.perflevel0)
P_CORES = 12       # Performance cores          (hw.perflevel1)

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
    wired      = pages.get('Pages wired down', 0)
    active     = pages.get('Pages active', 0)
    inactive   = pages.get('Pages inactive', 0)
    compressed = pages.get('Pages stored in compressor', 0)
    free       = pages.get('Pages free', 0) + pages.get('Pages speculative', 0)
    swap       = psutil.swap_memory().used

    used = total - free
    pct  = used / total * 100

    if free / total < 0.05:
        pressure = ('CRITICAL', 'error')
    elif free / total < 0.15:
        pressure = ('HIGH', 'warning')
    elif free / total < 0.35:
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
_net_snap:  object = None
_net_time:  float  = 0.0


def disk_io_rate() -> dict[str, float]:
    """MB/s read + write since last call."""
    import time
    global _disk_snap, _disk_time
    now = time.monotonic()
    curr = psutil.disk_io_counters()
    if curr is None:
        return {'read': 0.0, 'write': 0.0}
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
    import time
    global _net_snap, _net_time
    now = time.monotonic()
    curr = psutil.net_io_counters(pernic=False)
    if curr is None:
        return {'sent': 0.0, 'recv': 0.0}
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
