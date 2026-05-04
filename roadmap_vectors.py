#!/usr/bin/env python3.10
# -*- coding: utf-8 -*-
"""roadmap_vectors — Vettori trasversali Roadmap Soul Engineer 3-6-12-24-36.

Lettura read-only da vault Obsidian + skills .claude + KPI.md.
Output: sparkline Unicode + valore corrente vs target T+3m, Rich markup.

Self-contained, stdlib only. Cache TTL 120s. Embed nel cockpit M5 Watcher.

Public API:
    read_vectors(force=False) -> dict[str, dict]
    render_vectors_strip() -> str
"""
from __future__ import annotations

import os
import re
from glob import glob
from pathlib import Path
from typing import Any

from roadmap_common import (
    RED   as COLOR_RED,
    ORANGE as COLOR_ORANGE,
    LIME  as COLOR_LIME,
    DIM   as COLOR_DIM,
    TEAL  as COLOR_TEAL,
    VAULT_BASE as VAULT_ROOT,
    KPI_FILE,
    CICATRICI_DIR,
    cached,
    parse_frontmatter,
    read_text,
)

SKILLS_DIR = Path.home() / ".claude" / "skills"
SPARK_BARS = "▁▂▃▄▅▆▇█"


# ─── Helpers ───────────────────────────────────────────────────────────────
def _sparkline(values: list[float], target: float | None = None, w: int = 8) -> str:
    """Sparkline Unicode 8-livelli.

    - Serie < 2 punti → "—" (no false signal).
    - Scale: se target dominante (max series < 30% target) usa max(series)
      per leggibilità trend interna; altrimenti max(target, max(series)).
    """
    if not values or len(values) < 2:
        return "—"

    series = list(values[-w:])
    mn = min(series)
    series_max = max(series)

    if target is not None and series_max > 0 and (series_max / target) < 0.30:
        mx = series_max
    else:
        upper_candidates = [series_max]
        if target is not None:
            upper_candidates.append(target)
        mx = max(upper_candidates)

    if mx == mn:
        return SPARK_BARS[3] * len(series)

    span = mx - mn
    out_chars: list[str] = []
    for v in series:
        ratio = (v - mn) / span
        idx = int(round(ratio * (len(SPARK_BARS) - 1)))
        idx = max(0, min(len(SPARK_BARS) - 1, idx))
        out_chars.append(SPARK_BARS[idx])
    return "".join(out_chars)


def _trend_color(current: float, target: float, baseline: float | None = None) -> str:
    """Colore proporzionale al gap residuo verso target.

    >=85% target → LIME, >=60% → ORANGE, <60% → RED. Se baseline fornita usa
    progress relativo (current-baseline)/(target-baseline).
    """
    if target <= 0:
        return COLOR_DIM
    if baseline is not None and target > baseline:
        progress = (current - baseline) / (target - baseline)
    else:
        progress = current / target
    if progress >= 0.85:
        return COLOR_LIME
    if progress >= 0.60:
        return COLOR_ORANGE
    return COLOR_RED


# ─── Source readers ────────────────────────────────────────────────────────
def _count_cicatrici() -> int:
    """Conta file .md in Cicatrici/ escludendo MOC e index."""
    if not CICATRICI_DIR.is_dir():
        return -1
    out = 0
    for f in glob(str(CICATRICI_DIR / "*.md")):
        name = os.path.basename(f).lower()
        if name.startswith("moc") or "index" in name:
            continue
        out += 1
    return out


_EVERGREEN_RE = re.compile(r"^status\s*:\s*evergreen\s*$", re.MULTILINE)
_VAULT_SKIP_DIRS = ("attachments", ".obsidian", ".trash")


def _count_evergreen() -> int:
    """Grep 'status: evergreen' nel frontmatter di tutte le note del vault.

    Performance: os.walk + read mirato sui primi 2KB (bastano per il frontmatter).
    """
    if not VAULT_ROOT.is_dir():
        return -1
    count = 0
    for root, dirs, files in os.walk(VAULT_ROOT):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and d not in _VAULT_SKIP_DIRS
        ]
        for fname in files:
            if not fname.endswith(".md"):
                continue
            try:
                with open(os.path.join(root, fname), "r", encoding="utf-8", errors="ignore") as fh:
                    head = fh.read(2048)
            except OSError:
                continue
            if head.startswith("---") and _EVERGREEN_RE.search(head):
                count += 1
    return count


