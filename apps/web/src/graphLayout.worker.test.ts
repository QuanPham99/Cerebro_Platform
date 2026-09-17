import { describe, expect, it } from 'vitest'
import type { LayoutRequest, LayoutResult } from './graphLayout.worker'

// graphLayout.worker.ts assigns `self.onmessage` on import (its top-level body).
// jsdom aliases `self` to the global object, so we can import it directly here
// and drive it like a worker without mocking the Worker constructor/postMessage
// transport - this is the module's own layout math, exercised for real.
import './graphLayout.worker'

function runLayout(request: LayoutRequest): Promise<LayoutResult> {
  return new Promise((resolve) => {
    const originalPostMessage = self.postMessage
    ;(self as unknown as { postMessage: (data: LayoutResult) => void }).postMessage = (data: LayoutResult) => {
      self.postMessage = originalPostMessage
      resolve(data)
    }
    ;(self.onmessage as (event: MessageEvent<LayoutRequest>) => void)({ data: request } as MessageEvent<LayoutRequest>)
  })
}

describe('graphLayout.worker', () => {
  it('computes a distinct finite position for every node', async () => {
    const nodes = Array.from({ length: 12 }, (_, index) => ({ id: `n${index}` }))
    const edges = nodes.slice(1).map((node, index) => ({ source: nodes[index].id, target: node.id }))
    const result = await runLayout({ nodes, edges })

    expect(result.positions).toHaveLength(nodes.length)
    const ids = new Set(result.positions.map((position) => position.id))
    expect(ids).toEqual(new Set(nodes.map((node) => node.id)))
    for (const position of result.positions) {
      expect(Number.isFinite(position.x)).toBe(true)
      expect(Number.isFinite(position.y)).toBe(true)
    }
    // A chain layout shouldn't collapse every node onto the same point.
    const distinctX = new Set(result.positions.map((position) => Math.round(position.x)))
    expect(distinctX.size).toBeGreaterThan(1)
  })

  it('ignores edges that reference an unknown node id instead of throwing', async () => {
    const nodes = [{ id: 'a' }, { id: 'b' }]
    const edges = [{ source: 'a', target: 'missing' }]
    const result = await runLayout({ nodes, edges })
    expect(result.positions.map((position) => position.id).sort()).toEqual(['a', 'b'])
  })

  it('handles an empty graph without error', async () => {
    const result = await runLayout({ nodes: [], edges: [] })
    expect(result.positions).toEqual([])
  })
})
