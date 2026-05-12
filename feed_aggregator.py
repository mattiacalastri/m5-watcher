"""feed_aggregator — Unifica 4 stream events nel log-table del tab Feed (m5-watcher).

Forgiato sess.1607 per trasformare 4 sorgenti eterogenee (tentacoli, UNIFEED,
telemetry, sentinel) in entries normalizzate compatibili con _render_logs esistente.

Public API:
    aggregate_feed_events(app) -> list[dict]
        Ritorna list di entries da APPENDERE (non sostituire) a app._log_entries.
        Ogni entry: {ts, severity, emoji, source, title, desc, is_new}

Schema entry (allinea a app.py::_render_logs riga 2665+):
    {
        'ts':       'HH:MM:SS',
        'severity': 'P0' | 'P1' | 'info',
        'emoji':    '🐙' | '⚡' | '🔬' | '🛡',
        'source':   'Tentacoli' | 'UNIFEED' | 'Telemetry' | 'Sentinel',
        'title':    'short title <=36ch',
        'desc':     'detail <=60ch',
        'is_new':   bool,   # True se evento entro 60s da time.time()
    }

DEFENSIVE: ogni sorgente è racchiusa in try/except. Un'eccezione produce 1 entry
P1 'unavailable' e prosegue con le altre sorgenti. Mai solleva.
"""

from __future__ import annotations

import json
import pathlib
import re
import time
from datetime import datetime
from typing import Any


# ── Limits per sorgente (rispetta brief sess.1607) ───────────────────────────
_LIMIT_TENTACOLI   = 8
_LIMIT_UNIFEED     = 5
_LIMIT_TELEMETRY   = 4
_LIMIT_SENTINEL    = 5
_LIMIT_GOVERNANCE  = 5  # sess.1762 — governance daemon signals
_LIMIT_TGBOTS      = 5  # sess.1762 — polpo-tg-watcher recent[]
_LIMIT_VOICEAGENTS = 5  # sess.1762 — voice agents events (DLQ + killswitch + live)
_LIMIT_RADAR       = 5  # sess.1777 — governance signals top-N nel feed

# Governance daemon — severity mapping
_GOV_SEV_P0 = {"critical", "high"}
_GOV_SEV_P1 = {"medium"}

# TG bots — severity mapping (polpo-tg-watcher schema)
_TGBOT_SEV_P0 = {"high", "critical"}
_TGBOT_SEV_P1 = {"medium"}

# File paths — ground truth jsonl/state
_GOV_PATH        = pathlib.Path.home() / ".local/run/governance_signals.jsonl"
_TGBOT_STATE     = pathlib.Path.home() / ".local/run/polpo-tg-watcher/state.json"

# Soglie telemetry
_SLOW_P95_THRESHOLD_MS  = 500.0
_DRIFT_P95_THRESHOLD_MS = 200.0
_FRAME_P95_FLASH_MS     = 200.0  # frame_ms p95 alto = flash latency

# Tentacoli — status flagged → sev mapping
_TENT_SEV_P0 = {"error", "dead", "drift_critical", "crashed", "fail"}
_TENT_SEV_P1 = {"warn", "warning", "stale", "drift", "slow"}
_TENT_SKIP_OK = {"ok", "running", "healthy", "live", "up"}

# Sentinel alert severity / threat_level → sev mapping
_SENTINEL_SEV_P0 = {"critical", "breach", "p0"}
_SENTINEL_SEV_P1 = {"warn", "warning", "p1", "high"}

# Regex parse UNIFEED Rich-markup: estrae ts e content visibile
# Esempio input: "[dim]14:03:21[/] 🟠 [orange]swap activated[/] [dim]+1.2GB[/]"
_RX_TS = re.compile(r"\[\w+\]\s*(\d{2}:\d{2}:\d{2})\s*\[/\]")
_RX_RICH_TAGS = re.compile(r"\[/?[^\]]*\]")


def _now_hms() -> str:
    return time.strftime("%H:%M:%S")