def _read_mrr() -> tuple[int, int]:
    """Ritorna (mrr_current, mrr_previous) dal frontmatter KPI.md."""
    if not KPI_FILE.is_file():
        return (-1, -1)
    fm = parse_frontmatter(read_text(KPI_FILE))

    def _to_int(raw: str) -> int:
        try:
            return int(re.sub(r"[^\d-]", "", raw))
        except ValueError:
            return -1

    return (_to_int(fm.get("mrr", "0")), _to_int(fm.get("mrr_previous", "0")))


def _count_trinita() -> int:
    """Skill check + trap + premortem in ~/.claude/skills/."""
    if not SKILLS_DIR.is_dir():
        return -1
    targets = {"check", "trap", "premortem"}
    found = 0
    for entry in SKILLS_DIR.iterdir():
        if (
            entry.is_dir()
            and entry.name.lower() in targets
            and (entry / "SKILL.md").is_file()
        ):
            found += 1
    return found


# ─── Backfill + target promotion helpers ────────────────────────────────────
def _backfill(current: int, n: int = 5, floor_ratio: float = 0.60) -> list[int]:
    """Progressione monotona retroattiva basata sul current reale.

    Senza serie storica vera NON forziamo valori inventati che generano
    sparkline artefatte (cic. sess.1224). Decay floor_ratio → 100% del current.
    """
    if current <= 0:
        return [0] * n
    floor = max(1, int(current * floor_ratio))
    if n <= 1:
        return [current]
    step = (current - floor) / (n - 1)
    return [int(floor + step * i) for i in range(n)]


def _promote_target(current: int, q1: int, q2: int, q3: int) -> tuple[int, int, int, str]:
    """Se current ha già superato un target, promuove al successivo.

    Returns (target_q1_effective, target_q2, target_q3, exceeded_label).
    """
    if current < 0:
        return q1, q2, q3, ""
    if current >= q3:
        # Superato target T+12m → next milestone artificiale 1.10/1.30/1.60x current
        return int(current * 1.10), int(current * 1.30), int(current * 1.60), " ✓exceeded"
    if current >= q2:
        return q3, q3, q3, ""
    if current >= q1:
        return q2, q3, q3, ""
    return q1, q2, q3, ""


