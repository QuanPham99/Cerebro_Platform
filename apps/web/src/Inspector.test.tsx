import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { Inspector } from './Inspector'
import type { ProfileKind, SemanticObject } from './types'

function object(profile_kind: ProfileKind, cerebro: Record<string, unknown>, extra: Partial<SemanticObject> = {}): SemanticObject {
  return {
    id: `${profile_kind}.fixture`,
    type: profile_kind === 'physical_table' ? 'Table' : profile_kind,
    profile_kind,
    label: `${profile_kind} fixture`,
    name: `${profile_kind} fixture`,
    status: 'active',
    description: `A ${profile_kind} fixture.`,
    classification: 'internal',
    aliases: [],
    tags: [],
    links: [],
    provenance: { origin: 'human_reviewed' },
    cerebro,
    body: '',
    path: `${profile_kind}s/fixture.md`,
    ...extra,
  }
}

afterEach(cleanup)

describe('Inspector', () => {
  it('renders empty guidance', () => {
    render(<Inspector object={null} loading={false} />)
    expect(screen.getByText('Select a semantic object')).toBeInTheDocument()
  })

  it('renders entity bindings and common governance metadata with active normalized to stable', () => {
    render(<Inspector object={object('entity', {
      classification: 'restricted',
      physical_mapping: { table: 'table.customers', key: ['customer_id'] },
      grain: { type: 'entity', description: 'One customer', key: ['customer_id'] },
    }, {
      aliases: ['Client'],
      sources: [{ id: 'semantic-definition', resource: 'docs/semantic-layer-definition.md' }],
      generated: { model: 'fixture-model' },
      verified: [{ reviewer: 'Data Owner' }],
    })} loading={false} />)
    expect(screen.getByText('table.customers')).toBeInTheDocument()
    expect(screen.getAllByText('customer_id').length).toBeGreaterThan(0)
    expect(screen.getByText('One customer')).toBeInTheDocument()
    expect(screen.getByText('Client')).toBeInTheDocument()
    expect(screen.getByText('stable')).toBeInTheDocument()
    expect(screen.getByText(/semantic-definition/)).toBeInTheDocument()
    expect(screen.getByText(/fixture-model/)).toBeInTheDocument()
    expect(screen.getByText(/Data Owner/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open OKF source/ })).toHaveAttribute('href', '/knowledge/entitys/fixture.md')
  })

  it('renders the dimension owner, bindings, semantic type, and compatible metrics', () => {
    render(<Inspector object={object('dimension', {
      entity: 'entity.customer', semantic_type: 'categorical',
      physical_mappings: [{ table: 'table.customers', column: 'gender' }],
      compatible_metrics: ['metric.customer-count'],
    })} loading={false} sourceBase={null} />)
    expect(screen.getByText('entity.customer')).toBeInTheDocument()
    expect(screen.getByText('table.customers.gender')).toBeInTheDocument()
    expect(screen.getByText('categorical')).toBeInTheDocument()
    expect(screen.getByText('metric.customer-count')).toBeInTheDocument()
  })

  it('renders metric structure, fields, filters, grain, dimensions, time anchor, dependencies, and warnings', () => {
    render(<Inspector object={object('metric', {
      entity: 'entity.transaction',
      measure: { kind: 'aggregate', aggregation: 'sum', source: { table: 'table.transactions', column: 'amount' } },
      filters: ['status = posted'], grain: { type: 'aggregate', description: 'Requested dimensions' },
      compatible_dimensions: ['dimension.transaction-channel'], time_dimension: 'dimension.transaction-date',
      relative_time_anchor: 'max_available_date', dependencies: ['table.transactions'], warnings: ['Use posted rows.'],
    })} loading={false} sourceBase={null} />)
    expect(screen.getByText('entity.transaction')).toBeInTheDocument()
    expect(screen.getByText('table.transactions.amount')).toBeInTheDocument()
    expect(screen.getByText('status = posted')).toBeInTheDocument()
    expect(screen.getByText('Requested dimensions')).toBeInTheDocument()
    expect(screen.getByText('dimension.transaction-channel')).toBeInTheDocument()
    expect(screen.getByText('max_available_date')).toBeInTheDocument()
    expect(screen.getByText('table.transactions')).toBeInTheDocument()
    expect(screen.getByText('Use posted rows.')).toBeInTheDocument()
  })

  it('renders business-rule output, grain, dependencies, constraints, and classification', () => {
    render(<Inspector object={object('business_rule', {
      entity: 'entity.customer', rule_kind: 'classification', output_type: 'boolean',
      grain: { type: 'entity', description: 'One customer' }, dependencies: ['entity.transaction'],
      logic: 'Aggregate activity before joining.', classification: 'restricted',
    })} loading={false} sourceBase={null} />)
    expect(screen.getByText('boolean')).toBeInTheDocument()
    expect(screen.getByText('One customer')).toBeInTheDocument()
    expect(screen.getByText('entity.transaction')).toBeInTheDocument()
    expect(screen.getByText('Aggregate activity before joining.')).toBeInTheDocument()
    expect(screen.getAllByText('restricted').length).toBeGreaterThan(0)
  })

  it('renders semantic and physical relationship endpoints plus validation and evidence', () => {
    render(<Inspector object={object('relationship', {
      semantic: { from: 'entity.account', to: 'entity.customer' },
      physical: { source: { table: 'table.accounts', column: 'customer_id' }, target: { table: 'table.customers', column: 'customer_id' } },
      cardinality: 'many-to-one', join_type: { default: 'left' },
      validation: { target_unique: 'not_checked', source_fk_coverage: 'not_checked', fanout: 'not_checked' },
      confidence: 0.9, evidence: ['accounts.customer_id'],
    })} loading={false} sourceBase={null} />)
    expect(screen.getByText('entity.account')).toBeInTheDocument()
    expect(screen.getByText('entity.customer')).toBeInTheDocument()
    expect(screen.getByText(/table.accounts/)).toBeInTheDocument()
    expect(screen.getByText('many-to-one')).toBeInTheDocument()
    expect(screen.getByText('left')).toBeInTheDocument()
    expect(screen.getAllByText('not_checked')).toHaveLength(3)
    expect(screen.getByText('0.9')).toBeInTheDocument()
    expect(screen.getByText('accounts.customer_id')).toBeInTheDocument()
  })

  it('renders physical-table schema, keys, fields, and field classifications', () => {
    render(<Inspector object={object('physical_table', {
      schema: 'main', grain: 'One customer', primary_key: 'customer_id', classification: 'restricted',
      columns: [{ name: 'email', data_type: 'VARCHAR', classification: 'restricted' }],
    })} loading={false} sourceBase={null} />)
    expect(screen.getByText('main')).toBeInTheDocument()
    expect(screen.getByText('customer_id')).toBeInTheDocument()
    expect(screen.getByText('email')).toBeInTheDocument()
    expect(screen.getByText('VARCHAR')).toBeInTheDocument()
    expect(screen.queryByLabelText('Search columns')).not.toBeInTheDocument()
  })

  it('offers column search once a table has enough fields for it to matter, and narrows the list', () => {
    const columns = Array.from({ length: 12 }, (_, index) => ({ name: `field_${index}`, data_type: 'VARCHAR', classification: 'internal' }))
    render(<Inspector object={object('physical_table', {
      schema: 'main', grain: 'One row', primary_key: 'key_column', classification: 'internal', columns,
    })} loading={false} sourceBase={null} />)
    const fieldList = screen.getByText('12').closest('section') as HTMLElement
    expect(screen.getByText('12')).toBeInTheDocument()
    columns.forEach((column) => expect(within(fieldList).getByText(column.name)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Search columns'), { target: { value: 'field_3' } })
    expect(within(fieldList).getByText('field_3')).toBeInTheDocument()
    expect(within(fieldList).queryByText('field_0')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Search columns'), { target: { value: 'nope' } })
    expect(within(fieldList).getByText('No columns match "nope".')).toBeInTheDocument()
  })

  it('renders policy rule, scope, confidence, evidence, warnings, and provenance', () => {
    render(<Inspector object={object('policy', {
      classification: 'restricted', applies_to: ['table.customers'], rule: 'Return aggregate results.',
      confidence: 0.9, evidence: ['customers.email'], warnings: ['Requires human review.'],
    }, { provenance: { origin: 'ai_proposed' } })} loading={false} sourceBase={null} />)
    expect(screen.getByText('Return aggregate results.')).toBeInTheDocument()
    expect(screen.getByText('table.customers')).toBeInTheDocument()
    expect(screen.getByText('customers.email')).toBeInTheDocument()
    expect(screen.getByText('Requires human review.')).toBeInTheDocument()
    expect(screen.getByText('ai_proposed')).toBeInTheDocument()
  })
})
