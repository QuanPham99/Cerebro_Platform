import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const mocks = vi.hoisted(() => ({
  postChat: vi.fn().mockResolvedValue({
    conversation_id: 'conversation-1',
    status: 'answered',
    answer: 'Female: 2; Male: 1.',
    sql: 'SELECT gender, COUNT(*) AS total FROM customers GROUP BY gender LIMIT 100',
    columns: ['gender', 'total'],
    rows: [['Female', 2], ['Male', 1]],
    row_count: 2,
    truncated: false,
    semantic_version: '0.1.0',
    evidence_ids: ['table.customers'],
    warnings: [],
    trace: [{ agent: 'validation', status: 'completed', summary: 'Read-only checks passed' }],
  }),
  startGeneration: vi.fn().mockResolvedValue({
    id: 'run-1', status: 'running', created_at: '2026-09-03T00:00:00Z', updated_at: '2026-09-03T00:00:00Z',
    events: [], candidate: null, error: null, source_mode: 'database_only',
  }),
  getGenerationGraph: vi.fn().mockResolvedValue({ version: '0.1.0+candidate', nodes: [], edges: [] }),
  getGenerationConcept: vi.fn(),
  reviewGeneration: vi.fn().mockResolvedValue({ decision: 'approve', reviewer: 'Data Owner' }),
  activateGeneration: vi.fn().mockResolvedValue({ path: '/reviewed/run-1', name: 'bank', version: '0.1.0+candidate' }),
  getGeneration: vi.fn(),
}))

let eventSource: MockEventSource | null = null

class MockEventSource {
  listeners = new Map<string, Array<(event: Event) => void>>()
  constructor(public url: string) { eventSource = this }
  addEventListener(name: string, listener: (event: Event) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) || []), listener])
  }
  close() {}
  emit(name: string, data: unknown) {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>
    this.listeners.get(name)?.forEach((listener) => listener(event))
  }
}

vi.mock('./api', () => ({
  getGraph: vi.fn().mockResolvedValue({
    version: '0.1.0',
    nodes: [],
    edges: [],
  }),
  getBundle: vi.fn().mockResolvedValue({
    name: 'Bank workshop',
    version: '0.1.0',
    counts: {},
    generation_mode: 'reviewed',
  }),
  getConcept: vi.fn(),
  getRuntimeStatus: vi.fn().mockResolvedValue({
    llm_configured: true,
    provider_id: 'greennode-glm',
    provider_name: 'GreenNode',
    model: 'test-model',
    base_url: 'http://example.test/v1',
    response_mode: 'json_schema',
    llm_timeout_seconds: 120,
    llm_max_output_tokens: 8192,
    api_key_configured: true,
    embedding_model: null,
    database_configured: true,
    database_reachable: true,
    database_schema: 'main',
    query_row_limit: 100,
    query_timeout_seconds: 10,
    bundle: 'Bank workshop',
    semantic_version: '0.1.0',
    generation_mode: 'reviewed',
    review_state: 'active',
    chat_ready: true,
  }),
  startGeneration: mocks.startGeneration,
  getGeneration: mocks.getGeneration,
  getGenerationGraph: mocks.getGenerationGraph,
  getGenerationConcept: mocks.getGenerationConcept,
  generationEventsUrl: (runId: string) => `/api/generation/runs/${runId}/events`,
  reviewGeneration: mocks.reviewGeneration,
  activateGeneration: mocks.activateGeneration,
  postChat: mocks.postChat,
}))

vi.mock('./GraphView', async () => {
  const React = await import('react')
  return {
    GraphView: React.forwardRef(({ graph }: { graph: { version: string } }, _ref) => <div data-testid="graph-view">{graph.version}</div>),
    GraphLegend: () => <div>Graph legend</div>,
  }
})

