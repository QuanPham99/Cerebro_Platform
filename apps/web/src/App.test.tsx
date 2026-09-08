import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  getGenerationTrace: vi.fn().mockResolvedValue({ run_id: 'run-1', steps: [] }),
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
    nodes: [
      { id: 'dataset.bank', type: 'Dataset', profile_kind: 'dataset', label: 'Bank', description: 'Bank dataset', classification: 'internal' },
      { id: 'table.customers', type: 'Table', profile_kind: 'physical_table', label: 'Customers', description: 'Customer records', classification: 'restricted' },
      { id: 'entity.customer', type: 'Entity', profile_kind: 'entity', label: 'Customer', description: 'Customer meaning', classification: 'restricted' },
      { id: 'dimension.gender', type: 'Dimension', profile_kind: 'dimension', label: 'Gender', description: 'Gender dimension', classification: 'restricted' },
      { id: 'rule.active', type: 'Business Rule', profile_kind: 'business_rule', label: 'Active customer', description: 'Activity rule', classification: 'restricted' },
      { id: 'metric.count', type: 'Metric', profile_kind: 'metric', label: 'Customer count', description: 'Count metric', classification: 'restricted' },
      { id: 'relationship.account-customer', type: 'Relationship', profile_kind: 'relationship', label: 'Account customer', description: 'Join', classification: 'internal' },
      { id: 'policy.sensitive', type: 'Policy', profile_kind: 'policy', label: 'Sensitive data', description: 'Policy', classification: 'restricted' },
      { id: 'concept.customer', type: 'Concept', profile_kind: 'legacy_concept', label: 'Legacy customer', description: 'Legacy meaning', classification: 'internal' },
      { id: 'custom.note', type: 'Note', profile_kind: 'generic', label: 'Custom note', description: 'Other object', classification: 'internal' },
    ],
    edges: [],
  }),
  getGoldenGraph: vi.fn().mockResolvedValue({
    version: '0.2.0',
    nodes: [
      { id: 'dataset.bank', type: 'Dataset', profile_kind: 'dataset', label: 'Bank', description: 'Bank dataset', classification: 'internal' },
      { id: 'table.customers', type: 'Table', profile_kind: 'physical_table', label: 'Customers', description: 'Customer records', classification: 'restricted' },
      { id: 'entity.customer', type: 'Entity', profile_kind: 'entity', label: 'Customer', description: 'Customer meaning', classification: 'restricted' },
      { id: 'dimension.gender', type: 'Dimension', profile_kind: 'dimension', label: 'Gender', description: 'Gender dimension', classification: 'restricted' },
      { id: 'rule.active', type: 'Business Rule', profile_kind: 'business_rule', label: 'Active customer', description: 'Activity rule', classification: 'restricted' },
      { id: 'metric.count', type: 'Metric', profile_kind: 'metric', label: 'Customer count', description: 'Count metric', classification: 'restricted' },
      { id: 'relationship.account-customer', type: 'Relationship', profile_kind: 'relationship', label: 'Account customer', description: 'Join', classification: 'internal' },
      { id: 'policy.sensitive', type: 'Policy', profile_kind: 'policy', label: 'Sensitive data', description: 'Policy', classification: 'restricted' },
      { id: 'concept.customer', type: 'Concept', profile_kind: 'legacy_concept', label: 'Legacy customer', description: 'Legacy meaning', classification: 'internal' },
      { id: 'custom.note', type: 'Note', profile_kind: 'generic', label: 'Custom note', description: 'Other object', classification: 'internal' },
    ],
    edges: [],
  }),
  getBundle: vi.fn().mockResolvedValue({
    name: 'Bank workshop',
    version: '0.2.0',
    counts: {},
    generation_mode: 'reviewed',
  }),
  getGoldenBundle: vi.fn().mockResolvedValue({ name: 'bank-workshop', version: '0.2.0', counts: {}, generation_mode: 'fallback', review_state: 'approved' }),
  getGoldenObject: vi.fn(),
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
    review_state: 'approved',
    chat_ready: true,
  }),
  startGeneration: mocks.startGeneration,
  getGeneration: mocks.getGeneration,
  getGenerationTrace: mocks.getGenerationTrace,
  getGenerationGraph: mocks.getGenerationGraph,
  getGenerationConcept: mocks.getGenerationConcept,
  generationEventsUrl: (runId: string) => `/api/generation/runs/${runId}/events`,
  reviewGeneration: mocks.reviewGeneration,
  activateGeneration: mocks.activateGeneration,
  getDefinitionRevision: vi.fn(),
  getDefinitionGraph: vi.fn(),
  getDefinitionObject: vi.fn(),
  getDefinitionContext: vi.fn().mockResolvedValue({ version: '0.1.0', entities: [], dimensions: [], tables: [] }),
  translateDefinition: vi.fn(),
  createDefinitionRevision: vi.fn(),
  addDefinition: vi.fn(),
  reviewDefinitionRevision: vi.fn(),
  activateDefinitionRevision: vi.fn(),
  postChat: mocks.postChat,
}))

