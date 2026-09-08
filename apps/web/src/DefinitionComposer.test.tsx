import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DefinitionComposer } from './DefinitionComposer'
import type { RuntimeStatus } from './types'

const mocks = vi.hoisted(() => ({
  getDefinitionContext: vi.fn().mockResolvedValue({
    version: '0.2.0',
    entities: [{ id: 'entity.customer', name: 'Customer' }],
    dimensions: [{ id: 'dimension.gender', name: 'Gender', entity: 'entity.customer' }],
    tables: [{ id: 'table.customers', name: 'Customers', columns: [{ name: 'customer_id', data_type: 'INTEGER' }] }],
  }),
  createDefinitionRevision: vi.fn().mockResolvedValue({
    id: 'definition-1', base_version: '0.2.0', version: '0.2.0+rev.definition-1', counts: { metric: 1 },
    generation_mode: 'authored', review_state: 'candidate', review_record: null,
  }),
}))

vi.mock('./api', () => ({
  getDefinitionContext: mocks.getDefinitionContext,
  createDefinitionRevision: mocks.createDefinitionRevision,
  addDefinition: vi.fn(),
  translateDefinition: vi.fn(),
  reviewDefinitionRevision: vi.fn(),
  activateDefinitionRevision: vi.fn(),
}))

const runtime = {
  llm_configured: true, provider_id: 'test', provider_name: 'Test', model: 'model', base_url: '', response_mode: 'json_schema',
  llm_timeout_seconds: 30, llm_max_output_tokens: 1024, api_key_configured: true, embedding_model: null,
  database_configured: true, database_reachable: true, database_schema: 'main', query_row_limit: 100,
  query_timeout_seconds: 10, bundle: 'bank-workshop', semantic_version: '0.2.0', generation_mode: 'reviewed',
  review_state: 'approved', chat_ready: true,
} satisfies RuntimeStatus

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('DefinitionComposer', () => {
  it('creates a user-authored metric revision from the compact form', async () => {
    const onRevision = vi.fn()
    const onGraphChange = vi.fn()
    render(<DefinitionComposer runtime={runtime} revision={null} onRevision={onRevision} onGraphChange={onGraphChange} onActivated={vi.fn()} onInspect={vi.fn()} onBuild={vi.fn()} />)

    const manual = screen.getByRole('button', { name: /Use manual form/i })
    await waitFor(() => expect(manual).toBeEnabled())
    fireEvent.click(manual)
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Customer count' } })
    fireEvent.click(screen.getByRole('button', { name: /Add to graph/i }))

    await waitFor(() => expect(mocks.createDefinitionRevision).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'metric', definition: expect.objectContaining({ id: 'metric.customer-count', entity: 'entity.customer' }) }),
      'declared',
    ))
    expect(onRevision).toHaveBeenCalledWith(expect.objectContaining({ id: 'definition-1' }))
    expect(onGraphChange).toHaveBeenCalledWith(expect.objectContaining({ id: 'definition-1' }), 'metric.customer-count')
  })

  it('keeps invalid measure JSON editable and prevents saving it', async () => {
    render(<DefinitionComposer runtime={runtime} revision={null} onRevision={vi.fn()} onGraphChange={vi.fn()} onActivated={vi.fn()} onInspect={vi.fn()} onBuild={vi.fn()} />)
    const manual = screen.getByRole('button', { name: /Use manual form/i })
    await waitFor(() => expect(manual).toBeEnabled())
    fireEvent.click(manual)
    const measure = screen.getByLabelText('Measure JSON')
    fireEvent.change(measure, { target: { value: '{ invalid' } })
    expect(measure).toHaveValue('{ invalid')
    expect(screen.getByText('Measure must be valid JSON.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Add to graph/i })).toBeDisabled()
  })
})
