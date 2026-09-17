import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useGraphExplorer } from './useGraphExplorer'
import type { GraphResponse } from './types'

const graph: GraphResponse = {
  version: '0.1.0',
  nodes: [
    { id: 'domain.retail', type: 'Domain', profile_kind: 'domain', label: 'Retail', description: '', classification: 'internal' },
    { id: 'entity.account', type: 'Entity', profile_kind: 'entity', label: 'Account', description: '', classification: 'internal' },
    { id: 'entity.customer', type: 'Entity', profile_kind: 'entity', label: 'Customer', description: '', classification: 'internal' },
    { id: 'table.accounts', type: 'Table', profile_kind: 'physical_table', label: 'Accounts', description: '', classification: 'internal' },
  ],
  edges: [
    { id: 'e1', source: 'entity.account', target: 'domain.retail', type: 'domain_membership', label: 'belongs to' },
    { id: 'e2', source: 'entity.customer', target: 'domain.retail', type: 'domain_membership', label: 'belongs to' },
    { id: 'e3', source: 'entity.account', target: 'table.accounts', type: 'entity_mapping', label: 'maps to' },
  ],
}

describe('useGraphExplorer', () => {
  it('starts with no expansion, focus, or path', () => {
    const { result } = renderHook(() => useGraphExplorer(graph))
    expect(result.current.expandedIds.size).toBe(0)
    expect(result.current.focusMode).toBe(false)
    expect(result.current.pathIds).toBeNull()
  })

  it('expand reveals the 1-hop neighborhood and collapse removes it', () => {
    const { result } = renderHook(() => useGraphExplorer(graph))
    act(() => result.current.expand('entity.account'))
    expect(result.current.expandedIds).toEqual(new Set(['entity.account', 'domain.retail', 'table.accounts']))

    act(() => result.current.collapse('entity.account'))
    expect(result.current.expandedIds.size).toBe(0)
  })

  it('findPath resolves the shortest node route between two connected nodes', () => {
    const { result } = renderHook(() => useGraphExplorer(graph))
    act(() => result.current.findPath('entity.account', 'entity.customer'))
    expect(result.current.pathIds).toEqual(['entity.account', 'domain.retail', 'entity.customer'])
  })

  it('findPath returns null for an unreachable or unknown node', () => {
    const { result } = renderHook(() => useGraphExplorer(graph))
    act(() => result.current.findPath('entity.account', 'missing.id'))
    expect(result.current.pathIds).toBeNull()
  })

  it('findPath from a node to itself resolves to a single-node path', () => {
    const { result } = renderHook(() => useGraphExplorer(graph))
    act(() => result.current.findPath('entity.account', 'entity.account'))
    expect(result.current.pathIds).toEqual(['entity.account'])
  })

  it('clearPath resets the resolved path', () => {
    const { result } = renderHook(() => useGraphExplorer(graph))
    act(() => result.current.findPath('entity.account', 'entity.customer'))
    expect(result.current.pathIds).not.toBeNull()
    act(() => result.current.clearPath())
    expect(result.current.pathIds).toBeNull()
  })

  it('resets expansion, focus, and path when the graph version changes', () => {
    const { result, rerender } = renderHook(({ g }: { g: GraphResponse | null }) => useGraphExplorer(g), {
      initialProps: { g: graph },
    })
    act(() => {
      result.current.expand('entity.account')
      result.current.setFocusMode(true)
      result.current.findPath('entity.account', 'entity.customer')
    })
    expect(result.current.expandedIds.size).toBeGreaterThan(0)
    expect(result.current.focusMode).toBe(true)
    expect(result.current.pathIds).not.toBeNull()

    rerender({ g: { ...graph, version: '0.2.0' } })
    expect(result.current.expandedIds.size).toBe(0)
    expect(result.current.focusMode).toBe(false)
    expect(result.current.pathIds).toBeNull()
  })
})
