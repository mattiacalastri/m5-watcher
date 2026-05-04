"""bench/run_bench.py — empirical telemetry benchmark for m5-watcher.

Runs M5Watcher headless via textual.app.run_test() for ~8 seconds,
then reads Metrics from the live app instance and produces a structured
JSON verdict.

Usage:
    python -m bench.run_bench [--markdown] [--json-out PATH]

Exit codes:
    0 = PASS   (all thresholds met)
    1 = WARN   (one threshold missed)
    2 = FAIL   (two or more thresholds missed)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# ── Path bootstrap: allow `python -m bench.run_bench` from project root ──────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Thresholds ────────────────────────────────────────────────────────────────
THRESHOLDS = {
    "frame_p95_ms":    33.0,   # 2 frames @ 60fps budget
    "cache_ratio_min": 0.5,    # >50% hit ratio post-warmup
    "rss_max_mb":      500.0,  # resident set ceiling
}

# ── Bench duration ────────────────────────────────────────────────────────────
BENCH_SECONDS = 8   # long enough for multiple fast+slow ticks


async def _run_pilot() -> dict:
    """Run M5Watcher headless and collect Metrics snapshot after BENCH_SECONDS."""
    # Import here so sys.path manipulation is already done
    import app as m5_app  # noqa: PLC0415

    app_instance = m5_app.M5Watcher()

    async with app_instance.run_test(headless=True, size=(220, 50)) as pilot:
        # Let the app run its on_mount + initial refreshes, then tick
        # by simulating key presses at intervals to keep the event loop alive.
        # We press 'r' (refresh binding) periodically and also just await time.
        deadline = time.monotonic() + BENCH_SECONDS
        while time.monotonic() < deadline:
            await pilot.press("r")
            await asyncio.sleep(0.5)

        # Collect final metrics snapshot from the live app instance
        m = app_instance._metrics
        s = m.summary()

    return s


def _evaluate(s: dict) -> dict:
    """Apply thresholds and produce verdict dict."""
    frame_p95  = s["frame_ms"]["p95"]
    cache_ratio = s["cache"]["ratio"]
    rss_mb      = s["rss_mb"]["last"]

    results = {
        "frame_p50_ms":  s["frame_ms"]["p50"],
        "frame_p95_ms":  frame_p95,
        "frame_p99_ms":  s["frame_ms"]["p99"],
        "cache_ratio":   cache_ratio,
        "rss_mb":        rss_mb,
        "flash_count":   s["flash"]["count"],
        "frame_samples": s["frame_ms"]["n"],
        "uptime_s":      s["uptime_s"],
    }

    misses: list[str] = []
    if frame_p95 >= THRESHOLDS["frame_p95_ms"]:
        misses.append(
            f"frame_p95={frame_p95:.1f}ms >= {THRESHOLDS['frame_p95_ms']}ms"
        )
    if cache_ratio < THRESHOLDS["cache_ratio_min"] and s["cache"]["hits"] + s["cache"]["misses"] > 0:
        misses.append(
            f"cache_ratio={cache_ratio:.3f} < {THRESHOLDS['cache_ratio_min']}"
        )
    if rss_mb > THRESHOLDS["rss_max_mb"]:
        misses.append(
            f"rss={rss_mb:.1f}MB > {THRESHOLDS['rss_max_mb']}MB"
        )

    if len(misses) == 0:
        verdict = "PASS"
        detail  = "All thresholds met."
    elif len(misses) == 1:
        verdict = "WARN"
        detail  = f"One threshold missed: {misses[0]}"
    else:
        verdict = "FAIL"
        detail  = f"{len(misses)} thresholds missed: " + "; ".join(misses)

    return {
        "verdict":        verdict,
        "thresholds":     THRESHOLDS,
        "results":        results,
        "verdict_detail": detail,
        "ts":             s["ts"],
    }


def _markdown_report(bench: dict) -> str:
    """Render a Markdown summary of the bench result."""
    v = bench["verdict"]
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(v, "?")
    r = bench["results"]
    t = bench["thresholds"]

    lines = [
        f"# m5-watcher bench report — {v} {icon}",
        "",
        f"> {bench['verdict_detail']}",
        "",
        "## Results vs Thresholds",
        "",
        "| Metric | Value | Threshold | Status |",
        "|--------|-------|-----------|--------|",
        f"| frame p95 ms | {r['frame_p95_ms']:.2f} | < {t['frame_p95_ms']} | {'OK' if r['frame_p95_ms'] < t['frame_p95_ms'] else 'MISS'} |",
        f"| frame p50 ms | {r['frame_p50_ms']:.2f} | — | — |",
        f"| frame p99 ms | {r['frame_p99_ms']:.2f} | — | — |",
        f"| cache ratio | {r['cache_ratio']:.3f} | > {t['cache_ratio_min']} | {'OK' if r['cache_ratio'] >= t['cache_ratio_min'] else 'MISS'} |",
        f"| RSS MB | {r['rss_mb']:.1f} | < {t['rss_max_mb']} | {'OK' if r['rss_mb'] <= t['rss_max_mb'] else 'MISS'} |",
        f"| flash count | {r['flash_count']} | — | — |",
        f"| frame samples | {r['frame_samples']} | — | — |",
        f"| uptime s | {r['uptime_s']} | — | — |",
        "",
        f"_Generated: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(bench['ts']))}_",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Empirical telemetry benchmark for m5-watcher"
    )
    parser.add_argument(
        "--markdown", action="store_true",
        help="Also print a Markdown report to stdout after the JSON result"
    )
    parser.add_argument(
        "--json-out", metavar="PATH", default=None,
        help="Write JSON result to this file (in addition to stdout)"
    )
    args = parser.parse_args()

    print(f"[bench] running headless pilot for {BENCH_SECONDS}s ...", file=sys.stderr)

    try:
        snapshot = asyncio.run(_run_pilot())
    except Exception as exc:
        error_result = {
            "verdict": "FAIL",
            "thresholds": THRESHOLDS,
            "results": {},
            "verdict_detail": f"Pilot failed: {exc}",
            "ts": time.time(),
        }
        print(json.dumps(error_result, indent=2))
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(error_result, indent=2))
        sys.exit(2)

    bench = _evaluate(snapshot)

    json_output = json.dumps(bench, indent=2)
    print(json_output)

    if args.json_out:
        Path(args.json_out).write_text(json_output)
        print(f"[bench] JSON written to {args.json_out}", file=sys.stderr)

    if args.markdown:
        print()
        print(_markdown_report(bench))

    verdict_exit = {"PASS": 0, "WARN": 1, "FAIL": 2}.get(bench["verdict"], 2)
    print(
        f"[bench] verdict={bench['verdict']} exit={verdict_exit}",
        file=sys.stderr,
    )
    sys.exit(verdict_exit)


if __name__ == "__main__":
    main()
