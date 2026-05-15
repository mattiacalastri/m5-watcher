#!/bin/bash
# sess.1895 — m5-watcher web companion launcher (wrapper su serve_web.py).
cd "$(dirname "$0")"
exec ./venv/bin/python serve_web.py "$@"
