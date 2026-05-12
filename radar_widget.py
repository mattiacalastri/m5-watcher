"""radar_widget.py — RADAR 360 Business Governance Intelligence Panel.

Forgiato sess.1777 (11 Mag 2026) come UI visivo del governance autonomo Polpo.
Estende feed_aggregator pattern canonical (sess.1607).

Public API:
    render_radar(signals: list[dict]) -> str
        Ritorna Rich markup string per Static widget.
        signals: lista di dict da governance_signals.jsonl (schema reale verificato):
            signal, severity, value_eur, urgency_days, context, suggested_skill,
            suggested_args, ts  [+ type, scope opzionali]

Design:
    - Heatmap per categoria (righe) x severity (colonne): critical/high/medium/low
    - Top-5 signals ranked per severity+value
    - Timeline sparkline 12h (bucket per ora, conta delta signals nelle ultime 12h)
    - Detector status bar (source availability check)

Color scheme Polpo (da polpo.tokens.json — mantenuto inline per no circular import):
    cyan    = #00bcd4   primary
    amber   = #ffb300   warn
    red     = #ff4444   critical
    dim     = #888888   low / chrome
    lime    = #a8ff60   ok / healthy
"""

from __future__ import annotations

import json
import pathlib
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

# ── Colori Polpo (inline — no circular import su polpo.tokens.json) ──────────
_CYAN    = "#00bcd4"   # primary — sweep header / neutral
_AMBER   = "#ffb300"   # warn — medium
_RED     = "#ff4444"   # critical
_LIME    = "#a8ff60"   # ok / healthy
_DIM     = "#888888"   # chrome / low
_WHITE   = "#ffffff"   # headline bold
_ORANGE  = "#ff8a3d"   # high
_TEAL    = "#009688"   # accento secondario

# ── Source path ───────────────────────────────────────────────────────────────
_GOV_PATH = pathlib.Path.home() / ".local" / "run" / "governance_signals.jsonl"

# ── Sparkline chars ───────────────────────────────────────────────────────────
_SPARK_CHARS = " ▁▂▃▄▅▆▇█"

# ── Severity ordering ─────────────────────────────────────────────────────────
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_SEV_COLS  = ["critical", "high", "medium", "low"]

# ── Category detection — mappa prefisso signal → categoria display ────────────
_CAT_MAP: list[tuple[str, str]] = [
    ("ANTHROPIC",    "Anthropic"),
    ("BTC",          "BTC Bot"),
    ("GHOST_CALL",   "GHL Ghost"),
    ("GHL",          "GHL"),
    ("WHATSAPP",     "WhatsApp"),
    ("WA_",          "WhatsApp"),
    ("EMAIL",        "Email"),
    ("FATHOM",       "Fathom"),
    ("STRIPE",       "Stripe"),
    ("DREAM",        "Dream"),
    ("RAILWAY",      "Railway"),
    ("DETECTOR",     "Detector"),
    ("SENTINEL",     "Sentinel"),
    ("PIPELINE",     "Pipeline"),
]

def _signal_category(signal_name: str) -> str:
    """Mappa signal_name → categoria display."""
    su = signal_name.upper()
    for prefix, cat in _CAT_MAP:
        if su.startswith(prefix):
            return cat
    # Fallback: prima parola _ -separated
    return signal_name.split("_")[0].title()[:12]


def _sev_color(sev: str) -> str:
    """Colore Rich per severity string."""
    return {
        "critical": _RED,
        "high":     _ORANGE,
        "medium":   _AMBER,
        "low":      _DIM,
    }.get(sev.lower(), _DIM)


def _sev_icon(sev: str) -> str:
    s = sev.lower()
    if s == "critical": return "[bold red]!"
    if s == "high":     return "[orange3]H"
    if s == "medium":   return "[yellow]~"
    return "·"


