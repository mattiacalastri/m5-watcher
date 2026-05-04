"""roadmap_common — costanti condivise dei 5 moduli roadmap (sess.1534).

Estratto in round 5 dopo FSE audit:
  - 27 hex color duplicati in 5 moduli → 5 costanti centralizzate
  - Vault path hardcoded in 4/5 moduli → env-aware con fallback unico
  - Today date helper (oggi è ground truth, non hardcoded test)

Import target:
    from roadmap_common import VAULT_BASE, RED, ORANGE, LIME, DIM, TEAL, today_iso
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

# ── Color palette (hex inline, allineata a app.py + Polpo Brand) ──────────────
RED      = "#ff3366"
ORANGE   = "#ff8a3d"
LIME     = "#3ddc97"
DIM      = "#8a98ad"
TEAL     = "#00d4aa"
ELEC_BLUE = "#3a7afe"
HOT_PINK  = "#ff6ec7"
DEEP_PURPL = "#9d4edd"
SOFT_GREEN = "#7dd87f"

PALETTE = {
    "RED": RED, "ORANGE": ORANGE, "LIME": LIME, "DIM": DIM, "TEAL": TEAL,
    "ELEC_BLUE": ELEC_BLUE, "HOT_PINK": HOT_PINK, "DEEP_PURPL": DEEP_PURPL,
    "SOFT_GREEN": SOFT_GREEN,
}


# ── Vault path (env-aware con fallback canonico) ──────────────────────────────
def _resolve_vault_base() -> Path:
    """Risolve la radice del vault Obsidian Astra Digital Marketing.

    Override via env var M5_VAULT_PATH (testabilità + portabilità M1/M5).
    Fallback al path canonico iCloud Obsidian di Mattia.
    """
    env_path = os.environ.get("M5_VAULT_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return (
        Path.home()
        / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Astra Digital Marketing"
    )


VAULT_BASE = _resolve_vault_base()


# ── Path canonici dei file roadmap-rilevanti ──────────────────────────────────
KPI_FILE        = VAULT_BASE / "KPI.md"
SESSION_CURRENT = VAULT_BASE / "session_current.md"
ROADMAP_Q2      = VAULT_BASE / "🧠 Memory" / "roadmap_q2_2026.md"
ROADMAP_CAL     = (
    VAULT_BASE
    / "5 — Vision"
    / "Roadmap Calendar 2026-2029 — Soul Engineer 3-6-12-24-36.md"
)
SESSIONI_DIR    = VAULT_BASE / "Sessioni"
CICATRICI_DIR   = VAULT_BASE / "Cicatrici"


# ── Date helper (ground truth, non hardcoded test) ───────────────────────────
def today_iso() -> str:
    """ISO date today — wrapper per allineamento test riproducibili.

    Override via env M5_TODAY_OVERRIDE (es. '2026-05-04') per snapshot test.
    """
    override = os.environ.get("M5_TODAY_OVERRIDE")
    if override:
        return override
    return date.today().isoformat()


def today_date() -> date:
    override = os.environ.get("M5_TODAY_OVERRIDE")
    if override:
        try:
            return date.fromisoformat(override)
        except ValueError:
            pass
    return date.today()
