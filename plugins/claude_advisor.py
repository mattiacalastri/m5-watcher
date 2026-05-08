"""
plugins/claude_advisor.py — Agentic LLM advisor for m5-watcher (sess.1694).

Reads /tmp/polpo_m5_resources.json, asks Claude Haiku via LiteLLM proxy for a
diagnostic + 1 concrete action recommendation, appends result to
~/.m5-watcher/advisor.jsonl.

Modes:
  python claude_advisor.py --once     # single advice cycle, prints to stdout
  python claude_advisor.py --loop     # daemon loop, every $INTERVAL seconds
  python claude_advisor.py --tail     # tail last N entries from JSONL

Zero deps beyond stdlib (urllib).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROXY_URL = os.environ.get("LITELLM_PROXY_URL", "https://proxy.astradigital.marketing")
PROXY_KEY = os.environ.get("LITELLM_PROXY_KEY", "")  # vk-scripts-local — set via env or LaunchAgent plist
MODEL = os.environ.get("ADVISOR_MODEL", "claude-haiku-4-5")
INTERVAL = int(os.environ.get("ADVISOR_INTERVAL", "60"))

SNAPSHOT_PATH = Path("/tmp/polpo_m5_resources.json")
OUT_DIR = Path.home() / ".m5-watcher"
OUT_PATH = OUT_DIR / "advisor.jsonl"

SYSTEM_PROMPT = """Sei l'Advisor del cockpit M5 Max di Mattia Calastri.

Ricevi uno snapshot JSON delle risorse del Mac (RAM, swap, CPU, thermal, agent_budget).

Output OBBLIGATORIO: un singolo oggetto JSON con questi campi (no markdown, no prose):
{
  "verdict": "OPTIMAL" | "WATCH" | "ACT" | "CRITICAL",
  "headline": "<= 80 char, italiano, asciutto>",
  "reasoning": "<2-3 frasi tecniche, italiano>",
  "action": "<1 azione concreta o 'none'>",
  "confidence": <float 0..1>
}

Soglie guida:
- swap > 90% sostenuto → WATCH minimo
- ram free < 4 GB → ACT
- thermal != NOMINAL → ACT
- compressed_gb > 8 → WATCH (memoria pesante)
- altrimenti OPTIMAL

