/// <reference lib="webworker" />
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, type SimulationLinkDatum, type SimulationNodeDatum } from 'd3-force'

// Computes node positions off the main UI thread for graphs too large for a
// synchronous Cytoscape layout to run smoothly (spec 026: "run expensive graph
// layout computation outside the main UI thread"). d3-force has no DOM
// dependency, so it runs unmodified inside a Worker.

export interface LayoutRequest {
  nodes: Array<{ id: string }>
  edges: Array<{ source: string; target: string }>
}

export interface LayoutResult {
  positions: Array<{ id: string; x: number; y: number }>
}

interface SimNode extends SimulationNodeDatum {
  id: string
}

self.onmessage = (event: MessageEvent<LayoutRequest>) => {
  const { nodes, edges } = event.data
  const simNodes: SimNode[] = nodes.map((node) => ({ id: node.id }))
  const byId = new Map(simNodes.map((node) => [node.id, node]))
  const simLinks: Array<SimulationLinkDatum<SimNode>> = edges
    .filter((edge) => byId.has(edge.source) && byId.has(edge.target))
    .map((edge) => ({ source: edge.source, target: edge.target }))

  const simulation = forceSimulation<SimNode>(simNodes)
    .force('charge', forceManyBody().strength(-220))
    .force('link', forceLink<SimNode, SimulationLinkDatum<SimNode>>(simLinks).id((node) => node.id).distance(90))
    .force('center', forceCenter(0, 0))
    .force('collide', forceCollide(24))
    .stop()

  // Run to convergence synchronously (no rendering to block, so no need for
  // requestAnimationFrame-paced ticks like d3-force uses on the main thread).
  const tickCount = Math.min(500, Math.max(150, Math.round(2000 / Math.sqrt(simNodes.length || 1))))
  for (let i = 0; i < tickCount; i += 1) simulation.tick()

  const positions = simNodes.map((node) => ({ id: node.id, x: node.x ?? 0, y: node.y ?? 0 }))
  self.postMessage({ positions } satisfies LayoutResult)
}
