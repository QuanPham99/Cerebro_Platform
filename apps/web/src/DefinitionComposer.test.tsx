import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DefinitionComposer } from './DefinitionComposer'
import type { RuntimeStatus } from './types'

const mocks = vi.hoisted(() => ({
  getDefinitionContext: vi.fn().mockResolvedValue({
    bundle_id: 'golden', version: '0.2.0',
    entities: [{ id: 'entity.customer', name: 'Customer' }],
    dimensions: [{ id: 'dimension.gender', name: 'Gender', entity: 'entity.customer', semantic_type: 'categorical' }],
    tables: [{ id: 'table.customers', name: 'Customers', columns: [{ name: 'customer_id', data_type: 'INTEGER' }] }],
    metrics: [], business_rules: [],
  }),
  createDefinitionRevision: vi.fn().mockResolvedValue({
    id: 'definition-1', base_bundle_id: 'golden', base_version: '0.2.0', version: '0.2.0+rev.definition-1', counts: { metric: 1 },
    definitions: [{ id: 'metric.customer-count', name: 'Customer count', kind: 'metric' }],
    generation_mode: 'authored', review_state: 'candidate', review_record: null,
  }),
  translateDefinition: vi.fn().mockResolvedValue({
    payload: {
      kind: 'metric',
      definition: {
        id: 'metric.customer-count', name: 'Customer count', description: 'Distinct customers.',
        entity: 'entity.customer', classification: 'internal', warnings: ['Confirm customer identity semantics.'],
        measure: { kind: 'aggregate', aggregation: 'count_distinct', source: { table: 'table.customers', column: 'customer_id' }, predicates: [] },
        dependencies: ['table.customers'], grain: { type: 'aggregate', description: 'Requested dimensions', key: [] },
        compatible_dimensions: [], time_dimension: null, relative_time_anchor: null,
      },
    }, warnings: ['Confirm customer identity semantics.'], provider: 'test', model: 'model',
  }),
  reviewDefinitionRevision: vi.fn().mockResolvedValue({
    run_id: 'definition-1', decision: 'reject', reviewer: 'Data Owner', comment: 'Needs a clearer grain.',
    acknowledge_ai_risk: false, reviewed_at: '2026-09-09T00:00:00Z', candidate_digest: 'digest',
    reviewed_bundle: null, reviewed_digest: null,
  }),
}))

vi.mock('./api', () => ({
  getDefinitionContext: mocks.getDefinitionContext,
  createDefinitionRevision: mocks.createDefinitionRevision,
  addDefinition: vi.fn(),
  translateDefinition: mocks.translateDefinition,
  reviewDefinitionRevision: mocks.reviewDefinitionRevision,
  activateDefinitionRevision: vi.fn(),
}))

const runtime = {
  llm_configured: true, provider_id: 'test', provider_name: 'Test', model: 'model', base_url: '', response_mode: 'json_schema',
  llm_timeout_seconds: 30, llm_max_output_tokens: 1024, api_key_configured: true, embedding_model: null,
  database_configured: true, database_reachable: true, database_schema: 'main', query_row_limit: 100,
  query_timeout_seconds: 10, bundle: 'bank-workshop', semantic_version: '0.2.0', generation_mode: 'reviewed',
  review_state: 'approved', chat_ready: true,
} satisfies RuntimeStatus

