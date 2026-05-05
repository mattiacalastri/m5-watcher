"""🩺 Health Widget — Apple Health vitals panel for M5 Max Watcher.

Reads `/tmp/polpo_health_live.json` (scritto da ~/scripts/health_sync.py
via LaunchAgent com.astra.apple-health-sync, refresh 5min) e renderizza
un pannello dark-theme coerente col design system Polpo:
  ⚖️ Peso (vs target + sparkline 14gg)
  🚶 Passi (vs target + sparkline 7gg)
  😴 Sonno totale + profondo (con sparkline 7gg)
  ❤️ FC riposo · HRV · SpO₂ (status color)
  🔥 Energia attiva
  🚨 Alert clinici (max 3 visibili)

Pattern speculare a kpi_widget.py — TTL-cached (30s), no import da app.py,
asyncio.to_thread safe.

Sess.1582 — fix Apple Health sync + dashboard live in m5-watcher.
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime
from pathlib import Path

from polpo_charts import (
    DIM, TEAL, WHITE,
    HOT_PINK, ELEC_BLUE, LIME, ORANGE, SOFT_GREEN,
    proportional_bar, empty_state,
)

_DEFAULT_SNAPSHOT_PATH = Path("/tmp/polpo_health_live.json")
_SNAPSHOT_PATH = Path(os.environ.get("M5W_HEALTH_SNAPSHOT", str(_DEFAULT_SNAPSHOT_PATH))).expanduser()

_cache:    dict | None = None
_cache_ts: float       = 0.0
_CACHE_TTL             = 30.0


def _safe_float(v, default: float | None = None) -> float | None:
    """Cast a float, NaN/inf-safe, None passthrough."""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def read_health_data(path: Path | None = None) -> dict:
    """Read health snapshot JSON. TTL-cached. Safe for asyncio.to_thread.

    Returns:
        dict (snapshot schema v1) o {} se file mancante / corrotto / staler.
    """
    global _cache, _cache_ts
    target = path if path is not None else _SNAPSHOT_PATH
    if path is None:
        now = time.monotonic()
        if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
            return _cache
    try:
        if not target.exists():
            data = {}
        else:
            data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if path is None:
        _cache, _cache_ts = data, time.monotonic()
    return data


# ── Status helpers ──────────────────────────────────────────────────────────
def _peso_color(peso: float | None, target: float, ref_lug25: float = 81.0) -> str:
    """Verde se peso ≤ target+1, lime se in-range progress, orange se >ref_lug25."""
    if peso is None:
        return DIM
    if peso <= target + 1.0:
        return LIME
    if peso < ref_lug25:
        return SOFT_GREEN  # progresso vs riferimento luglio 2025
    return ORANGE


def _passi_color(passi: int | None, target: int = 8000) -> str:
    if passi is None:
        return DIM
    if passi >= target:
        return LIME
    if passi >= target * 0.6:
        return ORANGE
    return HOT_PINK


def _sonno_color(ore: float | None) -> str:
    if ore is None:
        return DIM
    if ore >= 7.5:
        return LIME
    if ore >= 6.5:
        return SOFT_GREEN
    if ore >= 5.5:
        return ORANGE
    return HOT_PINK


def _hrv_color(hrv: float | None, baseline: float | None) -> str:
    if hrv is None:
        return DIM
    if baseline is None or baseline == 0:
        return ELEC_BLUE
    delta_pct = (hrv - baseline) / baseline * 100
    if delta_pct < -20:
        return HOT_PINK
    if delta_pct < -10:
        return ORANGE
    return LIME


def _rhr_color(rhr: float | None, baseline: float | None) -> str:
    if rhr is None:
        return DIM
    if baseline is None or baseline == 0:
        return ELEC_BLUE
    delta_pct = (rhr - baseline) / baseline * 100
    if delta_pct > 10:
        return HOT_PINK
    if delta_pct > 5:
        return ORANGE
    return LIME


def _spo2_color(spo2: float | None) -> str:
    if spo2 is None:
        return DIM
    if spo2 < 92:
        return HOT_PINK
    if spo2 < 95:
        return ORANGE
    return LIME


def _stale_marker(stale_min: int | None) -> tuple[str, str]:
    """Ritorna (emoji, color) in base a stale del snapshot."""
    if stale_min is None:
        return ("⏸", DIM)        # mai sincronizzato
    if stale_min < 60:
        return ("●", LIME)         # fresco <1h
    if stale_min < 60 * 24:
        return ("◐", ORANGE)       # vecchio <24h
    return ("○", HOT_PINK)         # stale >24h


def _fmt_giorni_fa(gg: int | None) -> str:
    if gg is None:
        return ""
    if gg == 0:
        return "oggi"
    if gg == 1:
        return "ieri"
    return f"{gg}gg fa"


def render_health(data: dict | None = None, w: int = 28) -> str:
    """Render Health vitals panel as Rich markup string.

    Args:
        data: snapshot dict (schema v1). Se None, legge automaticamente.
        w:    larghezza barre (default 28, coerente con kpi_widget).
    """
    if data is None:
        data = read_health_data()

    if not data:
        return empty_state(
            "🩺",
            "Snapshot Apple Health assente",
            "Atteso: /tmp/polpo_health_live.json (LaunchAgent com.astra.apple-health-sync)",
        )

    if data.get("n_records", 0) == 0:
        return empty_state(
            "🩺",
            "Health log vuoto — nessun dato Apple Health importato",
            "Attivare Shortcut iOS che droppa health_data.json in iCloud Drive/🏥 Salute Personale",
        )

    # ── Estrazione safe ─────────────────────────────────────────────────────
    peso_d   = data.get("peso", {})
    passi_d  = data.get("passi", {})
    sonno_d  = data.get("sonno", {})
    cardio_d = data.get("cardio", {})
    attiv_d  = data.get("attivita", {})
    workout_d = data.get("workout", {})
    alerts   = data.get("alerts", []) or []
    stale    = data.get("stale_min")
    n_rec    = data.get("n_records", 0)

    stale_emoji, stale_color = _stale_marker(stale)
    if stale is None:
        stale_label = "no data"
    elif stale < 60:
        stale_label = f"{stale}min fa"
    elif stale < 60 * 24:
        stale_label = f"{stale//60}h fa"
    else:
        stale_label = f"{stale//(60*24)}gg fa"

    # ── Header ──────────────────────────────────────────────────────────────
    lines: list[str] = [
        "",
        f"  [bold {WHITE}]🩺 HEALTH[/]  [{DIM}]· Apple Health · last sync [/][{stale_color}]{stale_emoji} {stale_label}[/]  [{DIM}]· {n_rec} rilevazioni[/]",
        "",
    ]

    # ── Peso ────────────────────────────────────────────────────────────────
    peso     = _safe_float(peso_d.get("current"))
    p_target = _safe_float(peso_d.get("target"), 72.0)
    delta_t  = _safe_float(peso_d.get("delta_target"))
    delta_l  = _safe_float(peso_d.get("delta_lug25"))
    trend14  = peso_d.get("trend_14d", {}) or {}
    spark_p  = trend14.get("spark", "")
    delta14  = _safe_float(trend14.get("delta"))
    gg_fa    = peso_d.get("giorni_fa")

    if peso is not None:
        # progress = quanto peso eccede vs target — invertito: meno = meglio
        # Bar: 100% = peso = lug25, 0% = peso = target. Più corto = meglio.
        ref_l25 = 81.0
        progress_pct = max(0.0, min(100.0, (peso - p_target) / max(ref_l25 - p_target, 0.1) * 100))
        peso_col = _peso_color(peso, p_target)
        bar = proportional_bar(progress_pct, 100.0, w, peso_col)
        peso_value = f"{peso:.1f} kg"
        if spark_p:
            peso_value += f"  [dim {peso_col}]{spark_p}[/]"
        lines.append(f"  [{peso_col}]⚖️[/]  [{DIM}]{'Peso':<14}[/] {bar}  [bold {WHITE}]{peso_value}[/]")
        sub_parts = []
        if delta_t is not None:
            sign = "+" if delta_t >= 0 else ""
            sub_parts.append(f"vs target [{LIME if delta_t<=0 else ORANGE}]{sign}{delta_t:.1f}kg[/]")
        if delta_l is not None:
            sign = "+" if delta_l >= 0 else ""
            sub_parts.append(f"vs lug25 [{LIME if delta_l<0 else ORANGE}]{sign}{delta_l:.1f}kg[/]")
        if delta14 is not None and abs(delta14) >= 0.2:
            arrow = "📉" if delta14 < 0 else "📈"
            sub_parts.append(f"14gg {arrow}{delta14:+.1f}kg")
        if gg_fa is not None:
            sub_parts.append(f"[{DIM}]{_fmt_giorni_fa(gg_fa)}[/]")
        if sub_parts:
            lines.append(f"  [{DIM}]{'':18}[/]" + " · ".join(sub_parts))
    else:
        lines.append(f"  [{DIM}]⚖️  Peso              — nessuna rilevazione recente[/]")
    lines.append("")

    # ── Passi ───────────────────────────────────────────────────────────────
    passi    = passi_d.get("today")
    p_target_passi = passi_d.get("target", 8000)
    qual     = passi_d.get("qualita") or ""
    spark_pa = (passi_d.get("trend_7d") or {}).get("spark", "")
    if passi is not None:
        passi_pct = min(100.0, passi / p_target_passi * 100)
        passi_col = _passi_color(passi, p_target_passi)
        bar = proportional_bar(passi_pct, 100.0, w, passi_col)
        passi_value = f"{int(passi):,}".replace(",", ".")
        if spark_pa:
            passi_value += f"  [dim {passi_col}]{spark_pa}[/]"
        lines.append(f"  [{passi_col}]🚶[/]  [{DIM}]{'Passi':<14}[/] {bar}  [bold {WHITE}]{passi_value}[/]")
        sub = f"target {p_target_passi:,}".replace(",", ".") + f" · {passi_pct:.0f}%"
        if qual:
            sub += f" · {qual}"
        lines.append(f"  [{DIM}]{'':18}{sub}[/]")
    else:
        lines.append(f"  [{DIM}]🚶  Passi             — n/d[/]")
    lines.append("")

    # ── Sonno ───────────────────────────────────────────────────────────────
    sonno_h  = _safe_float(sonno_d.get("totali"))
    deep_h   = _safe_float(sonno_d.get("profondo"))
    rem_h    = _safe_float(sonno_d.get("rem"))
    leg_h    = _safe_float(sonno_d.get("leggero"))
    spark_s  = (sonno_d.get("trend_7d") or {}).get("spark", "")
    if sonno_h is not None:
        sonno_col = _sonno_color(sonno_h)
        sonno_pct = min(100.0, sonno_h / 8.0 * 100)
        bar = proportional_bar(sonno_pct, 100.0, w, sonno_col)
        sonno_value = f"{sonno_h:.1f}h"
        if spark_s:
            sonno_value += f"  [dim {sonno_col}]{spark_s}[/]"
        lines.append(f"  [{sonno_col}]😴[/]  [{DIM}]{'Sonno':<14}[/] {bar}  [bold {WHITE}]{sonno_value}[/]")
        fasi = []
        if deep_h is not None:
            deep_col = HOT_PINK if deep_h < 1.0 else (ORANGE if deep_h < 1.5 else LIME)
            fasi.append(f"profondo [{deep_col}]{deep_h:.1f}h[/]")
        if rem_h is not None:
            fasi.append(f"REM {rem_h:.1f}h")
        if leg_h is not None:
            fasi.append(f"leggero {leg_h:.1f}h")
        if fasi:
            lines.append(f"  [{DIM}]{'':18}{' · '.join(fasi)}[/]")
    else:
        lines.append(f"  [{DIM}]😴  Sonno             — n/d[/]")
    lines.append("")

    # ── Cardio: FC riposo + HRV + SpO₂ in riga densa ────────────────────────
    rhr      = _safe_float(cardio_d.get("fc_riposo"))
    hrv      = _safe_float(cardio_d.get("hrv"))
    spo2     = _safe_float(cardio_d.get("spo2"))
    resp     = _safe_float(cardio_d.get("freq_resp"))
    rhr_avg  = _safe_float((cardio_d.get("rhr_trend_7d") or {}).get("avg"))
    hrv_avg  = _safe_float((cardio_d.get("hrv_trend_7d") or {}).get("avg"))

    cardio_segs = []
    if rhr is not None:
        c = _rhr_color(rhr, rhr_avg)
        cardio_segs.append(f"[{c}]❤️ {int(rhr)}bpm[/]")
    if hrv is not None:
        c = _hrv_color(hrv, hrv_avg)
        cardio_segs.append(f"[{c}]💫 HRV {int(hrv)}ms[/]")
    if spo2 is not None:
        c = _spo2_color(spo2)
        cardio_segs.append(f"[{c}]🩸 {spo2:.0f}%[/]")
    if resp is not None:
        c = LIME if 12 <= resp <= 20 else ORANGE
        cardio_segs.append(f"[{c}]🌬 {int(resp)}/min[/]")

    if cardio_segs:
        lines.append(f"  [{DIM}]{'Cardio':<14}[/]      " + "   ".join(cardio_segs))
        # Sub: baseline indicator se disponibile
        sub_parts = []
        if rhr is not None and rhr_avg:
            d = (rhr - rhr_avg) / rhr_avg * 100
            sub_parts.append(f"FC base {rhr_avg:.0f} ({d:+.0f}%)")
        if hrv is not None and hrv_avg:
            d = (hrv - hrv_avg) / hrv_avg * 100
            sub_parts.append(f"HRV base {hrv_avg:.0f} ({d:+.0f}%)")
        if sub_parts:
            lines.append(f"  [{DIM}]{'':18}{' · '.join(sub_parts)}[/]")
        lines.append("")

    # ── Energia attiva + workout ────────────────────────────────────────────
    energia  = _safe_float(attiv_d.get("energia_kcal"))
    minuti   = _safe_float(attiv_d.get("minuti_esercizio"))
    vo2      = _safe_float(attiv_d.get("vo2_max"))
    spark_e  = (attiv_d.get("energia_trend_7d") or {}).get("spark", "")

    if energia is not None or minuti is not None:
        ene_segs = []
        if energia is not None:
            ene_segs.append(f"[{ORANGE}]🔥 {int(energia)} kcal[/]")
            if spark_e:
                ene_segs[-1] += f" [dim {ORANGE}]{spark_e}[/]"
        if minuti is not None:
            mc = LIME if minuti >= 30 else (ORANGE if minuti >= 15 else HOT_PINK)
            ene_segs.append(f"[{mc}]🏋 {int(minuti)}min[/]")
        if vo2 is not None:
            ene_segs.append(f"[{ELEC_BLUE}]💨 VO₂ {vo2:.1f}[/]")
        lines.append(f"  [{DIM}]{'Attività':<14}[/]      " + "   ".join(ene_segs))
        lines.append("")

    # ── Workout (se presente) ───────────────────────────────────────────────
    w_tipo = workout_d.get("tipo")
    if w_tipo:
        bits = [f"[bold {ELEC_BLUE}]{w_tipo}[/]"]
        if workout_d.get("durata_min"):
            bits.append(f"{workout_d['durata_min']}min")
        if workout_d.get("kcal"):
            bits.append(f"{workout_d['kcal']}kcal")
        if workout_d.get("fc_max"):
            bits.append(f"max {workout_d['fc_max']}bpm")
        lines.append(f"  [{DIM}]{'Workout':<14}[/]      " + " · ".join(bits))
        if workout_d.get("note"):
            lines.append(f"  [{DIM}]{'':18}📝 {workout_d['note']}[/]")
        lines.append("")

    # ── Alert clinici (max 3 visibili) ──────────────────────────────────────
    if alerts:
        lines.append(f"  [{DIM}]── Alert clinici ──────────────────────────────[/]")
        for a in alerts[:3]:
            lines.append(f"  {a}")
        if len(alerts) > 3:
            lines.append(f"  [{DIM}]  …+{len(alerts)-3} altri alert[/]")

    return "\n".join(lines)


# ── Public API per TitleBar / status compact (futuro hook) ──────────────────
def health_for_titlebar(data: dict | None = None) -> dict:
    """Payload compatto per TitleBar Polpo Cockpit (pattern kpi_for_titlebar).

    Returns:
        dict con peso/passi/sonno/hrv + spark + alert_count.
        Empty dict {} se snapshot vuoto.
    """
    if data is None:
        data = read_health_data()
    if not data or data.get("n_records", 0) == 0:
        return {}
    peso_d   = data.get("peso", {})
    passi_d  = data.get("passi", {})
    sonno_d  = data.get("sonno", {})
    cardio_d = data.get("cardio", {})
    return {
        "peso":       _safe_float(peso_d.get("current")),
        "peso_spark": (peso_d.get("trend_14d") or {}).get("spark", ""),
        "passi":      passi_d.get("today"),
        "sonno":      _safe_float(sonno_d.get("totali")),
        "hrv":        _safe_float(cardio_d.get("hrv")),
        "rhr":        _safe_float(cardio_d.get("fc_riposo")),
        "alerts_n":   len(data.get("alerts", []) or []),
        "stale_min":  data.get("stale_min"),
    }
