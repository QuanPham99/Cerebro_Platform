import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PROFILE_KINDS, PROFILE_PRESENTATION } from './profilePresentation'
import { NodeNavigator } from './NodeNavigator'
import type { GraphNode } from './types'

const nodes: GraphNode[] = PROFILE_KINDS.map((profile_kind) => ({
  id: `${profile_kind}.fixture`,
  type: profile_kind === 'physical_table' ? 'Table' : profile_kind,
  profile_kind,
  label: PROFILE_PRESENTATION[profile_kind].label,
  description: '',
  classification: 'internal',
}))

afterEach(cleanup)

describe('NodeNavigator', () => {
  it('groups every visible object by shared profile metadata', () => {
    render(<NodeNavigator nodes={nodes} visibleIds={new Set(nodes.map((node) => node.id))} selectedId={null} onSelect={() => {}} />)

    for (const kind of PROFILE_KINDS) {
      const presentation = PROFILE_PRESENTATION[kind]
      const list = screen.getByRole('list', { name: `${presentation.plural} objects` })
      const summary = screen.getByText(presentation.plural).closest('summary')
      expect(summary).not.toBeNull()
      expect(within(summary!).getByText('1')).toBeInTheDocument()
      expect(summary!.querySelector(`.type-symbol.${kind}`)).toBeInTheDocument()
      expect(within(list).getByText(presentation.label)).toBeInTheDocument()
    }
  })

  it('uses profile_kind instead of raw OKF type and preserves selection', () => {
    const onSelect = vi.fn()
    const table = nodes.find((node) => node.profile_kind === 'physical_table')!
    const { rerender } = render(<NodeNavigator nodes={nodes} visibleIds={new Set([table.id])} selectedId={null} onSelect={onSelect} />)

    fireEvent.click(screen.getByRole('button', { name: 'Physical table' }))
    expect(onSelect).toHaveBeenCalledWith('physical_table.fixture')
    expect(screen.getByText('Physical tables')).toBeInTheDocument()

    rerender(<NodeNavigator nodes={nodes} visibleIds={new Set()} selectedId={null} onSelect={onSelect} />)
    expect(screen.getByText('No objects match this view.')).toBeInTheDocument()
    expect(screen.queryByText('Physical tables')).not.toBeInTheDocument()
  })
})