const baseVersion = {
  id: 'golden', name: 'bank-workshop', version: '0.2.0', origin: 'golden', is_default: true,
  review_state: 'approved', reviewer: null, reviewed_at: null, parent_version: null, counts: {}, kind_counts: {},
  generation_mode: 'fallback', source_mode: 'configured', provider: null, model: null,
} as const

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('DefinitionComposer', () => {
  it('creates a user-authored metric revision from the guided form', async () => {
    const onRevision = vi.fn()
    const onGraphChange = vi.fn()
    render(<DefinitionComposer runtime={runtime} baseVersion={baseVersion} revision={null} onRevision={onRevision} onGraphChange={onGraphChange} onReviewed={vi.fn()} />)

    const manual = screen.getByRole('button', { name: /Use guided form/i })
    await waitFor(() => expect(manual).toBeEnabled())
    fireEvent.click(manual)
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Customer count' } })
    fireEvent.click(screen.getByRole('button', { name: /Add to graph/i }))

    await waitFor(() => expect(mocks.createDefinitionRevision).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'metric', definition: expect.objectContaining({ id: 'metric.customer-count', entity: 'entity.customer' }) }),
      'declared',
      'golden',
    ))
    expect(onRevision).toHaveBeenCalledWith(expect.objectContaining({ id: 'definition-1' }))
    expect(onGraphChange).toHaveBeenCalledWith(expect.objectContaining({ id: 'definition-1' }), 'metric.customer-count')
  })

  it('uses guided aggregate and ratio controls without exposing measure JSON', async () => {
    render(<DefinitionComposer runtime={runtime} baseVersion={baseVersion} revision={null} onRevision={vi.fn()} onGraphChange={vi.fn()} onReviewed={vi.fn()} />)
    const manual = screen.getByRole('button', { name: /Use guided form/i })
    await waitFor(() => expect(manual).toBeEnabled())
    fireEvent.click(manual)
    expect(screen.queryByLabelText('Measure JSON')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Measure aggregation')).toHaveValue('count_distinct')
    fireEvent.click(screen.getByRole('button', { name: 'Ratio' }))
    expect(screen.getByLabelText('Numerator aggregation')).toBeInTheDocument()
    expect(screen.getByLabelText('Denominator source column')).toHaveValue('customer_id')
  })

  it('scopes LLM translation to the selected approved graph', async () => {
    render(<DefinitionComposer runtime={runtime} baseVersion={baseVersion} revision={null} onRevision={vi.fn()} onGraphChange={vi.fn()} onReviewed={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Definition idea'), { target: { value: 'Count distinct customers' } })
    fireEvent.click(screen.getByRole('button', { name: /Translate with LLM/i }))

    await waitFor(() => expect(mocks.translateDefinition).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'metric', intent: 'Count distinct customers', base_bundle_id: 'golden', entity_id: 'entity.customer',
    })))
    expect(screen.getByText('AI proposed')).toBeInTheDocument()
    expect(screen.getByLabelText('Warnings')).toHaveValue('Confirm customer identity semantics.')
  })

  it('supports guided business-rule dependencies and a recorded rejection', async () => {
    const onReviewed = vi.fn()
    const revision = {
      id: 'definition-1', base_bundle_id: 'golden', base_version: '0.2.0', version: '0.2.0+rev.definition-1',
      counts: { business_rule: 1 }, definitions: [{ id: 'rule.customer-active', name: 'Customer active', kind: 'business_rule' as const }],
      generation_mode: 'authored' as const, review_state: 'candidate' as const, review_record: null,
    }
    render(<DefinitionComposer runtime={runtime} baseVersion={baseVersion} revision={revision} onRevision={vi.fn()} onGraphChange={vi.fn()} onReviewed={onReviewed} />)
    fireEvent.click(screen.getByRole('button', { name: 'Business rule' }))
    const manual = screen.getByRole('button', { name: /Use guided form/i })
    await waitFor(() => expect(manual).toBeEnabled())
    fireEvent.click(manual)
    expect(screen.getByLabelText('Rule logic')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /Customers/i })).toBeChecked()

    fireEvent.change(screen.getByLabelText('Definition reviewer'), { target: { value: 'Data Owner' } })
    fireEvent.click(screen.getByRole('radio', { name: 'Reject' }))
    fireEvent.change(screen.getByLabelText('Definition review comment'), { target: { value: 'Needs a clearer grain.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record rejection' }))
    await waitFor(() => expect(mocks.reviewDefinitionRevision).toHaveBeenCalledWith('definition-1', expect.objectContaining({ decision: 'reject', reviewer: 'Data Owner' })))
    expect(onReviewed).toHaveBeenCalledWith('definition-1', 'reject')
  })
})