def _is_recent(hms: str, window_s: int = 60) -> bool:
    """True se hms (HH:MM:SS) è entro window_s secondi da ora.

    Defensive: parse fail → False.
    """
    try:
        now_dt   = datetime.now()
        ev_parts = hms.split(":")
        if len(ev_parts) != 3:
            return False
        h, m, s = (int(x) for x in ev_parts)
        ev_dt = now_dt.replace(hour=h, minute=m, second=s, microsecond=0)
        delta = (now_dt - ev_dt).total_seconds()
        # Cross-midnight tolerance: se delta negativo grande, non è "appena ora"
        return 0 <= delta <= window_s
    except Exception:
        return False


def _trunc(s: str, n: int) -> str:
    """Taglio sicuro su None/non-str."""
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def _strip_rich(s: str) -> str:
    """Rimuove tag Rich `[...]` per produrre testo plain."""
    if not s:
        return ""
    return _RX_RICH_TAGS.sub("", s).strip()


def fmt_uptime(sec: float | int | None) -> str:
    """Formatta uptime_sec in stringa compatta: '5s' / '3m' / '2h' / '23h' / '5d'."""
    try:
        s = float(sec or 0)
    except Exception:
        return ""
    if s <= 0:
        return ""
    if s < 60:
        return f"{int(s)}s"
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}h"
    return f"{int(s // 86400)}d"


def _existing_heuristic_severity(entry: dict) -> str | None:
    """Heuristica legacy CPU/mem-based per derivare severity quando enrich non c'è.

    Ritorna 'P0' / 'P1' / None (skip — rumore di fondo).
    """
    try:
        cpu     = float(entry.get("cpu", 0) or 0)
        mem_mb  = float(entry.get("mem_mb", 0) or 0)
        status  = str(entry.get("status", "")).lower().strip()
        if status in _TENT_SEV_P0:
            return "P0"
        if status in _TENT_SEV_P1:
            return "P1"
        if status in _TENT_SKIP_OK and cpu < 50 and mem_mb < 2000:
            return None  # consolidato OK
        if cpu >= 90 or mem_mb >= 4000:
            return "P0"
        if cpu >= 50 or mem_mb >= 2000:
            return "P1"
        return None
    except Exception:
        return None


