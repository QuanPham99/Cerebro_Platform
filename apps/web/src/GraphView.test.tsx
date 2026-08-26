import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { GraphLegend, physicalRelationshipRule } from './GraphView'

afterEach(cleanup)

describe('physical table relationship notation', () => {
  it('maps each cardinality to its endpoints and points to the target', () => {
    const style = physicalRelationshipRule.style as Record<string, unknown>
    const sourceLabel = style['source-label'] as (edge: { data: (key: string) => string }) => string
    const targetLabel = style['target-label'] as (edge: { data: (key: string) => string }) => string
    const labelsFor = (cardinality: string) => {
      const edge = { data: (key: string) => key === 'label' ? cardinality : '' }
      return [sourceLabel(edge), targetLabel(edge)]
    }

    expect(physicalRelationshipRule.selector).toBe('edge[type = "physical_fk"]')
    expect(sourceLabel).toBeTypeOf('function')
    expect(targetLabel).toBeTypeOf('function')
    expect(labelsFor('many-to-one')).toEqual(['many', 'one'])
    expect(labelsFor('one-to-many')).toEqual(['one', 'many'])
    expect(labelsFor('one-to-one')).toEqual(['one', 'one'])
    expect(labelsFor('many-to-many')).toEqual(['many', 'many'])
    expect(physicalRelationshipRule.style['target-arrow-shape']).toBe('triangle')
  })

  it('explains that the notation applies to table joins', () => {
    render(<GraphLegend />)

    expect(screen.getByLabelText('Table join cardinality example: many to one')).toHaveTextContent('many → one')
    expect(screen.getByText('table join')).toBeInTheDocument()
  })
})
