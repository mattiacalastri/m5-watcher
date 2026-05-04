"""cli_commands.py — M5 Watcher extensibility layer (sess.1508 round 4).

CLI subcommands + webhook hook. Zero import from app.py.
All data calls go through data_sources / vault_parser / kpi_widget directly.

Subcommands:
  snapshot    — one-shot JSON dump of all system vitals
  tail-feed   — follow ~/.m5-watcher/metrics.jsonl (tail -f style)
  export-kpi  — dump KPI.md data as CSV or JSON
  health      — one-shot health check with structured exit code

Webhook:
  webhook_post(url, payload) — fire-and-forget POST, async-safe

argparse:
  add_subparsers(parser) — registers all subcommands on the main parser
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import data_sources as ds
import kpi_widget
import vault_parser


# ── Snapshot ──────────────────────────────────────────────────────────────────

def cmd_snapshot(args: argparse.Namespace) -> int:
    """One-shot JSON snapshot of all system vitals."""
    try:
        cpu = asyncio.run(ds.cpu_per_core())
    except Exception as exc:
        cpu = []
        print(f"[snapshot] cpu_per_core failed: {exc}", file=sys.stderr)

    try:
        mem = ds.unified_memory()
        if isinstance(mem.get("pressure"), tuple):
            mem["pressure"] = {"label": mem["pressure"][0], "level": mem["pressure"][1]}
    except Exception as exc:
        mem = {"error": str(exc)}

    try:
        bat = ds.battery()
    except Exception as exc:
        bat = {"error": str(exc)}

    try:
        processes = ds.top_processes(16)
    except Exception as exc:
        processes = []
        print(f"[snapshot] top_processes failed: {exc}", file=sys.stderr)

    try:
        tents = ds.tentacoli()
    except Exception as exc:
        tents = []

    try:
        focus = ds.current_focus()
    except Exception as exc:
        focus = {"error": str(exc)}

    try:
        raw_logs = ds.log_feed()
        logs = [
            {k: v for k, v in e.items() if k != "time_sort"}
            for e in raw_logs[:20]
        ]
    except Exception as exc:
        logs = []

    try:
        kpi = kpi_widget.read_kpi_data()
    except Exception as exc:
        kpi = {"error": str(exc)}

    snapshot = {
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "chip":         getattr(ds, "CHIP_NAME", "unknown"),
        "total_ram_gb": getattr(ds, "TOTAL_RAM_GB", 0),
        "cpu_per_core": cpu,
        "memory":       mem,
        "battery":      bat,
        "processes":    processes,
        "tentacoli":    tents,
        "focus":        focus,
        "logs":         logs,
        "kpi":          kpi,
    }

    indent = 2 if getattr(args, "pretty", False) else None
    try:
        text = json.dumps(snapshot, indent=indent, ensure_ascii=False, default=str)
    except Exception as exc:
        print(f"[snapshot] json.dumps failed: {exc}", file=sys.stderr)
        return 1

    output_path = getattr(args, "output", None)
    if output_path:
        try:
            dest = Path(output_path).expanduser()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            print(f"snapshot written -> {dest}", file=sys.stderr)
        except OSError as exc:
            print(f"[snapshot] write failed: {exc}", file=sys.stderr)
            return 1
    else:
        print(text)
    return 0


# ── Tail Feed ─────────────────────────────────────────────────────────────────

_METRICS_FILE = Path.home() / ".m5-watcher" / "metrics.jsonl"


def cmd_tail_feed(args: argparse.Namespace) -> int:
    """Follow ~/.m5-watcher/metrics.jsonl in real time."""
    path = _METRICS_FILE
    if not path.exists():
        print(
            f"[tail-feed] {path} not found.\n"
            "Start m5-watcher TUI to begin writing metrics.",
            file=sys.stderr,
        )
        return 0
    print(f"[tail-feed] following {path}  (Ctrl+C to stop)", file=sys.stderr)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            fh.seek(0, 2)
            while True:
                line = fh.readline()
                if line:
                    s = line.rstrip("\n")
                    if s:
                        print(s, flush=True)
                else:
                    time.sleep(0.25)
    except KeyboardInterrupt:
        print("\n[tail-feed] stopped.", file=sys.stderr)
        return 0
    except OSError as exc:
        print(f"[tail-feed] read error: {exc}", file=sys.stderr)
        return 1


# ── Export KPI ────────────────────────────────────────────────────────────────

def cmd_export_kpi(args: argparse.Namespace) -> int:
    """Dump KPI.md frontmatter as CSV (default) or JSON."""
    fmt = getattr(args, "format", "csv") or "csv"
    try:
        kpi = kpi_widget.read_kpi_data()
    except Exception as exc:
        print(f"[export-kpi] read_kpi_data failed: {exc}", file=sys.stderr)
        return 1
    if not kpi:
        print("[export-kpi] KPI.md not found or empty.", file=sys.stderr)
        return 1
    if fmt == "json":
        print(json.dumps(kpi, indent=2, ensure_ascii=False, default=str))
        return 0
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["key", "value"])
    for k, v in sorted(kpi.items()):
        writer.writerow([k, v])
    print(buf.getvalue(), end="")
    return 0


# ── Health Check ──────────────────────────────────────────────────────────────

def cmd_health(args: argparse.Namespace) -> int:
    """Health check — JSON output, exit 0=OK / 1=WARN / 2=ERROR."""
    checks: list[dict] = []
    exit_code = 0

    try:
        kpi = kpi_widget.read_kpi_data()
        if kpi:
            checks.append({"name": "kpi_loaded", "status": "OK",
                           "detail": f"{len(kpi)} keys"})
        else:
            checks.append({"name": "kpi_loaded", "status": "WARN",
                           "detail": "KPI.md empty/missing"})
            exit_code = max(exit_code, 1)
    except Exception as exc:
        checks.append({"name": "kpi_loaded", "status": "ERROR", "detail": str(exc)})
        exit_code = max(exit_code, 2)

    vault_path = getattr(vault_parser, "VAULT_PATH", None)
    if vault_path and vault_path.exists() and vault_path.is_dir():
        try:
            n_md = sum(1 for _ in vault_path.glob("*.md"))
            checks.append({"name": "vault_accessible", "status": "OK",
                           "detail": f"{vault_path} ({n_md} .md root)"})
        except OSError as exc:
            checks.append({"name": "vault_accessible", "status": "WARN",
                           "detail": str(exc)})
            exit_code = max(exit_code, 1)
    else:
        checks.append({"name": "vault_accessible", "status": "WARN",
                       "detail": f"not found: {vault_path}"})
        exit_code = max(exit_code, 1)

    jarvis_dir = Path.home() / ".local" / "run" / "jarvis"
    if jarvis_dir.exists() and jarvis_dir.is_dir():
        checks.append({"name": "jarvis_dir", "status": "OK", "detail": str(jarvis_dir)})
    else:
        checks.append({"name": "jarvis_dir", "status": "WARN",
                       "detail": f"not found: {jarvis_dir}"})
        exit_code = max(exit_code, 1)

    sentinel_paths = [
        Path.home() / ".claude" / "canary_state.json",
        Path.home() / ".claude" / "security_audit.jsonl",
    ]
    sf = next((p for p in sentinel_paths if p.exists()), None)
    if sf:
        checks.append({"name": "sentinel", "status": "OK", "detail": str(sf)})
    else:
        checks.append({"name": "sentinel", "status": "WARN",
                       "detail": "no Cyber Sentinel artifact"})
        exit_code = max(exit_code, 1)

    verdict = {0: "OK", 1: "WARN", 2: "ERROR"}[exit_code]
    print(json.dumps({"verdict": verdict, "checks": checks}, indent=2, ensure_ascii=False))
    return exit_code


# ── Webhook ───────────────────────────────────────────────────────────────────

def _post(url: str, payload: dict) -> None:
    """Sync POST. Never raises (fire-and-forget)."""
    try:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "m5-watcher/2.5.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            _ = resp.read()
    except Exception:
        pass


def webhook_post(url: str, payload: dict) -> None:
    """Schedule fire-and-forget POST. Async-safe (uses to_thread if loop present)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _post(url, payload)
        return
    loop.create_task(asyncio.to_thread(_post, url, payload))


# ── argparse registration ────────────────────────────────────────────────────

def add_subparsers(parser: argparse.ArgumentParser) -> None:
    """Register all CLI subcommands on *parser*."""
    subs = parser.add_subparsers(dest="cmd", metavar="COMMAND")

    p_snap = subs.add_parser("snapshot",
        help="JSON dump of all system vitals.")
    p_snap.add_argument("--output", "-o", metavar="FILE", default=None,
        help="Write to FILE instead of stdout.")
    p_snap.add_argument("--pretty", action="store_true",
        help="Pretty-print JSON (indent=2).")
    p_snap.set_defaults(func=cmd_snapshot)

    p_tail = subs.add_parser("tail-feed",
        help=f"Follow {_METRICS_FILE} live.")
    p_tail.set_defaults(func=cmd_tail_feed)

    p_kpi = subs.add_parser("export-kpi",
        help="Dump KPI.md frontmatter as CSV or JSON.")
    p_kpi.add_argument("--format", "-f", choices=["csv", "json"],
        default="csv", help="Output format.")
    p_kpi.set_defaults(func=cmd_export_kpi)

    p_health = subs.add_parser("health",
        help="One-shot health check (KPI/vault/jarvis/sentinel).")
    p_health.set_defaults(func=cmd_health)