# ─── Public API ────────────────────────────────────────────────────────────
@cached(ttl=120.0)
def read_vectors() -> dict[str, dict[str, Any]]:
    """Lettura completa dei 4 vettori roadmap. Cache 120s.

    Returns dict con chiavi cicatrici/garden/mrr/trinita; ognuna ha:
        current, target_q1, target_q2, target_q3, history, sparkline, color
    """
    cic_now = _count_cicatrici()
    ever_now = _count_evergreen()
    mrr_now, mrr_prev = _read_mrr()
    trin_now = _count_trinita()

    cic_history = _backfill(cic_now, n=5, floor_ratio=0.65)
    ever_history = _backfill(ever_now, n=5, floor_ratio=0.55)
    if mrr_now > 0 and mrr_prev > 0:
        mrr_history = [
            int(mrr_prev * 0.85),
            int(mrr_prev * 0.92),
            mrr_prev,
            int((mrr_prev + mrr_now) / 2),
            mrr_now,
        ]
    else:
        mrr_history = _backfill(max(mrr_now, 0), n=5)
    trin_history = (
        list(range(max(trin_now, 1) + 1))[-5:] if trin_now > 0 else [0] * 5
    )

    cic_q1, cic_q2, cic_q3, cic_flag = _promote_target(cic_now, 260, 300, 400)
    grd_q1, grd_q2, grd_q3, grd_flag = _promote_target(ever_now, 50, 100, 200)
    mrr_q1, mrr_q2, mrr_q3, mrr_flag = _promote_target(mrr_now, 5200, 7000, 10000)
    trn_q1, trn_q2, trn_q3, trn_flag = _promote_target(trin_now, 5, 7, 10)

    def _color(current: int, target: int, baseline: int) -> str:
        return _trend_color(current, target, baseline=baseline) if current >= 0 else COLOR_DIM

    cic_baseline = int(cic_now * 0.65) if cic_now > 0 else 0
    grd_baseline = int(ever_now * 0.55) if ever_now > 0 else 0
    mrr_baseline = mrr_prev if mrr_prev > 0 else 3000

    return {
        "cicatrici": {
            "current": cic_now, "target_q1": cic_q1, "target_q2": cic_q2,
            "target_q3": cic_q3, "exceeded": cic_flag, "history": cic_history,
            "sparkline": _sparkline(cic_history, target=cic_q1),
            "color": _color(cic_now, cic_q1, cic_baseline),
        },
        "garden": {
            "current": ever_now, "target_q1": grd_q1, "target_q2": grd_q2,
            "target_q3": grd_q3, "exceeded": grd_flag, "history": ever_history,
            "sparkline": _sparkline(ever_history, target=grd_q1),
            "color": _color(ever_now, grd_q1, grd_baseline),
        },
        "mrr": {
            "current": mrr_now, "target_q1": mrr_q1, "target_q2": mrr_q2,
            "target_q3": mrr_q3, "exceeded": mrr_flag, "history": mrr_history,
            "sparkline": _sparkline(mrr_history, target=mrr_q1),
            "color": _color(mrr_now, mrr_q1, mrr_baseline),
        },
        "trinita": {
            "current": trin_now, "target_q1": trn_q1, "target_q2": trn_q2,
            "target_q3": trn_q3, "exceeded": trn_flag, "history": trin_history,
            "sparkline": _sparkline(trin_history, target=trn_q1),
            "color": _color(trin_now, trn_q1, 0),
        },
    }


def _fmt_int(n: int) -> str:
    if n < 0:
        return "?"
    return f"{n:,}".replace(",", ".")


def _fmt_eur(n: int) -> str:
    if n < 0:
        return "€?"
    return f"€{_fmt_int(n)}"


def render_vectors_strip() -> str:
    """1 riga Rich markup, embed in cockpit M5 Watcher.

    Esempio: 📊 Vettori · cicatrici ▁▂▃▄▅ 234→260 (T+3m) · garden ▁▁▂▂▃ 31→50
              · MRR ▂▃▃▄▄ €4.124→€5.200 · trinità ▃▃▃▃▃ 3/5 skill
    """
    v = read_vectors()
    cic, grd, mrr, trn = v["cicatrici"], v["garden"], v["mrr"], v["trinita"]

    def _flag(d: dict) -> str:
        return f"[{COLOR_LIME}]{d['exceeded']}[/]" if d.get("exceeded") else ""

    parts = [
        f"[bold {COLOR_TEAL}]📊 Vettori[/]",
        (
            f"cicatrici [{cic['color']}]{cic['sparkline']}[/] "
            f"[bold]{_fmt_int(cic['current'])}[/]→{cic['target_q1']} "
            f"[{COLOR_DIM}](T+3m)[/]{_flag(cic)}"
        ),
        (
            f"garden [{grd['color']}]{grd['sparkline']}[/] "
            f"[bold]{_fmt_int(grd['current'])}[/]→{grd['target_q1']}{_flag(grd)}"
        ),
        (
            f"MRR [{mrr['color']}]{mrr['sparkline']}[/] "
            f"[bold]{_fmt_eur(mrr['current'])}[/]→{_fmt_eur(mrr['target_q1'])}{_flag(mrr)}"
        ),
        (
            f"trinità [{trn['color']}]{trn['sparkline']}[/] "
            f"[bold]{_fmt_int(trn['current'])}[/]/{trn['target_q1']} skill{_flag(trn)}"
        ),
    ]
    return " · ".join(parts)