Sii pragmatico. Niente filosofia. Niente raccomandazioni generiche tipo "monitorare"."""


def load_snapshot() -> dict:
    if not SNAPSHOT_PATH.exists():
        raise FileNotFoundError(f"Snapshot mancante: {SNAPSHOT_PATH} — daemon LEGACY giù?")
    return json.loads(SNAPSHOT_PATH.read_text())


def call_llm(snapshot: dict, timeout: float = 20.0) -> dict:
    if not PROXY_KEY:
        raise RuntimeError(
            "LITELLM_PROXY_KEY non impostata — exporta vkey o aggiungi a LaunchAgent EnvironmentVariables."
        )
    payload = {
        "model": MODEL,
        "max_tokens": 400,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": f"Snapshot:\n```json\n{json.dumps(snapshot, indent=2)}\n```\n\nDammi il verdetto JSON."}
        ],
    }
    req = urllib.request.Request(
        f"{PROXY_URL}/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": PROXY_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())

    text = body["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    advice = json.loads(text)
    advice["model"] = body.get("model", MODEL)
    advice["usage"] = body.get("usage", {})
    return advice


def cycle_once(verbose: bool = False) -> dict:
    snapshot = load_snapshot()
    started = time.time()
    advice = call_llm(snapshot)
    advice["ts"] = datetime.now(timezone.utc).isoformat()
    advice["snapshot_ts"] = snapshot.get("ts")
    advice["snapshot_status"] = snapshot.get("status")
    advice["latency_ms"] = int((time.time() - started) * 1000)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("a") as fp:
        fp.write(json.dumps(advice, ensure_ascii=False) + "\n")

    if verbose:
        print(json.dumps(advice, ensure_ascii=False, indent=2))
    return advice


def run_loop(interval: int = INTERVAL) -> None:
    print(f"[advisor] loop start — interval={interval}s — out={OUT_PATH}", flush=True)
    while True:
        try:
            advice = cycle_once()
            print(
                f"[advisor] {advice['ts']} verdict={advice['verdict']} "
                f"headline={advice['headline'][:60]!r} latency={advice['latency_ms']}ms",
                flush=True,
            )
        except urllib.error.HTTPError as exc:
            print(f"[advisor] HTTP {exc.code}: {exc.reason}", file=sys.stderr, flush=True)
        except Exception as exc:
            print(f"[advisor] error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        time.sleep(interval)


def tail(n: int = 5) -> None:
    if not OUT_PATH.exists():
        print("(empty — advisor never ran)")
        return
    lines = OUT_PATH.read_text().strip().splitlines()[-n:]
    for line in lines:
        entry = json.loads(line)
        print(f"{entry['ts']} [{entry['verdict']}] {entry['headline']}")
        if entry.get("action") and entry["action"] != "none":
            print(f"  → action: {entry['action']}")


# ── Textual widget (tab "🤖 Advisor") ───────────────────────────────────────

def _verdict_color(verdict: str) -> str:
    return {
        "OPTIMAL": "bold green",
        "WATCH": "bold yellow",
        "ACT": "bold dark_orange",
        "CRITICAL": "bold red",
    }.get(verdict, "bold white")


def _age_str(iso_ts: str) -> str:
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        delta = (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return "?"
    if delta < 60:
        return f"{int(delta)}s fa"
    if delta < 3600:
        return f"{int(delta // 60)}m fa"
    return f"{int(delta // 3600)}h fa"


def _build_widget():
    """Lazy import — Textual only loaded when widget is constructed."""
    from textual.containers import Vertical
    from textual.widgets import Static

    class AdvisorWidget(Vertical):
        DEFAULT_CSS = """
        AdvisorWidget { padding: 1 2; }
        AdvisorWidget #advisor-headline { padding-bottom: 1; }
        AdvisorWidget #advisor-reasoning { color: $text-muted; padding-bottom: 1; }
        AdvisorWidget #advisor-action { color: cyan; padding-bottom: 1; }
        AdvisorWidget #advisor-meta { color: $text-muted; }
        """

        def compose(self):
            yield Static("🤖 Claude Advisor — caricamento…", id="advisor-headline")
            yield Static("", id="advisor-reasoning")
            yield Static("", id="advisor-action")
            yield Static("", id="advisor-meta")

        def on_mount(self) -> None:
            self.refresh_advice()
            self.set_interval(15.0, self.refresh_advice)

        def refresh_advice(self) -> None:
            if not OUT_PATH.exists():
                self.query_one("#advisor-headline", Static).update(
                    "[dim]Nessun advice ancora — daemon partito da poco?[/dim]"
                )
                return
            try:
                last = OUT_PATH.read_text().strip().splitlines()[-1]
                entry = json.loads(last)
            except Exception as exc:
                self.query_one("#advisor-headline", Static).update(
                    f"[red]Errore lettura advisor.jsonl: {exc}[/red]"
                )
                return

            verdict = entry.get("verdict", "?")
            color = _verdict_color(verdict)
            self.query_one("#advisor-headline", Static).update(
                f"[{color}]● {verdict}[/{color}]  {entry.get('headline', '')}"
            )
            self.query_one("#advisor-reasoning", Static).update(
                entry.get("reasoning", "")
            )
            action = entry.get("action") or "none"
            if action and action != "none":
                self.query_one("#advisor-action", Static).update(f"→ {action}")
            else:
                self.query_one("#advisor-action", Static).update("[dim]nessuna azione richiesta[/dim]")
            usage = entry.get("usage", {})
            tok_in = usage.get("input_tokens", "?")
            tok_out = usage.get("output_tokens", "?")
            self.query_one("#advisor-meta", Static).update(
                f"[dim]model={entry.get('model', '?')} · "
                f"latency={entry.get('latency_ms', '?')}ms · "
                f"tokens={tok_in}→{tok_out} · "
                f"snapshot={entry.get('snapshot_status', '?')} · "
                f"{_age_str(entry.get('ts', ''))}[/dim]"
            )

    return AdvisorWidget()


try:
    from plugin_loader import register_tab  # type: ignore
    register_tab(id="tab-advisor", label="🤖 Advisor", key="a")(_build_widget)
except ImportError:  # plugin loaded standalone, no problem
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude advisor for m5-watcher")
    parser.add_argument("--once", action="store_true", help="single advice cycle")
    parser.add_argument("--loop", action="store_true", help="daemon loop")
    parser.add_argument("--tail", type=int, nargs="?", const=5, help="tail last N entries")
    parser.add_argument("--interval", type=int, default=INTERVAL, help="loop interval seconds")
    args = parser.parse_args()

    if args.tail is not None:
        tail(args.tail)
        return 0
    if args.loop:
        run_loop(args.interval)
        return 0
    cycle_once(verbose=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
