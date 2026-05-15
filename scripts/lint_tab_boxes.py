#!/usr/bin/env python3
"""
m5-watcher tab-box linter — sess.1895.
Garantisce che ogni TabPane abbia un box border definito in CSS.
Sintomo madre: radar + tgbots erano box-less per oblio CSS.

Usage:
    python3 scripts/lint_tab_boxes.py
    # exit 0 = ok, exit 1 = drift detected

Integrazione pre-commit (opzionale):
    cd m5-watcher && python3 scripts/lint_tab_boxes.py || exit 1
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


def main() -> int:
    src = APP_PY.read_text()

    # Estrae tutti gli id ScrollableContainer dichiarati nel codice Python.
    container_ids = set(re.findall(r'ScrollableContainer\(id="([a-z0-9-]+)"\)', src))

    # Estrae tutti gli id che hanno una regola border heavy in CSS.
    # Supporta selettori multi-id `#a, #b {{ ... }}`.
    css_boxed_ids = set()
    block_re = re.compile(r'((?:#[a-z0-9-]+\s*,\s*)*#[a-z0-9-]+)\s*\{\{(.*?)\}\}', re.DOTALL)
    for m in block_re.finditer(src):
        selectors_str, body = m.group(1), m.group(2)
        if re.search(r'border:\s*heavy', body):
            for sel in re.findall(r'#([a-z0-9-]+)', selectors_str):
                css_boxed_ids.add(sel)

    # Whitelist — id che NON sono tab content (modal, popup, transient).
    whitelist = {"triage-outer"}
    missing_raw = container_ids - css_boxed_ids
    missing = missing_raw - whitelist

    # R3 sess.1895: audit padding/height divergence per design system uniformity.
    padding_variants: dict[str, set[str]] = {}
    height_variants: dict[str, set[str]] = {}
    for m in block_re.finditer(src):
        selectors_str, body = m.group(1), m.group(2)
        ids = re.findall(r'#([a-z0-9-]+)', selectors_str)
        if not any(cid in container_ids for cid in ids):
            continue
        pad = re.search(r'padding:\s*([^;]+);', body)
        hei = re.search(r'^\s*height:\s*([^;]+);', body, re.MULTILINE)
        if pad:
            padding_variants.setdefault(pad.group(1).strip(), set()).update(ids)
        if hei:
            height_variants.setdefault(hei.group(1).strip(), set()).update(ids)

    inconsistencies = []
    if len(padding_variants) > 2:  # tollero 2 padding (es. 1 3 + 1 2)
        inconsistencies.append(f"padding variants: {len(padding_variants)} → {sorted(padding_variants.keys())}")
    if len(height_variants) > 2:
        inconsistencies.append(f"height variants: {len(height_variants)} → {sorted(height_variants.keys())}")

    if not missing and not inconsistencies:
        print(f"✅ tab-box lint OK — {len(container_ids)} container, tutti con border heavy CSS.")
        print(f"   Design system: {len(padding_variants)} padding · {len(height_variants)} height variants.")
        return 0

    if missing:
        print(f"❌ tab-box lint FAIL — {len(missing)} container SENZA border CSS:")
        for cid in sorted(missing):
            print(f"   • #{cid}")
        print()
    if inconsistencies:
        print(f"⚠️  design system drift:")
        for issue in inconsistencies:
            print(f"   • {issue}")
        print()
    if not missing:
        return 0  # solo warning, no fail su drift padding/height
    print()
    print("Fix: aggiungi in CSS string blocco styles (template in templates/new_tab_template.py):")
    print()
    for cid in sorted(missing):
        print(f"    #{cid} {{{{")
        print(f"        background: {{BG_ALT}};")
        print(f"        border: heavy {{TEAL}};")
        print(f"        padding: 1 3;")
        print(f"        height: 1fr;")
        print(f"    }}}}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