vi.mock('./GraphView', async () => {
  const React = await import('react')
  return {
    GraphView: React.forwardRef(({ graph, visibleIds }: { graph: { version: string }; visibleIds: Set<string> }, _ref) => <div data-testid="graph-view"><span>{graph.version}</span><span data-testid="visible-ids">{[...visibleIds].join(' ')}</span></div>),
    GraphLegend: () => <div aria-label="Graph edge legend">Graph legend</div>,
  }
})

beforeEach(() => {
  eventSource = null
  window.sessionStorage.clear()
  vi.stubGlobal('EventSource', MockEventSource)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('workspace navigation', () => {
  it('filters canonical profile kinds with presets, checkboxes, search, and reset', async () => {
    render(<App />)
    expect(await screen.findByTestId('visible-ids')).toHaveTextContent('custom.note')
    const legend = screen.getByLabelText('Graph edge legend')
    expect(legend.closest('.canvas-wrap')).not.toBeNull()
    expect(legend.closest('.discovery-rail')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Semantic' }))
    expect(screen.getByTestId('visible-ids')).toHaveTextContent('entity.customer')
    expect(screen.getByTestId('visible-ids')).toHaveTextContent('dimension.gender')
    expect(screen.getByTestId('visible-ids')).not.toHaveTextContent('metric.count')
    expect(screen.getByTestId('visible-ids')).not.toHaveTextContent('table.customers')

    fireEvent.click(screen.getByRole('checkbox', { name: /^Relationship/ }))
    expect(screen.getByTestId('visible-ids')).not.toHaveTextContent('relationship.account-customer')

    fireEvent.change(screen.getByRole('textbox', { name: 'Search semantic objects' }), { target: { value: 'gender' } })
    expect(screen.getByTestId('visible-ids')).toHaveTextContent('dimension.gender')
    expect(screen.getByTestId('visible-ids')).not.toHaveTextContent('entity.customer')

    fireEvent.click(screen.getByTitle('Reset view'))
    expect(screen.getByTestId('visible-ids')).toHaveTextContent('custom.note')
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('switches workspaces and collapses and expands the left rail', async () => {
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: /Cerebro Semantic constellation/i }))
    expect(screen.getByRole('menu', { name: 'Switch workspace' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('menuitemradio', { name: /Text to SQL agents/i }))
    expect(screen.getByRole('heading', { name: 'Your Curiosity - Reliable Answer' })).toBeInTheDocument()
    expect(screen.getByLabelText('Cerebro Agent')).toHaveTextContent('Cerebro Agent')
    expect(screen.getByRole('heading', { name: 'Ask Cerebro' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'What tables are available to query?' })).toBeInTheDocument()
    expect(document.querySelectorAll('.chat-panel')).toHaveLength(1)
    expect(document.querySelector('.chat-dock')).not.toBeInTheDocument()
    expect(screen.queryByText(/Governed database chat/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Every question is grounded/i)).not.toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Agent setup steps' })).toBeInTheDocument()
    expect(screen.getByText('All metadata available · database read-only')).toBeInTheDocument()
    expect(screen.queryByText('Guardrails & evals')).not.toBeInTheDocument()
    expect(screen.getByText('GreenNode · test-model')).toBeInTheDocument()
    expect(screen.getByLabelText('Runtime status')).toHaveTextContent('test-model')
    expect(screen.getByLabelText('Runtime status')).toHaveTextContent('DuckDB read-only')
    expect(screen.getByLabelText('Runtime status')).toHaveTextContent('100 row cap')
    expect(screen.queryByText(/Execution contract/i)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expect(screen.queryByRole('navigation', { name: 'Agent setup steps' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    expect(screen.getByRole('navigation', { name: 'Agent setup steps' })).toBeInTheDocument()
  })

  it('collapses the right details rail without moving the graph legend', async () => {
    render(<App />)
    expect(await screen.findByTestId('visible-ids')).toHaveTextContent('custom.note')

    fireEvent.click(screen.getByRole('button', { name: 'Collapse details panel' }))
    const expand = screen.getByRole('button', { name: 'Expand details panel' })
    expect(expand.closest('.right-panel-slot')).toHaveClass('collapsed')
    expect(expand.closest('.app-shell')).toHaveClass('right-panel-collapsed')
    expect(screen.getByLabelText('Graph edge legend').closest('.canvas-wrap')).not.toBeNull()

    fireEvent.click(expand)
    expect(screen.getByRole('button', { name: 'Collapse details panel' }).closest('.right-panel-slot')).not.toHaveClass('collapsed')
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

    const liveTab = await screen.findByRole('tab', { name: /bank-workshop/i })
    const generationTab = screen.getByRole('tab', { name: /Semantic generation/i })
    expect(liveTab).toHaveAttribute('aria-selected', 'true')
    expect(generationTab).toHaveAttribute('aria-selected', 'false')

    fireEvent.click(generationTab)
    expect(generationTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: /Generate semantics from zero/i })).toBeInTheDocument()
    expect(screen.queryByTestId('graph-view')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Graph edge legend')).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Raw DuckDB smoke test/i })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Run full pipeline' }))
    await waitFor(() => expect(mocks.startGeneration).toHaveBeenCalledWith('database_only'))
    expect(window.sessionStorage.getItem('cerebro.semanticGenerationRunId')).toBe('run-1')
    await waitFor(() => expect(eventSource).not.toBeNull())

    const progress = {
      sequence: 1, stage: 'catalog_scan', status: 'completed',
      summary: 'Discovered 10 tables, 75 columns, and 11 declared relationships.',
      command: 'cerebro scan', timestamp: '2026-09-03T00:00:01Z',
      details: { tables: 10, columns: 75, declared_relationships: 11, row_sampling: 'disabled', rows_read: 0, config_loaded: false },
    }
    act(() => eventSource?.emit('progress', progress))
    expect(await screen.findByText(progress.summary)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Semantic generation: 1 \/ 8 stages/i })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByTestId('graph-view')).not.toBeInTheDocument()

    fireEvent.click(liveTab)
    expect(liveTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('graph-view')).toHaveTextContent('0.2.0')
    fireEvent.click(generationTab)
    expect(await screen.findByText(progress.summary)).toBeInTheDocument()
    expect(screen.queryByTestId('graph-view')).not.toBeInTheDocument()
    fireEvent.click(liveTab)

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
    await waitFor(() => expect(mocks.getGenerationGraph).toHaveBeenCalledWith('run-1'))
    const readyTab = screen.getByRole('tab', { name: /Semantic generation: Candidate ready/i })
    expect(readyTab).toHaveAttribute('aria-selected', 'false')
    fireEvent.click(readyTab)
    expect(await screen.findByTestId('graph-view')).toHaveTextContent('0.1.0+candidate')

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
    fireEvent.click(liveTab)
    fireEvent.click(readyTab)
    expect(screen.getByRole('textbox', { name: 'Reviewer name' })).toHaveValue('Data Owner')
    fireEvent.click(screen.getByRole('checkbox', { name: /acknowledge/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Record approve' }))
    await waitFor(() => expect(mocks.reviewGeneration).toHaveBeenCalledWith('run-1', expect.objectContaining({ reviewer: 'Data Owner', acknowledge_ai_risk: true })))
    fireEvent.click(await screen.findByRole('button', { name: 'Activate reviewed bundle' }))
    await waitFor(() => expect(mocks.activateGeneration).toHaveBeenCalledWith('run-1'))
    expect(liveTab).toHaveAttribute('aria-selected', 'false')
    expect(readyTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('graph-view')).toHaveTextContent('0.1.0')
  })

  it('restores a running generation and its sanitized trace after refresh', async () => {
    const restoredRun = {
      id: 'run-restored', status: 'running', created_at: '2026-09-03T00:00:00Z', updated_at: '2026-09-03T00:00:01Z',
      events: [{
        sequence: 1, stage: 'business_semantics', status: 'started', summary: 'Building semantic inventory.',
        command: 'cerebro generate', timestamp: '2026-09-03T00:00:01Z', details: { agent_id: 'semantic_inventory' },
      }],
      candidate: null, error: null, source_mode: 'database_only',
    }
    mocks.getGeneration.mockResolvedValue(restoredRun)
    mocks.getGenerationTrace.mockResolvedValue({
      run_id: 'run-restored',
      steps: [{
        stage: 'business_semantics', actor: 'agent', agent_id: 'semantic_inventory', status: 'running',
        started_at: '2026-09-03T00:00:01Z', completed_at: null, summary: 'Building semantic inventory.',
        command: 'cerebro generate', input: { catalog: { tables: [{ name: 'accounts' }] } }, output: null, error: null,
      }],
    })
    window.sessionStorage.setItem('cerebro.semanticGenerationRunId', 'run-restored')

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Semantic inventory' })).toBeInTheDocument()
    expect(screen.getByLabelText('Input payload')).toHaveTextContent('accounts')
    expect(screen.queryByTestId('graph-view')).not.toBeInTheDocument()
    await waitFor(() => expect(eventSource?.url).toBe('/api/generation/runs/run-restored/events'))
  })

  it('keeps smoke generation database-only', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('tab', { name: /Semantic generation/i }))
    expect(screen.queryByText('Advanced source mode')).not.toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /Configured/i })).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Raw DuckDB smoke test/i })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Run full pipeline' }))
    await waitFor(() => expect(mocks.startGeneration).toHaveBeenCalledWith('database_only'))
  })
})
