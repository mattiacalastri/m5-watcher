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
            for kw, emoji in POLPO_PROCS.items():
                if kw in name_lc:
                    cmd = ' '.join(p.info.get('cmdline') or [])
                    found.append({
                        'pid': p.info['pid'], 'emoji': emoji,
                        'name': p.info['name'][:22],
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