def _err_entry(source: str, emoji: str, error: BaseException) -> dict:
    """Singola entry P1 quando una sorgente solleva."""
    return {
        "ts":       _now_hms(),
        "severity": "P1",
        "emoji":    emoji,
        "source":   source,
        "title":    f"{source} unavailable",
        "desc":     _trunc(repr(error), 60),
        "is_new":   True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 🐙 TENTACOLI
# ─────────────────────────────────────────────────────────────────────────────
def _aggregate_tentacoli(app: Any) -> list[dict]:
    """Pull da data_sources.tentacoli() arricchito da feed_tentacoli_enrich.

    Pipeline:
      1. raw = ds.tentacoli() → list[dict] {pid, emoji, name, cpu, mem_mb, cmd}
      2. raw = enrich_tentacoli(raw) → +{uptime_sec, status, severity_hint,
         last_log_line, last_event_ts, log_path}  (graceful degradation)
      3. severity = entry['severity_hint'] (se presente) o heuristic legacy
      4. desc =  "up {fmt_uptime} · {last_log_line[:40]}"  (se enrich presente)

    Defensive: se tentacoli() o enrich falliscono → 1 entry P1 unavailable.
    """
    out: list[dict] = []
    try:
        import data_sources as ds  # local import — defensive vs path issues
        raw = ds.tentacoli() or []
    except Exception as e:
        return [_err_entry("Tentacoli", "🐙", e)]

    # Enrich tentacoli — graceful degradation se modulo o probe falliscono
    try:
        from feed_tentacoli_enrich import enrich_tentacoli
        raw = enrich_tentacoli(raw)
    except Exception:
        pass  # raw resta intatto, scendiamo su heuristic legacy

    ts = _now_hms()
    flagged: list[tuple[str, dict]] = []
    for t in raw:
        try:
            if not isinstance(t, dict):
                continue
            cpu     = float(t.get("cpu", 0) or 0)
            mem_mb  = float(t.get("mem_mb", 0) or 0)
            name    = t.get("name", "?")
            status  = str(t.get("status", "")).lower().strip()

            # Severity: hint dall'enricher vince; altrimenti heuristic legacy
            sev_hint = t.get("severity_hint")
            if sev_hint in {"P0", "P1", "info"}:
                # 'info' sull'hint = running healthy → skip (rumore di fondo)
                if sev_hint == "info":
                    continue
                severity = sev_hint
            else:
                heur = _existing_heuristic_severity(t)
                if heur is None:
                    continue
                severity = heur

            # Title: status/uptime summary
            uptime_sec = t.get("uptime_sec")
            if status:
                title = f"{name} {status}"[:36]
            elif uptime_sec:
                title = f"{name} up {fmt_uptime(uptime_sec)}"[:36]
            else:
                title = f"{name} {cpu:.0f}% cpu"[:36]

            # Desc: priorità a uptime + last_log_line (enrich) → fallback legacy
            cmd_short = _trunc(t.get("cmd", ""), 30)
            default_desc = f"pid {t.get('pid','?')} · {mem_mb:.0f}MB · {cmd_short}"[:60]
            desc_parts: list[str] = []
            up_str = fmt_uptime(uptime_sec) if uptime_sec else ""
            if up_str:
                desc_parts.append(f"up {up_str}")
            last_line = t.get("last_log_line")
            if last_line:
                desc_parts.append(str(last_line)[:40])
            desc = " · ".join(desc_parts) if desc_parts else default_desc
            desc = desc[:60]

            flagged.append((severity, {
                "ts":       ts,
                "severity": severity,
                "emoji":    t.get("emoji", "🐙"),
                "source":   "Tentacoli",
                "title":    title,
                "desc":     desc,
                "is_new":   True,
            }))
        except Exception:
            continue  # tentacolo malformato → skip silenzioso

    # Ordina per severity (P0 prima) e prendi top N
    sev_rank = {"P0": 0, "P1": 1, "info": 2}
    flagged.sort(key=lambda x: sev_rank.get(x[0], 9))
    for _, entry in flagged[:_LIMIT_TENTACOLI]:
        out.append(entry)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# ⚡ UNIFEED
# ─────────────────────────────────────────────────────────────────────────────
def _aggregate_unifeed(app: Any) -> list[dict]:
    """Parse app._event_feed (deque[str] Rich-markup) → entries normalizzate."""
    out: list[dict] = []
    try:
        feed = getattr(app, "_event_feed", None)
        if feed is None:
            return out
        raw_items = list(feed)[:_LIMIT_UNIFEED]
    except Exception as e:
        return [_err_entry("UNIFEED", "⚡", e)]

    for raw in raw_items:
        try:
            if not isinstance(raw, str):
                continue
            # Estrai ts
            m = _RX_TS.search(raw)
            ts = m.group(1) if m else _now_hms()
            content = _strip_rich(raw)
            # Rimuove ts dal content (già catturato)
            if m:
                content = content.replace(m.group(1), "", 1).strip()

            # Severity
            content_lc = content.lower()
            if "swap activated" in content_lc and "gb" in content_lc:
                # estrarre size se possibile
                sm = re.search(r"\+(\d+(?:\.\d+)?)\s*gb", content_lc)
                size_gb = float(sm.group(1)) if sm else 0.0
                severity = "P0" if size_gb > 1.0 else "P1"
            elif "critical" in content_lc:
                severity = "P1"
            elif "spike" in content_lc or "pressure" in content_lc:
                severity = "P1"
            else:
                severity = "info"

            # Title: prima riga semantica (max 36)
            title = _trunc(content, 36)
            # Desc: contesto numerico se c'è (resto della stringa)
            desc = _trunc(content, 60)

            out.append({
                "ts":       ts,
                "severity": severity,
                "emoji":    "⚡",
                "source":   "UNIFEED",
                "title":    title,
                "desc":     desc,
                "is_new":   _is_recent(ts),
            })
        except Exception:
            continue
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 🔬 TELEMETRY
# ─────────────────────────────────────────────────────────────────────────────
def _aggregate_telemetry(app: Any) -> list[dict]:
    """Pull eventi da app._metrics (struct Metrics in metrics.py).

    Eventi derivati:
      - flash: flash_count > 0 → entry P1 con breakdown reasons
      - slow:  slow_ms p95 > 500ms → entry P1
      - drift: tick_drift_ms p95 > 200ms → entry info
      - idle:  (idle_enters - idle_exits) > 0 → entry info "currently idle"
    """
    out: list[dict] = []
    try:
        m = getattr(app, "_metrics", None)
        if m is None:
            return out
        # Defensive: usa summary() se disponibile, fallback a attributi diretti
        try:
            s = m.summary()
        except Exception:
            s = None
    except Exception as e:
        return [_err_entry("Telemetry", "🔬", e)]

    ts = _now_hms()
    try:
        # Flash events
        flash_cnt = (s or {}).get("flash", {}).get("count", 0) if s else getattr(m, "flash_count", 0)
        if flash_cnt and flash_cnt > 0:
            reasons = (s or {}).get("flash", {}).get("reasons", {}) if s else getattr(m, "flash_reasons", {})
            top_reason = next(iter(reasons.keys()), "unspecified") if reasons else "unspecified"
            out.append({
                "ts":       ts,
                "severity": "P1",
                "emoji":    "🔬",
                "source":   "Telemetry",
                "title":    _trunc(f"flash {top_reason} × {flash_cnt}", 36),
                "desc":     _trunc(f"reasons: {dict(reasons)}", 60) if reasons else "critical flash trigger",
                "is_new":   True,
            })

        # Slow tick p95
        slow_p95 = (s or {}).get("slow_ms", {}).get("p95", 0.0) if s else 0.0
        if slow_p95 > _SLOW_P95_THRESHOLD_MS:
            out.append({
                "ts":       ts,
                "severity": "P1",
                "emoji":    "🔬",
                "source":   "Telemetry",
                "title":    _trunc(f"slow tick {slow_p95:.0f}ms p95", 36),
                "desc":     _trunc(f"slow_ms p95 > {_SLOW_P95_THRESHOLD_MS:.0f}ms threshold", 60),
                "is_new":   True,
            })

        # Drift
        drift_p95 = (s or {}).get("tick_drift_ms", {}).get("p95", 0.0) if s else 0.0
        if abs(drift_p95) > _DRIFT_P95_THRESHOLD_MS:
            sign = "+" if drift_p95 >= 0 else "-"
            out.append({
                "ts":       ts,
                "severity": "info",
                "emoji":    "🔬",
                "source":   "Telemetry",
                "title":    _trunc(f"drift {sign}{abs(drift_p95):.0f}ms", 36),
                "desc":     _trunc(f"tick_drift_ms p95 vs target", 60),
                "is_new":   True,
            })

        # Idle active
        idle_active = (s or {}).get("idle", {}).get("active", 0) if s else 0
        if idle_active and idle_active > 0:
            out.append({
                "ts":       ts,
                "severity": "info",
                "emoji":    "🔬",
                "source":   "Telemetry",
                "title":    _trunc(f"idle active × {idle_active}", 36),
                "desc":     _trunc("rainbow motion frozen — system quiet", 60),
                "is_new":   True,
            })
    except Exception:
        # Non bloccare: ritorna ciò che già abbiamo
        pass

    return out[:_LIMIT_TELEMETRY]


# ─────────────────────────────────────────────────────────────────────────────
# 🛡 SENTINEL
# ─────────────────────────────────────────────────────────────────────────────
def _aggregate_sentinel(app: Any) -> list[dict]:
    """Pull da app._sentinel_data['alerts'] (list[dict] da security_audit.jsonl)."""
    out: list[dict] = []
    try:
        sd = getattr(app, "_sentinel_data", None) or {}
        alerts = sd.get("alerts", []) or []
    except Exception as e:
        return [_err_entry("Sentinel", "🛡", e)]

    ranked: list[tuple[int, dict]] = []
    for a in alerts:
        try:
            # severity da campo esplicito o threat_level (alto = P0)
            sev_str = str(a.get("severity", "")).lower().strip()
            tl      = a.get("threat_level", 0) or 0
            try:
                tl_int = int(tl)
            except Exception:
                tl_int = 0

            if sev_str in _SENTINEL_SEV_P0 or tl_int >= 8:
                severity, rank = "P0", 0
            elif sev_str in _SENTINEL_SEV_P1 or tl_int >= 5:
                severity, rank = "P1", 1
            else:
                severity, rank = "info", 2

            # ts (HH:MM:SS) — alert ts può essere ISO o già hms
            ts_raw = str(a.get("ts", ""))
            ts = ts_raw[11:19] if len(ts_raw) >= 19 else (ts_raw[:8] if ts_raw else _now_hms())
            if not re.match(r"^\d{2}:\d{2}:\d{2}$", ts):
                ts = _now_hms()

            title_raw = a.get("title") or a.get("alert_type") or a.get("name") or "alert"
            desc_raw  = a.get("detail") or a.get("desc") or repr(a)[:80]

            ranked.append((rank, {
                "ts":       ts,
                "severity": severity,
                "emoji":    "🛡",
                "source":   "Sentinel",
                "title":    _trunc(str(title_raw), 36),
                "desc":     _trunc(str(desc_raw), 60),
                "is_new":   _is_recent(ts),
            }))
        except Exception:
            continue

    ranked.sort(key=lambda x: x[0])
    for _, entry in ranked[:_LIMIT_SENTINEL]:
        out.append(entry)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Governance daemon (sess.1762) — ~/.local/run/governance_signals.jsonl
# ─────────────────────────────────────────────────────────────────────────────
def _aggregate_governance(app: Any) -> list[dict]:
    """Tail jsonl governance daemon, rank per severity + value_eur."""
    out: list[dict] = []
    try:
        if not _GOV_PATH.exists():
            return []
        with _GOV_PATH.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 16384)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        return [_err_entry("Governance", "🛡️", e)]

    ranked: list[tuple[int, dict]] = []
    for line in tail[-30:]:
        line = line.strip()
        if not line:
            continue
        try:
            a = json.loads(line)
            sev_str = str(a.get("severity", "")).lower().strip()
            if sev_str in _GOV_SEV_P0:
                severity, rank = "P0", 0
            elif sev_str in _GOV_SEV_P1:
                severity, rank = "P1", 1
            else:
                severity, rank = "info", 2

            ts_raw = str(a.get("ts", ""))
            ts = ts_raw[11:19] if len(ts_raw) >= 19 else _now_hms()
            if not re.match(r"^\d{2}:\d{2}:\d{2}$", ts):
                ts = _now_hms()

            signal_name = str(a.get("signal", "signal"))
            value_eur   = a.get("value_eur") or 0
            urgency     = a.get("urgency_days") or 0
            try:
                value_int = int(value_eur)
            except Exception:
                value_int = 0
            try:
                urg_int = int(urgency)
            except Exception:
                urg_int = 0

            desc = f"€{value_int} · u={urg_int}d"
            ranked.append((rank, {
                "ts":       ts,
                "severity": severity,
                "emoji":    "🛡️",
                "source":   "Governance",
                "title":    _trunc(signal_name, 36),
                "desc":     _trunc(desc, 60),
                "is_new":   _is_recent(ts),
            }))
        except Exception:
            continue

    ranked.sort(key=lambda x: x[0])
    for _, e in ranked[:_LIMIT_GOVERNANCE]:
        out.append(e)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# TG Bots (sess.1762) — ~/.local/run/polpo-tg-watcher/state.json::recent[]
