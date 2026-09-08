import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { EDGE_PRESENTATION, GraphLegend, physicalRelationshipRule, semanticRelationshipRules } from './GraphView'
import { PROFILE_KINDS, PROFILE_PRESENTATION } from './profilePresentation'
import type { GraphEdgeType } from './types'

afterEach(cleanup)

describe('profile presentation', () => {
  it('defines every profile kind with its canonical layer, color, and shape', () => {
    expect(PROFILE_KINDS).toEqual([
      'dataset', 'physical_table', 'entity', 'dimension', 'business_rule', 'metric',
      'relationship', 'policy', 'legacy_concept', 'generic',
    ])
    expect(PROFILE_PRESENTATION).toMatchObject({
      dataset: { layer: 'Physical', color: '#58C7D9', shape: 'round-rectangle' },
      physical_table: { layer: 'Physical', color: '#3EA6B8', shape: 'rectangle' },
      entity: { layer: 'Semantic', color: '#A78BFA', shape: 'ellipse' },
      dimension: { layer: 'Semantic', color: '#60A5FA', shape: 'barrel' },
      business_rule: { layer: 'Semantic', color: '#5CCB8A', shape: 'octagon' },
      metric: { layer: 'Metrics', color: '#F2B56B', shape: 'hexagon' },
      relationship: { layer: 'Semantic', color: '#6E7A90', shape: 'diamond' },
      policy: { layer: 'Governance', color: '#F17B91', shape: 'tag' },
      legacy_concept: { layer: 'Semantic', color: '#8B79C6', shape: 'ellipse' },
      generic: { layer: 'Other', color: '#8993A8', shape: 'ellipse' },
    })
  })
})

describe('physical relationship notation', () => {
  it('maps every cardinality to persistent endpoints and points to the target', () => {
    const style = physicalRelationshipRule.style as Record<string, unknown>
    const sourceLabel = style['source-label'] as (edge: { data: (key: string) => string }) => string
    const targetLabel = style['target-label'] as (edge: { data: (key: string) => string }) => string
    const labelsFor = (cardinality: string) => {
      const edge = { data: (key: string) => key === 'label' ? cardinality : '' }
      return [sourceLabel(edge), targetLabel(edge)]
    }

    expect(labelsFor('many-to-one')).toEqual(['many', 'one'])
    expect(labelsFor('one-to-many')).toEqual(['one', 'many'])
    expect(labelsFor('one-to-one')).toEqual(['one', 'one'])
    expect(labelsFor('many-to-many')).toEqual(['many', 'many'])
    expect(style['line-color']).toBe('#3EA6B8')
    expect(style['target-arrow-shape']).toBe('triangle')
  })
})

describe('semantic edge grammar', () => {
  const expected: Array<[Exclude<GraphEdgeType, 'physical_fk'>, string, string, string]> = [
    ['semantic_mapping', '#A78BFA', 'solid', 'triangle'],
    ['entity_mapping', '#A78BFA', 'solid', 'triangle'],
    ['dimension_entity', '#60A5FA', 'solid', 'triangle'],
    ['dimension_binding', '#60A5FA', 'dotted', 'triangle'],
    ['metric_entity', '#F2B56B', 'solid', 'triangle'],
    ['metric_dimension', '#F2B56B', 'dashed', 'triangle'],
    ['metric_dependency', '#F2B56B', 'dotted', 'triangle'],
    ['rule_entity', '#5CCB8A', 'solid', 'triangle'],
    ['rule_dependency', '#5CCB8A', 'dashed', 'triangle'],
    ['semantic_relationship', '#6E7A90', 'solid', 'triangle'],
    ['policy_coverage', '#F17B91', 'dotted', 'triangle'],
    ['relationship_endpoint', '#66728A', 'solid', 'none'],
  ]

  it.each(expected)('renders %s with explicit color, line, and direction', (type, color, lineStyle, arrow) => {
    expect(EDGE_PRESENTATION[type]).toMatchObject({ color, lineStyle, arrow })
    const rule = semanticRelationshipRules.find((item) => item.selector === `edge[type = "${type}"]`)
    expect(rule?.style['line-color']).toBe(color)
    expect(rule?.style['target-arrow-color']).toBe(color)
    expect(rule?.style['line-style']).toBe(lineStyle)
    expect(rule?.style['target-arrow-shape']).toBe(arrow)
  })

  it('keeps semantic relationship cardinality visible and membership arrowless', () => {
    const relationship = semanticRelationshipRules.find((item) => item.selector === 'edge[type = "semantic_relationship"]')
    const membership = semanticRelationshipRules.find((item) => item.selector === 'edge[type = "relationship_endpoint"]')
    expect(relationship?.style.label).toBe('data(label)')
    expect(membership?.style['target-arrow-shape']).toBe('none')
  })

  it('explains physical, semantic, metric, dimension, rule, policy, and audit directions', async () => {
    render(<GraphLegend />)
    const toggle = screen.getByRole('button', { name: 'Collapse graph legend' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByLabelText('Table join cardinality example: many to one')).toHaveTextContent('many → one')
    expect(screen.getByText('physical join')).toBeInTheDocument()
    expect(screen.getByText('entity → table')).toBeInTheDocument()
    expect(screen.getByText('entity → entity')).toBeInTheDocument()
    expect(screen.getByText('dimension → entity/table')).toBeInTheDocument()
    expect(screen.getByText('metric → entity/dimension/table')).toBeInTheDocument()
    expect(screen.getByText('rule → semantic object')).toBeInTheDocument()
    expect(screen.getByText('policy → object')).toBeInTheDocument()
    expect(screen.getByText('relationship audit membership')).toBeInTheDocument()

    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: 'Expand graph legend' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('physical join')).not.toBeVisible()
  })
})
