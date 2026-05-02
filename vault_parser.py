"""Vault parser — regex wikilink extractor + NetworkX graph + spring layout."""
from __future__ import annotations

import re
import time
from pathlib import Path

import networkx as nx

VAULT_PATH = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Astra Digital Marketing"
)
_WIKILINK = re.compile(r'\[\[([^\[\]|#\n]+?)(?:[|#][^\[\]]*?)?\]\]')

# top-N nodes by degree fed to spring_layout (O(N²) — keep < 150)
TOP_N = 120
CACHE_TTL = 30.0  # seconds

_cache: dict | None = None
_cache_ts: float = 0.0


def _classify(G: nx.DiGraph, node: str) -> str:
    name = node.upper()
    if name.startswith("MOC") or "— MOC" in node or name.startswith("🗺"):
        return "moc"
    if G.in_degree(node) == 0 and G.out_degree(node) == 0:
        return "orphan"
    return "normal"


def vault_graph_data(vault: Path = VAULT_PATH) -> dict:
    """Return cached graph dict — safe to call from asyncio.to_thread."""
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache and now - _cache_ts < CACHE_TTL:
        return _cache

    G: nx.DiGraph = nx.DiGraph()
    stem_index: dict[str, str] = {}

    try:
        md_files = list(vault.rglob("*.md"))
    except OSError as exc:
        return {
            "error": f"vault non trovato: {exc}",
            "graph": nx.DiGraph(),
            "pos": {},
            "stats": {},
        }

    for f in md_files:
        stem = f.stem
        G.add_node(stem, path=str(f))
        stem_index[stem.lower()] = stem

    for f in md_files:
        src = f.stem
        try:
            text = f.read_text(errors="ignore", encoding="utf-8")
        except OSError:
            continue
        for m in _WIKILINK.finditer(text):
            raw = m.group(1).strip()
            if "/" in raw:
                raw = raw.rsplit("/", 1)[-1]
            target = stem_index.get(raw.lower())
            if target and target != src:
                G.add_edge(src, target)

    for node in G.nodes:
        G.nodes[node]["type"] = _classify(G, node)
        G.nodes[node]["degree"] = G.degree(node)

    total   = G.number_of_nodes()
    orphans = sum(1 for n in G.nodes if G.nodes[n]["type"] == "orphan")
    mocs    = sum(1 for n in G.nodes if G.nodes[n]["type"] == "moc")
    edges   = G.number_of_edges()

    ranked  = sorted(G.nodes, key=lambda n: G.degree(n), reverse=True)
    visible = ranked[:TOP_N]
    sub     = G.subgraph(visible)

    if len(sub) > 1:
        pos = nx.spring_layout(sub, k=1.8, iterations=40, seed=42)
    else:
        pos = {n: (0.0, 0.0) for n in sub.nodes}

    _cache = {
        "graph": G,
        "pos": pos,
        "stats": {
            "total": total,
            "orphans": orphans,
            "mocs": mocs,
            "edges": edges,
            "visible": len(visible),
        },
    }
    _cache_ts = now
    return _cache