# ─────────────────────────────────────────────────────────────────────────────
_RX_HTML_TAGS = re.compile(r"<[^>]+>")

def _aggregate_tgbots(app: Any) -> list[dict]:
    """Read polpo-tg-watcher state.json recent[] — bot/severity/preview."""
    out: list[dict] = []
    try:
        if not _TGBOT_STATE.exists():
            return []
        data = json.loads(_TGBOT_STATE.read_text(encoding="utf-8"))
    except Exception as e:
        return [_err_entry("Bots", "📡", e)]

    recent = data.get("recent", []) or []
    ranked: list[tuple[int, dict]] = []
    for r in recent[:30]:
        try:
            sev_str = str(r.get("severity", "")).lower().strip()
            if sev_str in _TGBOT_SEV_P0:
                severity, rank = "P0", 0
            elif sev_str in _TGBOT_SEV_P1:
                severity, rank = "P1", 1
            else:
                severity, rank = "info", 2

            ts_raw = str(r.get("ts", ""))
            ts = ts_raw[11:19] if len(ts_raw) >= 19 else _now_hms()
            if not re.match(r"^\d{2}:\d{2}:\d{2}$", ts):
                ts = _now_hms()

            bot = str(r.get("bot", "?"))
            src = str(r.get("src", ""))
            preview_raw = str(r.get("preview", ""))
            preview_clean = _RX_HTML_TAGS.sub("", preview_raw).split("\n")[0].strip()

            ranked.append((rank, {
                "ts":       ts,
                "severity": severity,
                "emoji":    "📡",
                "source":   "Bots",
                "title":    _trunc(f"{bot}/{src}" if src else bot, 36),
                "desc":     _trunc(preview_clean, 60),
                "is_new":   _is_recent(ts),
            }))
        except Exception:
            continue

    ranked.sort(key=lambda x: x[0])
    for _, e in ranked[:_LIMIT_TGBOTS]:
        out.append(e)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Voice Agents (sess.1762) — pull da app._voiceagents_data