def _sparkline(values: list[int]) -> str:
    """Sparkline Unicode 0-8 step per lista int counts."""
    if not values:
        return _SPARK_CHARS[0] * 12
    mn = 0
    mx = max(values) or 1
    result = []
    for v in values:
        idx = int((v - mn) / (mx - mn) * 8) if mx > mn else 0
        result.append(_SPARK_CHARS[min(idx, 8)])
    return "".join(result)


def _read_signals_24h() -> list[dict]:
    """Legge governance_signals.jsonl — filtra ultime 24h.

    Defensive: ogni parse error → skip silenzioso.
    Ritorna lista ordinata per ts asc.
    """
    if not _GOV_PATH.exists():
        return []
    try:
        with _GOV_PATH.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 131072)  # leggi max 128KB da coda
            f.seek(size - chunk)
            raw = f.read().decode("utf-8", errors="replace").splitlines()
    except Exception:
        return []

    cutoff = datetime.now() - timedelta(hours=24)
    out: list[dict] = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            ts_raw = d.get("ts", "")
            # ISO ts: "2026-05-11T13:38:04"
            try:
                ts_dt = datetime.fromisoformat(ts_raw[:19])
            except Exception:
                ts_dt = datetime.now()
            if ts_dt >= cutoff:
                d["_ts_dt"] = ts_dt
                out.append(d)
        except Exception:
            continue

    out.sort(key=lambda x: x.get("_ts_dt", datetime.min))
    return out


def _build_heatmap(signals: list[dict]) -> dict[str, dict[str, int]]:
    """Costruisce {categoria: {severity: count}} da lista signals."""
    hm: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in signals:
        cat = _signal_category(s.get("signal", "?"))
        sev = s.get("severity", "low").lower()
        if sev not in _SEV_ORDER:
            sev = "low"
        hm[cat][sev] += 1
    return dict(hm)


def _build_timeline_12h(signals: list[dict]) -> list[int]:
    """Bucketing per ora delle ultime 12h → lista 12 int (count signal per ora).

    Bucket 0 = ora corrente - 11h, bucket 11 = ora corrente.
    """
    now = datetime.now()
    buckets = [0] * 12
    for s in signals:
        ts_dt = s.get("_ts_dt")
        if not isinstance(ts_dt, datetime):
            continue
        delta_h = (now - ts_dt).total_seconds() / 3600.0
        if 0 <= delta_h < 12:
            bucket_idx = 11 - int(delta_h)
            if 0 <= bucket_idx < 12:
                buckets[bucket_idx] += 1
    return buckets


def _total_at_risk(signals: list[dict]) -> int:
    """Somma value_eur segnali critical+high ultime 24h."""
    total = 0
    for s in signals:
        sev = s.get("severity", "").lower()
        if sev in ("critical", "high"):
            try:
                total += int(s.get("value_eur") or 0)
            except Exception:
                pass
    return total


def _top_signals(signals: list[dict], n: int = 5) -> list[dict]:
    """Top N signals per severity DESC + value_eur DESC."""
    def _rank(s: dict) -> tuple[int, int]:
        sev_r = _SEV_ORDER.get(s.get("severity", "low").lower(), 9)
        val   = int(s.get("value_eur") or 0)
        return (sev_r, -val)

    sorted_s = sorted(signals, key=_rank)
    return sorted_s[:n]


def _detector_status(signals: list[dict]) -> dict[str, str]:
    """Mappa detector → stato.

    Logica: se esiste signal con prefix corrispondente nelle ultime 12h → 'active'.
    Se governance_signals.jsonl esiste ma vuoto per quel detector → 'ok'.
    """
    detectors = {
        "anthropic": "Anthropic",
        "btc":       "BTC",
        "ghl":       "GHL Stale",
        "ghost":     "Ghost Call",
        "email":     "Email Draft",
        "sentinel":  "Sentinel",
    }
    active: set[str] = set()
    for s in signals:
        sig_up = s.get("signal", "").upper()
        for prefix in detectors:
            if sig_up.startswith(prefix.upper()):
                active.add(prefix)
    return {k: ("active" if k in active else "ok") for k in detectors}


