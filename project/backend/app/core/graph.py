"""Dependency graph utilities: cycle detection and topological layering."""
from __future__ import annotations

from typing import Iterable, Mapping, Optional


def find_cycle(graph: Mapping) -> Optional[list]:
    """Return a cycle as a list of node ids (first == last) or None. Iterative DFS."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    for root in graph:
        if color[root] != WHITE:
            continue
        stack = [(root, iter(graph[root]))]
        path = [root]
        color[root] = GREY
        while stack:
            node, it = stack[-1]
            for nxt in it:
                if nxt not in color:
                    continue
                if color[nxt] == GREY:
                    return path[path.index(nxt):] + [nxt]
                if color[nxt] == WHITE:
                    color[nxt] = GREY
                    path.append(nxt)
                    stack.append((nxt, iter(graph[nxt])))
                    break
            else:
                color[node] = BLACK
                path.pop()
                stack.pop()
    return None


def topo_layers(graph: Mapping) -> list:
    """Layers of node ids; each node depends only on earlier layers (parallelisable within a layer).
    Raises ValueError on cycles."""
    deps = {n: set(d) for n, d in graph.items()}
    layers, done = [], set()
    while len(done) < len(deps):
        layer = sorted(n for n, d in deps.items() if n not in done and d <= done)
        if not layer:
            raise ValueError("dependency cycle detected")
        layers.append(layer)
        done.update(layer)
    return layers