# Cicatrice madre sess.1758 (TUI counter disco cieco): leggiamo lo stesso dict
# che voice_agents_feed() ha già aggregato — quando il dict guadagnerà ground
# truth Twilio/EL API, questo aggregator beneficia gratis.
# ─────────────────────────────────────────────────────────────────────────────
def _aggregate_voiceagents(app: Any) -> list[dict]:
    """Voice agent events: killswitch + DLQ + budget + live call."""
    try:
        vd = getattr(app, "_voiceagents_data", None) or {}
    except Exception as e:
        return [_err_entry("Voice", "☎️", e)]
    if not isinstance(vd, dict) or not vd:
        return []

    out: list[dict] = []
    ranked: list[tuple[int, dict]] = []
    try:
        # Killswitch P0
        killed_by = str(vd.get("killed_by", "") or "").strip()
        killed_at = str(vd.get("killed_at", "") or "").strip()
        if killed_by:
            ts = killed_at[11:19] if len(killed_at) >= 19 else _now_hms()
            if not re.match(r"^\d{2}:\d{2}:\d{2}$", ts):
                ts = _now_hms()
            ranked.append((0, {
                "ts":       ts,
                "severity": "P0",
                "emoji":    "☎️",
                "source":   "Voice",
                "title":    _trunc(f"KILLED by {killed_by}", 36),
                "desc":     _trunc(f"setter disabled · {killed_at}", 60),
                "is_new":   _is_recent(ts),
            }))

        # DLQ P1
        dlq_count = vd.get("dlq_count", 0) or 0
        try:
            dlq_int = int(dlq_count)
        except Exception:
            dlq_int = 0
        if dlq_int > 0:
            sev = "P0" if dlq_int >= 5 else "P1"
            r   = 0 if dlq_int >= 5 else 1
            ranked.append((r, {
                "ts":       _now_hms(),
                "severity": sev,
                "emoji":    "☎️",
                "source":   "Voice",
                "title":    _trunc(f"DLQ {dlq_int} call", 36),
                "desc":     _trunc("dead letter queue non vuota", 60),
                "is_new":   True,
            }))

        # Budget alert
        budget_pct = vd.get("budget_pct", 0) or 0
        try:
            bp = float(budget_pct)
        except Exception:
            bp = 0.0
        if bp >= 75:
            sev, r = ("P0", 0) if bp >= 100 else ("P1", 1)
            ranked.append((r, {
                "ts":       _now_hms(),
                "severity": sev,
                "emoji":    "☎️",
                "source":   "Voice",
                "title":    _trunc(f"budget {bp:.0f}%", 36),
                "desc":     _trunc("monthly cap close to hard stop", 60),
                "is_new":   True,
            }))

        # Live call (info)
        lc = vd.get("live_call") or {}
        if isinstance(lc, dict) and lc.get("call_id"):
            cid = str(lc.get("call_id", "?"))[:18]
            status = str(lc.get("status", "?"))
            ranked.append((2, {
                "ts":       _now_hms(),
                "severity": "info",
                "emoji":    "☎️",
                "source":   "Voice",
                "title":    _trunc(f"LIVE {cid}", 36),
                "desc":     _trunc(f"status={status}", 60),
                "is_new":   True,
            }))

        # Errors collected by voice_agents_feed
        errors = vd.get("errors") or []
        if isinstance(errors, list):
            for err in errors[:2]:
                ranked.append((1, {
                    "ts":       _now_hms(),
                    "severity": "P1",
                    "emoji":    "☎️",
                    "source":   "Voice",
                    "title":    _trunc("feed err", 36),
                    "desc":     _trunc(str(err), 60),
                    "is_new":   True,
                }))
    except Exception as e:
        return [_err_entry("Voice", "☎️", e)]

    ranked.sort(key=lambda x: x[0])
    for _, e in ranked[:_LIMIT_VOICEAGENTS]:
        out.append(e)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Radar 360 (sess.1777) — governance_signals.jsonl top signals nel Feed