beforeEach(() => {
  eventSource = null
  vi.stubGlobal('EventSource', MockEventSource)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('workspace navigation', () => {
  it('switches workspaces and collapses and expands the left rail', async () => {
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: /Cerebro Semantic constellation/i }))
    expect(screen.getByRole('menu', { name: 'Switch workspace' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('menuitemradio', { name: /Text to SQL agents/i }))
    expect(screen.getByRole('heading', { name: /Turn governed meaning into trusted queries/i })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Agent setup steps' })).toBeInTheDocument()
    expect(screen.getByText('GreenNode · test-model')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expect(screen.queryByRole('navigation', { name: 'Agent setup steps' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    expect(screen.getByRole('navigation', { name: 'Agent setup steps' })).toBeInTheDocument()
  })

  it('submits a governed database question and renders SQL, results, and evidence', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /Cerebro Semantic constellation/i }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Text to SQL agents/i }))

    fireEvent.change(screen.getByRole('textbox', { name: 'Ask about the database' }), {
      target: { value: 'How many customers are there by gender?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send question' }))

    expect(await screen.findByText('Female: 2; Male: 1.')).toBeInTheDocument()
    expect(screen.getByText('Female')).toBeInTheDocument()
    expect(screen.getByText('table.customers')).toBeInTheDocument()
    expect(mocks.postChat).toHaveBeenCalledTimes(1)
  })

  it('streams generation, records approval, activates separately, and previews the candidate', async () => {
    render(<App />)

    fireEvent.click(await screen.findByTitle('Open candidate builder'))
    expect(screen.getByRole('heading', { name: /Build meaning/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate candidate' })).toBeDisabled()
    fireEvent.click(screen.getByRole('radio', { name: /Database only/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Generate candidate' }))
    await waitFor(() => expect(mocks.startGeneration).toHaveBeenCalledWith('database_only'))
    await waitFor(() => expect(eventSource).not.toBeNull())

    const progress = {
      sequence: 1, stage: 'catalog_scan', status: 'completed',
      summary: 'Discovered 10 tables, 75 columns, and 11 declared relationships.',
      command: 'cerebro scan', timestamp: '2026-09-03T00:00:01Z',
      details: { tables: 10, columns: 75, declared_relationships: 11, row_sampling: 'disabled', rows_read: 0, config_loaded: false },
    }
    act(() => eventSource?.emit('progress', progress))
    expect((await screen.findAllByText(progress.summary)).length).toBeGreaterThan(1)
    const showDetails = screen.getByRole('button', { name: 'Show detailed generation process' })
    expect(showDetails).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(showDetails)
    const executionLog = screen.getByRole('region', { name: 'Detailed generation process' })
    expect(within(executionLog).getByText('Scan catalog')).toBeInTheDocument()
    expect(executionLog).toHaveTextContent(/tables\s*10/)
    expect(executionLog).toHaveTextContent('rows read')
    expect(executionLog).toHaveTextContent('Waiting for the previous step to finish.')
    fireEvent.click(screen.getByRole('button', { name: 'Hide detailed generation process' }))
    expect(screen.queryByRole('region', { name: 'Detailed generation process' })).not.toBeInTheDocument()

    const completed = {
      id: 'run-1', status: 'succeeded', created_at: '2026-09-03T00:00:00Z', updated_at: '2026-09-03T00:00:02Z',
      events: [progress], error: null, source_mode: 'database_only',
      candidate: {
        name: 'bank-workshop', version: '0.1.0+candidate', counts: { table: 10, relationship: 11 },
        generation_mode: 'live', provider: 'greennode-glm', model: 'test-model', source_mode: 'database_only',
        discovery_evidence: { config_loaded: false, rows_read: 0, web_enrichment: 'disabled' },
        review_state: 'candidate', review_record: null,
      },
    }
    act(() => eventSource?.emit('complete', completed))
    const approved = {
      ...completed,
      candidate: {
        ...completed.candidate,
        review_state: 'approved',
        review_record: { reviewer: 'Data Owner', comment: '', decision: 'approve' },
      },
    }
    mocks.getGeneration.mockResolvedValue(approved)
    fireEvent.change(await screen.findByRole('textbox', { name: 'Reviewer name' }), { target: { value: 'Data Owner' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /acknowledge/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Record approve' }))
    await waitFor(() => expect(mocks.reviewGeneration).toHaveBeenCalledWith('run-1', expect.objectContaining({ reviewer: 'Data Owner', acknowledge_ai_risk: true })))
    fireEvent.click(await screen.findByRole('button', { name: 'Activate reviewed bundle' }))
    await waitFor(() => expect(mocks.activateGeneration).toHaveBeenCalledWith('run-1'))
    fireEvent.click(await screen.findByRole('button', { name: 'Preview candidate graph' }))

    expect(await screen.findByText('Candidate preview')).toBeInTheDocument()
    expect(screen.getByTestId('graph-view')).toHaveTextContent('0.1.0+candidate')
    expect(mocks.getGenerationGraph).toHaveBeenCalledWith('run-1')
    expect(screen.getAllByRole('button', { name: /Return to active graph/i }).length).toBe(2)
  })
})
