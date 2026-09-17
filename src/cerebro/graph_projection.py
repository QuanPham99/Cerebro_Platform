"""Shared projection of a :class:`GraphResponse` down to an id allowlist.

Every graph-scoping feature (the customer self-service allowlist, and the
tier/expand progressive-disclosure params on ``GET /api/graph``) needs the same
two-step reduction: keep only the allowed nodes, then keep only the edges whose
endpoints both survived. This module is the single implementation both call.
"""

from __future__ import annotations

from .models import GraphResponse


def project_graph(graph: GraphResponse, allowed_ids: set[str]) -> GraphResponse:
    """Return a copy of ``graph`` restricted to ``allowed_ids``.

    Nodes outside the allowlist are dropped; an edge survives only if both its
    source and target are still present.
    """
    nodes = [node for node in graph.nodes if node.id in allowed_ids]
    kept_ids = {node.id for node in nodes}
    edges = [
        edge
        for edge in graph.edges
        if edge.source in kept_ids and edge.target in kept_ids
    ]
    return GraphResponse(version=graph.version, nodes=nodes, edges=edges)