def render_radar(signals: list[dict] | None = None) -> str:
    """Genera Rich markup per il tab Radar 360.

    Args:
        signals: se None, legge da governance_signals.jsonl. Permette injection
                 in test / override dati da app._radar_signals.
    """
    if signals is None:
        signals = _read_signals_24h()

    if not signals:
        return (
            f"[bold {_CYAN}]RADAR 360[/]  [{_DIM}]· governance intelligence · sweep {time.strftime('%H:%M:%S')}[/]\n\n"
            f"[{_DIM}]governance_signals.jsonl: nessun segnale nelle ultime 24h[/]\n"
            f"[{_DIM}]Path: {_GOV_PATH}[/]\n\n"
            f"[{_DIM}]Detector status: file {'trovato' if _GOV_PATH.exists() else 'NON trovato — governance daemon offline?'}[/]"
        )

    # ── Metriche header ───────────────────────────────────────────────────────
    n_active    = len(signals)
    at_risk_eur = _total_at_risk(signals)
    sweep_ts    = time.strftime("%H:%M:%S")
    n_critical  = sum(1 for s in signals if s.get("severity", "").lower() == "critical")
    n_high      = sum(1 for s in signals if s.get("severity", "").lower() == "high")

    # ── Heatmap ───────────────────────────────────────────────────────────────
    hm      = _build_heatmap(signals)
    cats    = sorted(hm.keys(), key=lambda c: min(_SEV_ORDER.get(s, 9) for s in hm[c]))
    cats    = cats[:8]  # max 8 righe

    # Larghezze colonne heatmap
    col_w = 8  # larghezza singola cella count
    label_w = max((len(c) for c in cats), default=8)
    label_w = max(label_w, 8)

    # Header heatmap
    hdr_cells = "".join(
        f"[{_DIM}]{s:>{col_w}}[/]" for s in ["CRIT", "HIGH", "MED", "LOW"]
    )
    heatmap_lines = [
        f"[bold {_CYAN}]{'CATEGORY':<{label_w}}[/]{hdr_cells}",
        f"[{_DIM}]{'─' * (label_w + col_w * 4)}[/]",
    ]
    for cat in cats:
        row_counts = hm[cat]
        cells = ""
        has_critical = row_counts.get("critical", 0) > 0
        has_high     = row_counts.get("high", 0) > 0
        for sev in _SEV_COLS:
            count = row_counts.get(sev, 0)
            col   = _sev_color(sev) if count > 0 else _DIM
            cell  = f"{count}" if count > 0 else "·"
            cells += f"[{col}]{cell:>{col_w}}[/]"
        label_col = _RED if has_critical else (_ORANGE if has_high else _DIM)
        heatmap_lines.append(
            f"[{label_col}]{cat:<{label_w}}[/]{cells}"
        )

    heatmap_block = "\n".join(heatmap_lines)

    # ── Top-5 signals ─────────────────────────────────────────────────────────
    top = _top_signals(signals, 5)
    top5_lines: list[str] = []
    for s in top:
        sev      = s.get("severity", "low").lower()
        sig_name = str(s.get("signal", "?"))[:36]
        val_eur  = int(s.get("value_eur") or 0)
        urg      = int(s.get("urgency_days") or 0)
        sev_col  = _sev_color(sev)

        # Estrarre contesto umano (context può essere dict o str)
        ctx = s.get("context", {})
        ctx_str = ""
        if isinstance(ctx, dict):
            # Cerca campi leggibili (priorita: name, subject, scope, type)
            for field in ("name", "subject", "scope", "type", "draft_id"):
                v = ctx.get(field)
                if v and isinstance(v, str):
                    ctx_str = str(v)[:28]
                    break
        elif isinstance(ctx, str):
            ctx_str = ctx[:28]

        val_str = f"€{val_eur:,}".replace(",", ".") if val_eur else "—"
        urg_str = f"u={urg}d" if urg else ""

        icon = {
            "critical": "[bold red]![/]",
            "high":     f"[{_ORANGE}]H[/]",
            "medium":   f"[{_AMBER}]~[/]",
            "low":      f"[{_DIM}]·[/]",
        }.get(sev, f"[{_DIM}]·[/]")

        meta_parts = [p for p in [ctx_str, val_str, urg_str] if p]
        meta = "  ·  ".join(meta_parts)

        top5_lines.append(
            f" {icon} [{sev_col}]{sig_name:<38}[/] [{_DIM}]{meta}[/]"
        )

    top5_block = "\n".join(top5_lines) if top5_lines else f"[{_DIM}]nessun segnale[/]"

    # ── Timeline sparkline 12h ─────────────────────────────────────────────────
    buckets  = _build_timeline_12h(signals)
    spark    = _sparkline(buckets)
    # Label ore: ora-11 → ora
    now_h   = datetime.now().hour
    h_start = (now_h - 11) % 24
    h_end   = now_h
    timeline_block = (
        f"[{_DIM}]{h_start:02d}:00[/] [{_CYAN}]{spark}[/] [{_DIM}]{h_end:02d}:59[/]  "
        f"[{_DIM}]· pressione 12h · {sum(buckets)} segnali[/]"
    )

    # ── Detector status ────────────────────────────────────────────────────────
    det_status = _detector_status(signals)
    det_parts: list[str] = []
    for key, label in [
        ("anthropic", "anthropic"),
        ("btc",       "btc_bot"),
        ("ghl",       "ghl_stale"),
        ("ghost",     "ghost_call"),
        ("email",     "email_draft"),
        ("sentinel",  "sentinel"),
    ]:
        st = det_status.get(key, "ok")
        if st == "active":
            det_parts.append(f"[{_AMBER}]~ {label}[/]")
        else:
            det_parts.append(f"[{_LIME}]ok {label}[/]")
    det_block = "  ".join(det_parts)

    # ── Composizione finale ────────────────────────────────────────────────────
    # Header
    at_risk_str = f"€{at_risk_eur:,}".replace(",", ".") if at_risk_eur else "€0"
    crit_flag   = f"  [{_RED}]{n_critical} critical[/]" if n_critical else ""
    high_flag   = f"  [{_ORANGE}]{n_high} high[/]"     if n_high     else ""
    header = (
        f"[bold {_CYAN}]RADAR 360[/]  "
        f"[{_DIM}]sweep {sweep_ts}[/]  "
        f"[bold {_WHITE}]{at_risk_str}@risk[/]"
        f"{crit_flag}{high_flag}  "
        f"[{_DIM}]{n_active} segnali 24h[/]"
    )

    divider = f"[{_DIM}]{'─' * 72}[/]"

    output = "\n".join([
        header,
        divider,
        f"[bold {_WHITE}]HEATMAP[/]",
        heatmap_block,
        "",
        divider,
        f"[bold {_WHITE}]TOP-5 SIGNALS[/]",
        top5_block,
        "",
        divider,
        f"[bold {_WHITE}]PRESSIONE 12h[/]",
        timeline_block,
        "",
        divider,
        f"[bold {_WHITE}]DETECTOR STATUS[/]",
        det_block,
        "",
        f"[{_DIM}]path: {_GOV_PATH}  ·  refresh ~5s[/]",
    ])
    return output


# ── Standalone smoke test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    from rich.console import Console
    from rich.text import Text as RichText
    c = Console()
    markup = render_radar()
    c.print(RichText.from_markup(markup))
    sigs = _read_signals_24h()
    print(f"\n[smoke] {len(sigs)} segnali letti da {_GOV_PATH}")
