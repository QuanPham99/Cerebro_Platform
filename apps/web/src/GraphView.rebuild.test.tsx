import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { GraphResponse } from './types'

// Cytoscape needs a real canvas, so it is replaced by a chainable stub that
// records how many cores were built and which tap handlers were registered.
const cytoscapeState = vi.hoisted(() => ({
  builds: 0,
  handlers: [] as Array<{ event: string; selector: unknown; handler: (event: unknown) => void }>,
}))

vi.mock('cytoscape', () => {
  const chainable = (): unknown => new Proxy(function stub() {}, {
    get: (_target, key) => {
      if (key === 'length') return 0
      if (key === 'forEach') return () => {}
      if (key === 'zoom') return () => 1
      if (key === 'batch') return (fn: () => void) => fn()
      return (..._args: unknown[]) => chainable()
    },
  })
  const factory = Object.assign(() => {
    cytoscapeState.builds += 1
    const core = chainable() as Record<string, unknown>
    return new Proxy(core, {
      get: (target, key) => key === 'on'
        ? (event: string, selector: unknown, handler?: (event: unknown) => void) => {
            if (typeof selector === 'function') cytoscapeState.handlers.push({ event, selector: null, handler: selector as (event: unknown) => void })
            else if (handler) cytoscapeState.handlers.push({ event, selector, handler })
          }
        : target[key as string],
    })
  }, { use: () => {} })
  return { default: factory }
})
vi.mock('cytoscape-fcose', () => ({ default: {} }))

const { GraphView } = await import('./GraphView')

const graph: GraphResponse = {
  nodes: [
    { id: 'entity.customer', label: 'Customer', type: 'entity', profile_kind: 'entity', description: '' },
    { id: 'entity.account', label: 'Account', type: 'entity', profile_kind: 'entity', description: '' },
  ],
  edges: [{ id: 'e1', source: 'entity.customer', target: 'entity.account', type: 'semantic_relationship', label: 'owns' }],
} as unknown as GraphResponse

beforeAll(() => {
  window.matchMedia ??= (() => ({ matches: false })) as unknown as typeof window.matchMedia
})
afterEach(() => {
  cleanup()
  cytoscapeState.builds = 0
  cytoscapeState.handlers = []
})

describe('GraphView node selection', () => {
  it('does not rebuild (re-layout) the graph when a node is selected and onSelect changes identity', () => {
    const visibleIds = new Set(graph.nodes.map((node) => node.id))
    const first = vi.fn()
    const { rerender } = render(<GraphView graph={graph} visibleIds={visibleIds} selectedId={null} onSelect={first} />)
    expect(cytoscapeState.builds).toBe(1)

    // App.tsx's select callback gets a new identity after every selection.
    const second = vi.fn()
    rerender(<GraphView graph={graph} visibleIds={visibleIds} selectedId="entity.customer" onSelect={second} />)
    expect(cytoscapeState.builds).toBe(1)

    // The tap handler registered at mount must still reach the latest callback.
    const tap = cytoscapeState.handlers.find((item) => item.event === 'tap' && item.selector === 'node')
    tap?.handler({ target: { id: () => 'entity.account' } })
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledWith('entity.account')
  })

  it('still rebuilds when the graph itself changes', () => {
    const visibleIds = new Set(graph.nodes.map((node) => node.id))
    const { rerender } = render(<GraphView graph={graph} visibleIds={visibleIds} selectedId={null} onSelect={vi.fn()} />)
    rerender(<GraphView graph={{ ...graph }} visibleIds={visibleIds} selectedId={null} onSelect={vi.fn()} />)
    expect(cytoscapeState.builds).toBe(2)
  })
})