# ─────────────────────────────────────────────────────────────────────────────
def _aggregate_radar(app: Any) -> list[dict]:
    """Top governance signals (ultime 24h) come entries Feed normalizzate.

    Usa radar_widget._read_signals_24h() come sorgente canonica.
    Serve SOLO a iniettare segnali Radar nel tab Feed come cross-source.
    La visualizzazione ricca sta nel tab Radar dedicato (radar_widget.render_radar).

    Severity mapping: critical→P0, high→P0, medium→P1, low→info.
    """
    out: list[dict] = []
    try:
        from radar_widget import _read_signals_24h, _signal_category, _sev_color  # type: ignore
        signals = _read_signals_24h()
        if not signals:
            return []
    except Exception as e:
        return [_err_entry("Radar", "🔴", e)]

    _SEV_TO_FEED = {"critical": "P0", "high": "P0", "medium": "P1", "low": "info"}
    _SEV_RANK    = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    ranked: list[tuple[int, dict]] = []
    for s in signals:
        try:
            sev_str = str(s.get("severity", "low")).lower().strip()
            if sev_str not in _SEV_RANK:
                sev_str = "low"
            severity = _SEV_TO_FEED.get(sev_str, "info")
            rank     = _SEV_RANK.get(sev_str, 9)

            sig_name = str(s.get("signal", "?"))
            cat      = _signal_category(sig_name)
            val_eur  = int(s.get("value_eur") or 0)
            urg      = int(s.get("urgency_days") or 0)

            ts_raw = str(s.get("ts", ""))
            ts = ts_raw[11:19] if len(ts_raw) >= 19 else _now_hms()
            if not re.match(r"^\d{2}:\d{2}:\d{2}$", ts):
                ts = _now_hms()

            val_str = f"€{val_eur:,}".replace(",", ".") if val_eur else ""
            urg_str = f"u={urg}d" if urg else ""
            desc_parts = [p for p in [cat, val_str, urg_str] if p]

            ranked.append((rank, {
                "ts":       ts,
                "severity": severity,
                "emoji":    "🔴" if sev_str == "critical" else ("🟡" if sev_str in ("high", "medium") else "⚪"),
                "source":   "Radar",
                "title":    _trunc(sig_name, 36),
                "desc":     _trunc(" · ".join(desc_parts), 60),
                "is_new":   _is_recent(ts),
            }))
        except Exception:
            continue

    ranked.sort(key=lambda x: x[0])
    for _, e in ranked[:_LIMIT_RADAR]:
        out.append(e)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
