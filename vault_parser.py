"""Vault parser — wikilink extractor + NetworkX graph + Neural Density intelligence."""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

import networkx as nx

VAULT_PATH = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Astra Digital Marketing"
)
_WIKILINK  = re.compile(r'\[\[([^\[\]|#\n]+?)(?:[|#][^\[\]]*?)?\]\]')
_STATUS_RE = re.compile(r'^status:\s*["\']?(\w+)["\']?\s*$', re.MULTILINE)

TOP_N     = 120
CACHE_TTL = 60.0   # vault changes slowly; 60s is plenty

_cache:    dict | None = None
_cache_ts: float       = 0.0


def _classify(G: nx.DiGraph, node: str) -> str:
    name = node.upper()
    if name.startswith("MOC") or "— MOC" in node or name.startswith("🗺"):
        return "moc"
    if G.in_degree(node) == 0:   # unreachable by navigation — no one links here
        return "orphan"
    return "normal"


def vault_graph_data(vault: Path = VAULT_PATH) -> dict:
    """Parse vault → graph + Neural Density metrics. Safe for asyncio.to_thread."""
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache and now - _cache_ts < CACHE_TTL:
        return _cache

    G: nx.DiGraph            = nx.DiGraph()
    stem_index: dict[str, str] = {}

    try:
        md_files = list(vault.rglob("*.md"))
    except OSError as exc:
        return {
            "error": f"vault non trovato: {exc}",
            "graph": nx.DiGraph(), "pos": {}, "stats": {}, "intel": {},
        }

    # Pass 1 — register nodes + mtime (stat only, no file read)
    mtime_map: dict[str, float] = {}
    for f in md_files:
        stem = f.stem
        G.add_node(stem, path=str(f))
        stem_index[stem.lower()] = stem
        try:
            mtime_map[stem] = f.stat().st_mtime
        except OSError:
            mtime_map[stem] = 0.0

    # Pass 2 — single read per file: wikilinks + frontmatter status
    status_dist: dict[str, int] = {"seed": 0, "growing": 0, "evergreen": 0, "stub": 0}
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
        head  = "\n".join(text.splitlines()[:25])
        st_m  = _STATUS_RE.search(head)
        if st_m:
            st = st_m.group(1).lower()
            if st in status_dist:
                status_dist[st] += 1

    # Classify
    for node in G.nodes:
        G.nodes[node]["type"] = _classify(G, node)

    total   = G.number_of_nodes()
    orphans = sum(1 for n in G.nodes if G.nodes[n]["type"] == "orphan")
    mocs    = sum(1 for n in G.nodes if G.nodes[n]["type"] == "moc")
    edges   = G.number_of_edges()

    # Spring layout on top-N (kept for backward compat)
    ranked  = sorted(G.nodes, key=lambda n: G.degree(n), reverse=True)
    visible = ranked[:TOP_N]
    sub     = G.subgraph(visible)
    pos     = (
        nx.spring_layout(sub, k=1.8, iterations=40, seed=42)
        if len(sub) > 1 else {}
    )

    # ── Neural Density — betweenness first (scores needed for data attractors) ──
    bet_scores: dict[str, float] = {}
    top_bridges: list[tuple[str, float]] = []
    if len(sub) > 5:
        try:
            bet = nx.betweenness_centrality(sub, k=min(30, len(sub)), normalized=True)
            bet_scores  = bet
            top_bridges = sorted(bet.items(), key=lambda x: x[1], reverse=True)[:5]
        except Exception:
            pass

    # Data attractors — top 10 by in-degree with full biometric profile
    top_by_ind = sorted(G.nodes, key=lambda n: G.in_degree(n), reverse=True)[:10]
    top_indegree_data: list[tuple[str, int, int, str, float]] = [
        (n, G.in_degree(n), G.out_degree(n), G.nodes[n].get("type", "normal"), bet_scores.get(n, 0.0))
        for n in top_by_ind
    ]

    # Weakly-connected components
    wcc        = sorted(nx.weakly_connected_components(G), key=len, reverse=True)
    n_clusters = len(wcc)
    top_clusters = [
        (max(c, key=lambda n: G.degree(n)), len(c))
        for c in wcc[:5]
    ]
    giant_ratio = len(wcc[0]) / total if wcc and total else 0.0

    # Density + average degree (undirected equivalent)
    density    = nx.density(G)
    avg_degree = (2 * edges / total) if total else 0.0

    # Clustering coefficient (sparse graphs → fast)
    try:
        clustering = nx.average_clustering(G.to_undirected())
    except Exception:
        clustering = 0.0

    # Recent activity
    now_ts        = time.time()
    recent_today: list[tuple[str, str]] = []
    recent_7d     = 0
    for stem, mtime in mtime_map.items():
        age_s = now_ts - mtime
        if age_s < 86400:
            recent_today.append((stem, datetime.fromtimestamp(mtime).strftime("%H:%M")))
        if age_s < 7 * 86400:
            recent_7d += 1
    recent_today.sort(key=lambda x: x[1], reverse=True)

    _cache = {
        "graph": G,
        "pos":   pos,
        "stats": {
            "total":   total,
            "orphans": orphans,
            "mocs":    mocs,
            "edges":   edges,
            "visible": len(visible),
        },
        "intel": {
            "status_dist":   status_dist,
            "recent_today":  recent_today[:8],
            "recent_7d":     recent_7d,
            "top_indegree":  top_indegree_data,   # (name, in, out, type, betweenness)
            "n_clusters":    n_clusters,
            "top_clusters":  top_clusters,
            "giant_ratio":   giant_ratio,
            "density":       density,
            "avg_degree":    avg_degree,
            "clustering":    clustering,
            "top_bridges":   top_bridges,
        },
    }
    _cache_ts = now
    return _cache
