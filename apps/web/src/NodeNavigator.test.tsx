import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NodeNavigator } from './NodeNavigator'
import type { GraphNode } from './types'

const nodes: GraphNode[] = [
  { id: 'table.accounts', type: 'table', label: 'Accounts', description: '', classification: 'confidential' },
  { id: 'table.branches', type: 'table', label: 'Branches', description: '', classification: 'internal' },
  { id: 'concept.active-customer', type: 'concept', label: 'Active customer', description: '', classification: 'internal' },
]

afterEach(cleanup)

describe('NodeNavigator', () => {
  it('groups visible objects under titled, counted type dropdowns', () => {
    render(<NodeNavigator nodes={nodes} visibleIds={new Set(nodes.map((node) => node.id))} selectedId={null} onSelect={() => {}} />)

    const tables = screen.getByRole('list', { name: 'Tables objects' })
    const concepts = screen.getByRole('list', { name: 'Concepts objects' })
    const tableSummary = screen.getByText('Tables').closest('summary')

    expect(tableSummary).not.toBeNull()
    expect(within(tableSummary!).getByText('2')).toBeInTheDocument()
    expect(tableSummary!.querySelector('.type-symbol.table')).toBeInTheDocument()
    expect(within(tables).getByText('Accounts')).toBeInTheDocument()
    expect(within(tables).getByText('Branches')).toBeInTheDocument()
    expect(within(tables).queryByText('Active customer')).not.toBeInTheDocument()
    expect(within(concepts).getByText('Active customer')).toBeInTheDocument()
    expect(screen.queryByText('Datasets')).not.toBeInTheDocument()
  })

  it('preserves selection and the no-results state', () => {
    const onSelect = vi.fn()
    const { rerender } = render(<NodeNavigator nodes={nodes} visibleIds={new Set(['table.accounts'])} selectedId={null} onSelect={onSelect} />)

    fireEvent.click(screen.getByRole('button', { name: 'Accounts' }))
    expect(onSelect).toHaveBeenCalledWith('table.accounts')

    rerender(<NodeNavigator nodes={nodes} visibleIds={new Set()} selectedId={null} onSelect={onSelect} />)
    expect(screen.getByText('No objects match this view.')).toBeInTheDocument()
    expect(screen.queryByText('Tables')).not.toBeInTheDocument()
  })
})
