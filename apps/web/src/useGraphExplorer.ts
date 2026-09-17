import { useCallback, useEffect, useMemo, useState } from 'react'
import type { GraphResponse } from './types'

export interface PathEndpoints {
  from: string
  to: string
}

/**
 * Tracks the interactive, per-node-click state layered on top of the default
 * domain/entity overview: which nodes the user has explicitly expanded to reveal
 * their 1-hop neighborhood, Focus Mode, and an in-progress/resolved Find Path
 * request. Operates purely client-side over an already-fetched `GraphResponse` -
 * the default tier itself is the existing type-filter checkbox set (defaulted to
 * domain+entity), so this hook only owns the "explicitly requested more" state.
 */
export function useGraphExplorer(graph: GraphResponse | null) {
  // The literal set of nodes the user asked to expand. `expandedIds` (below) is
  // derived fresh from this each time, so collapsing a seed correctly drops
  // neighbors no other still-expanded seed needs, without tracking that by hand.
  const [expandedSeeds, setExpandedSeeds] = useState<Set<string>>(new Set())
  const [focusMode, setFocusMode] = useState(false)
  const [pathEndpoints, setPathEndpoints] = useState<PathEndpoints | null>(null)

  // A new bundle/version invalidates any expansion, focus, or in-flight path picked
  // against the previous graph's node/edge set.
  useEffect(() => {
    setExpandedSeeds(new Set())
    setFocusMode(false)
    setPathEndpoints(null)
  }, [graph?.version])

  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>()
    if (!graph) return map
    for (const edge of graph.edges) {
      if (!map.has(edge.source)) map.set(edge.source, new Set())
      if (!map.has(edge.target)) map.set(edge.target, new Set())
      map.get(edge.source)!.add(edge.target)
      map.get(edge.target)!.add(edge.source)
    }
    return map
  }, [graph])

  const expandedIds = useMemo(() => {
    const ids = new Set<string>()
    for (const seed of expandedSeeds) {
      ids.add(seed)
      for (const neighbor of adjacency.get(seed) ?? []) ids.add(neighbor)
    }
    return ids
  }, [expandedSeeds, adjacency])

  const expand = useCallback((nodeId: string) => {
    setExpandedSeeds((current) => new Set(current).add(nodeId))
  }, [])

  const collapse = useCallback((nodeId: string) => {
    setExpandedSeeds((current) => {
      const next = new Set(current)
      next.delete(nodeId)
      return next
    })
  }, [])

  const resetExpansion = useCallback(() => setExpandedSeeds(new Set()), [])

  const findPath = useCallback((from: string, to: string) => setPathEndpoints({ from, to }), [])
  const clearPath = useCallback(() => setPathEndpoints(null), [])

  // Shortest node-id path between the two endpoints over the same client-side
  // adjacency `expand` uses - mirrors the backend's `SemanticRetriever.path` BFS
  // (`/api/graph/path`) so Find Path works without an extra round trip once the
  // full graph is already resident client-side.
  const pathIds = useMemo(() => {
    if (!pathEndpoints) return null
    const { from, to } = pathEndpoints
    if (!adjacency.has(from) || !adjacency.has(to)) return null
    if (from === to) return [from]
    const parents = new Map<string, string>([[from, from]])
    let frontier = [from]
    while (frontier.length) {
      const next: string[] = []
      for (const item of frontier) {
        for (const neighbor of adjacency.get(item) ?? []) {
          if (parents.has(neighbor)) continue
          parents.set(neighbor, item)
          if (neighbor === to) {
            const route = [to]
            while (route[route.length - 1] !== from) route.push(parents.get(route[route.length - 1])!)
            return route.reverse()
          }
          next.push(neighbor)
        }
      }
      frontier = next
    }
    return null
  }, [pathEndpoints, adjacency])

  return {
    expandedIds,
    expand,
    collapse,
    resetExpansion,
    focusMode,
    setFocusMode,
    pathEndpoints,
    pathIds,
    findPath,
    clearPath,
  }
}
