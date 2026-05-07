"""feed_tentacoli_enrich — arricchisce raw tentacoli list con campi semantici.

Defensive by design: ogni operazione filesystem/psutil in try/except.
Mai solleva. Se enrich fallisce sul singolo dict, restituisce raw_list intatta.

Forgiato sess.1607 per Feed Tab refactor — sostituisce euristiche CPU/mem-only
del feed_aggregator con segnali derivati da uptime, log mtime, cpu sustained.

Nessuna dipendenza esterna oltre psutil (già in data_sources.py).
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover — psutil è dep dura del cockpit
    psutil = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mappa nome→path log canonico per i tentacoli noti del Polpo Squad.
# Path resi assoluti via os.path.expanduser() in resolve.
# Aggiungere nuovi tentacoli qui quando emergono dal vault.
# ---------------------------------------------------------------------------
KNOWN_TENTACOLI_LOGS: dict[str, str] = {
    'screenshot_watcher':       '~/scripts/logs/screenshot_watcher.log',
    'email_reply_watcher':      '~/scripts/logs/email_reply_watcher.log',
    'git_debt_watcher':         '~/scripts/logs/git_debt_watcher.log',
    'polpo_tg_watcher_daemon':  '~/scripts/logs/polpo_tg_watcher_daemon.log',
    'm5-resources-daemon':      '~/scripts/logs/m5-resources-daemon.log',
    'health_bot':               '~/scripts/logs/health_bot.log',
    'setter_manager':           '~/scripts/logs/setter_manager.log',
    'predator':                 '~/scripts/logs/predator.log',
    'tg-bots-watcher':          '~/scripts/logs/tg-bots-watcher.log',
    'voice_briefing':           '~/scripts/logs/voice_briefing.log',
    'jarvis_toggle':            '~/scripts/logs/jarvis_toggle.log',
}

# Fallback search paths se KNOWN_TENTACOLI_LOGS non ha l'entry.
_LOG_FALLBACK_PATTERNS = (
    '~/scripts/logs/{name}.log',
    '~/.local/log/{name}.log',
    '/tmp/{name}.log',
)

# Soglie status — sintonia con dottrina cockpit (sess.1583 swap-blind fix).
_STARTING_THRESHOLD_SEC = 5.0
_SPIKE_CPU_PCT          = 150.0
_STUCK_UPTIME_SEC       = 3600.0


def _safe_uptime(pid: Any) -> float:
    """Calcola uptime_sec da psutil.Process(pid).create_time(). Fallback 0."""
    if psutil is None or not pid:
        return 0.0
    try:
        proc = psutil.Process(int(pid))
        return max(0.0, time.time() - proc.create_time())
    except Exception as e:  # NoSuchProcess, AccessDenied, ZombieProcess, ValueError
        logger.debug('uptime probe failed pid=%s: %s', pid, e)
        return 0.0


def _resolve_log_path(name: str) -> str | None:
    """Trova log file per il tentacolo. Prima KNOWN_TENTACOLI_LOGS poi fallback."""
    if not name:
        return None
    name_norm = name.strip().lower()
    # Match esatto o substring sui known
    for known_name, raw_path in KNOWN_TENTACOLI_LOGS.items():
        if known_name == name_norm or known_name in name_norm or name_norm in known_name:
            try:
                p = os.path.expanduser(raw_path)
                if os.path.isfile(p):
                    return p
            except Exception:
                continue
    # Fallback patterns
    for pattern in _LOG_FALLBACK_PATTERNS:
        try:
            candidate = os.path.expanduser(pattern.format(name=name_norm))
            if os.path.isfile(candidate):
                return candidate
        except Exception:
            continue
    return None


def _safe_tail_line(path: str, max_chars: int = 80) -> str | None:
    """Legge ultima riga del log, troncata a max_chars. Defensive."""
    try:
        # Lettura tail-style: leggi ultimi 4KB e split linee
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > 4096:
                f.seek(-4096, os.SEEK_END)
            chunk = f.read()
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            return None
        last = lines[-1].decode('utf-8', errors='replace').strip()
        return last[:max_chars]
    except Exception as e:
        logger.debug('tail read failed path=%s: %s', path, e)
        return None


def _safe_log_mtime(path: str) -> float | None:
    """mtime del log file in epoch sec. None se ko."""
    try:
        return os.path.getmtime(path)
    except Exception:
        return None


def _classify_status(uptime_sec: float, cpu: float, exit_status: Any) -> str:
    """Classifica status del tentacolo. Ordine: error > spike > starting > stuck > running."""
    if exit_status is not None:
        return 'error'
    if cpu > _SPIKE_CPU_PCT:
        return 'spike'
    if uptime_sec < _STARTING_THRESHOLD_SEC:
        return 'starting'
    # 'stuck' richiede memoria storica mem_mb crescente — guess su uptime puro.
    # In assenza di history qui, marchiamo stuck solo se uptime molto alto E cpu basso (proc dormiente lungo).
    if uptime_sec > _STUCK_UPTIME_SEC and cpu < 1.0:
        return 'stuck'
    return 'running'


def _severity_hint(status: str) -> str:
    """Mappa status → severity_hint per il feed aggregator."""
    if status in {'error', 'dead'}:
        return 'P0'
    if status in {'spike', 'stuck', 'drift'}:
        return 'P1'
    return 'info'


def enrich_tentacoli(raw_list: list[dict]) -> list[dict]:
    """Aggiunge status/uptime/severity_hint/last_log_line a ogni dict tentacolo.

    Defensive: se enrich fallisce sul singolo item, lo lascia intatto.
    Se la chiamata top-level esplode, ritorna raw_list intatta + warning log.

    Campi aggiunti per ogni dict:
      - uptime_sec        : float (0.0 se pid non valido)
      - status            : str   ('running'|'starting'|'spike'|'stuck'|'error')
      - severity_hint     : str   ('P0'|'P1'|'info')
      - last_log_line     : str|None (max 80 char)
      - last_event_ts     : float|None (epoch sec, da log mtime)
      - log_path          : str|None (path al log file usato)
    """
    if not raw_list:
        return raw_list

    try:
        enriched: list[dict] = []
        for item in raw_list:
            try:
                if not isinstance(item, dict):
                    enriched.append(item)
                    continue

                pid = item.get('pid')
                cpu = float(item.get('cpu') or 0.0)
                name = item.get('name') or ''
                exit_status = item.get('exit_status')

                uptime_sec = _safe_uptime(pid)
                status = _classify_status(uptime_sec, cpu, exit_status)
                severity = _severity_hint(status)

                log_path = _resolve_log_path(name)
                last_log_line = _safe_tail_line(log_path) if log_path else None
                last_event_ts = _safe_log_mtime(log_path) if log_path else None

                enriched_item = dict(item)  # shallow copy — non mutiamo l'originale
                enriched_item.update({
                    'uptime_sec':    uptime_sec,
                    'status':        status,
                    'severity_hint': severity,
                    'last_log_line': last_log_line,
                    'last_event_ts': last_event_ts,
                    'log_path':      log_path,
                })
                enriched.append(enriched_item)
            except Exception as e_item:
                logger.warning('enrich_tentacoli item failed name=%s: %s',
                               item.get('name') if isinstance(item, dict) else '?', e_item)
                enriched.append(item)
        return enriched
    except Exception as e_top:
        logger.warning('enrich_tentacoli top-level failed: %s — returning raw_list intact', e_top)
        return raw_list