def aggregate_feed_events(app: Any) -> list[dict]:
    """Aggrega 4 stream eterogenei in entries normalizzate per _render_logs.

    Args:
        app: M5Watcher instance (può essere None — defensive).

    Returns:
        list[dict] da APPENDERE a app._log_entries. Mai solleva.
        Lista vuota se app è None o tutte le sorgenti silenti.
    """
    if app is None:
        return []

    entries: list[dict] = []
    # Ogni sorgente è già try/except internamente — questo è doppio anello
    for fn, source, emoji in (
        (_aggregate_tentacoli,   "Tentacoli",  "🐙"),
        (_aggregate_unifeed,     "UNIFEED",    "⚡"),
        (_aggregate_telemetry,   "Telemetry",  "🔬"),
        (_aggregate_sentinel,    "Sentinel",   "🛡"),
        (_aggregate_governance,  "Governance", "🛡️"),  # sess.1762
        (_aggregate_tgbots,      "Bots",       "📡"),  # sess.1762
        (_aggregate_voiceagents, "Voice",      "☎️"),  # sess.1762
        (_aggregate_radar,       "Radar",      "🔴"),  # sess.1777
    ):
        try:
            entries.extend(fn(app))
        except Exception as e:
            entries.append(_err_entry(source, emoji, e))
    return entries
