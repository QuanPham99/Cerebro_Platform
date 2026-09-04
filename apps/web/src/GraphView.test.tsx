import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { GraphLegend, physicalRelationshipRule, semanticRelationshipRules } from './GraphView'
import { Inspector } from './Inspector'

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

describe('semantic relationship notation', () => {
  it.each([
    ['semantic_mapping', '#A78BFA', 'solid'],
    ['metric_dependency', '#F2B56B', 'dashed'],
    ['policy_coverage', '#F17B91', 'dotted'],
  ])('renders %s as a directional typed edge', (type, color, lineStyle) => {
    const rule = semanticRelationshipRules.find((item) => item.selector === `edge[type = "${type}"]`)
    const style = rule?.style as Record<string, unknown> | undefined
    expect(rule).toBeDefined()
    expect(style?.['target-arrow-shape']).toBe('triangle')
    expect(style?.['line-color']).toBe(color)
    expect(style?.['target-arrow-color']).toBe(color)
    expect(style?.['line-style']).toBe(lineStyle)
    expect(style?.['arrow-scale']).toBe(1)
  })

  it('keeps relationship membership arrowless and explains each direction', () => {
    const endpoint = semanticRelationshipRules.find((item) => item.selector === 'edge[type = "relationship_endpoint"]')
    expect(endpoint?.style['target-arrow-shape']).toBe('none')
    render(<GraphLegend />)
    expect(screen.getByText('concept → table')).toBeInTheDocument()
    expect(screen.getByText('metric → table')).toBeInTheDocument()
    expect(screen.getByText('policy → table')).toBeInTheDocument()
  })

  it('shows policy targets, rule, confidence, evidence, and AI provenance', () => {
    render(<Inspector object={{
      id: 'policy.customer-data-handling', type: 'policy', label: 'Customer data handling',
      name: 'Customer data handling', description: 'Protect customer identity fields.', classification: 'restricted',
      status: 'active', aliases: [], tags: [], links: ['table.customers'],
      provenance: { origin: 'ai_proposed' }, body: '', path: 'policies/customer-data-handling.md',
      cerebro: {
        classification: 'restricted', applies_to: ['table.customers'], rule: 'Return aggregate results.',
        confidence: 0.9, evidence: ['customers.email'], warnings: ['Requires human review.'],
      },
    }} loading={false} sourceBase={null} />)
    expect(screen.getByText('Return aggregate results.')).toBeInTheDocument()
    expect(screen.getByText('table.customers')).toBeInTheDocument()
    expect(screen.getByText('customers.email')).toBeInTheDocument()
    expect(screen.getByText('ai_proposed')).toBeInTheDocument()
  })
})
