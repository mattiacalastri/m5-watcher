#!/usr/bin/env python3
"""
m5-watcher web companion — sess.1895.
Espone la TUI come web app accessibile da LAN (iPhone Safari, iPad, ecc.).

Usage:
    ./venv/bin/python serve_web.py           # localhost only
    ./venv/bin/python serve_web.py --lan     # 0.0.0.0 (LAN visible)

Env:
    PORT (default 8765)
"""
import os
import sys
import socket
from pathlib import Path

# Bootstrap venv if not already in it
PROJECT = Path(__file__).resolve().parent

try:
    from textual_serve.server import Server
except ImportError:
    print("❌ textual-serve non installato. Run:", file=sys.stderr)
    print("   ./venv/bin/pip install textual-serve", file=sys.stderr)
    sys.exit(1)


def get_lan_ip() -> str:
    """Best-effort LAN IP detection (en0 / en1)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?"


def main() -> None:
    lan_mode = "--lan" in sys.argv
    port = int(os.environ.get("PORT", 8780))
    host = "0.0.0.0" if lan_mode else "localhost"

    cmd = f"{PROJECT}/venv/bin/python {PROJECT}/app.py"

    print("🐙 m5-watcher web companion")
    print(f"    Local:   http://localhost:{port}")
    if lan_mode:
        print(f"    LAN:     http://{get_lan_ip()}:{port}  (iPhone/iPad)")
    print(f"    Stop:    Ctrl-C")
    print()

    server = Server(cmd, host=host, port=port, title="🐙 m5-watcher")
    server.serve()


if __name__ == "__main__":
    main()
