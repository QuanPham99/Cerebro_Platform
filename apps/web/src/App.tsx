import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Bot,
  Box,
  Check,
  ChevronsUpDown,
  CircleDot,
  Clock,
  Database,
  Filter,
  Focus,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Table2,
  Terminal,
  Waypoints,
  X,
} from 'lucide-react'
import { activateGeneration, generationEventsUrl, getBundle, getConcept, getGeneration, getGenerationConcept, getGenerationGraph, getGraph, getRuntimeStatus, postChat, reviewGeneration, startGeneration } from './api'
import { GenerationPanel, GenerationRibbon } from './GenerationPanel'
import { GraphLegend, GraphView, type GraphHandle } from './GraphView'
import { Inspector } from './Inspector'
import { NodeNavigator, nodeTypes } from './NodeNavigator'
import type { BundleInfo, ChatResponse, GenerationEvent, GenerationRun, GraphResponse, NodeType, RuntimeStatus, SemanticObject } from './types'

type Workspace = 'semantic' | 'text-to-sql'

const workspaceDetails = {
  semantic: {
    label: 'Semantic constellation',
    description: 'Explore governed knowledge',
    icon: Network,
  },
  'text-to-sql': {
    label: 'Text to SQL agents',
    description: 'Configure the agent runtime',
    icon: Bot,
  },
} satisfies Record<Workspace, { label: string; description: string; icon: typeof Network }>

function WorkspaceMenu({
  active,
  collapsed,
  onCollapse,
  onSelect,
}: {
  active: Workspace
  collapsed: boolean
  onCollapse: () => void
  onSelect: (workspace: Workspace) => void
}) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const ActiveIcon = workspaceDetails[active].icon

  useEffect(() => {
    if (!open) return
    const closeMenu = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', closeMenu)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeMenu)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  if (collapsed) {
    return (
      <button className="collapsed-brand" onClick={onCollapse} aria-label="Expand sidebar" title="Expand sidebar">
        <Network className="collapsed-brand-logo" size={19} aria-hidden="true" />
        <PanelLeftOpen className="collapsed-brand-expand" size={18} aria-hidden="true" />
      </button>
    )
  }

  return (
    <div className="workspace-control" ref={menuRef}>
      <button
        className="workspace-trigger"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <span className="brand-mark"><ActiveIcon size={19} /></span>
        <span className="workspace-trigger-copy"><strong>Cerebro</strong><span>{workspaceDetails[active].label}</span></span>
        <ChevronsUpDown className="workspace-chevrons" size={14} />
      </button>
      <button className="collapse-sidebar" onClick={onCollapse} aria-label="Collapse sidebar" title="Collapse sidebar">
        <PanelLeftClose size={17} />
      </button>

      {open && (
        <div className="workspace-menu" role="menu" aria-label="Switch workspace">
          <div className="workspace-menu-heading"><span>Workspaces</span><kbd>2</kbd></div>
          {(Object.keys(workspaceDetails) as Workspace[]).map((workspace) => {
            const detail = workspaceDetails[workspace]
            const Icon = detail.icon
            const selected = active === workspace
            return (
              <button
                key={workspace}
                className={selected ? 'active' : ''}
                onClick={() => { onSelect(workspace); setOpen(false) }}
                role="menuitemradio"
                aria-checked={selected}
              >
                <span className={`workspace-option-icon ${workspace}`}><Icon size={17} /></span>
                <span><strong>{detail.label}</strong><small>{detail.description}</small></span>
                {selected && <Check size={15} />}
              </button>
            )
          })}
          <div className="workspace-menu-foot">One knowledge layer. Two ways to work.</div>
        </div>
      )}
    </div>
  )
}

function AgentSetupRail({ runtime }: { runtime: RuntimeStatus | null }) {
  const setupSteps = [
    { label: 'Agent topology', detail: '5-agent bounded runtime', ready: true },
    { label: 'Semantic grounding', detail: runtime ? `${runtime.bundle} · ${runtime.semantic_version}` : 'Loading bundle', ready: Boolean(runtime) },
    {
      label: 'Model gateway',
      detail: runtime?.model ? `${runtime.provider_name} · ${runtime.model}` : 'Set model in .env',
      ready: Boolean(runtime?.llm_configured),
    },
    { label: 'Query connection', detail: runtime?.database_reachable ? `DuckDB · ${runtime.database_schema}` : 'Set database path', ready: Boolean(runtime?.database_reachable) },
    { label: 'Guardrails & evals', detail: 'Read-only policy active', ready: true },
  ]
  const readyCount = setupSteps.filter((step) => step.ready).length
  const nextIndex = setupSteps.findIndex((step) => !step.ready)
  return (
    <aside className="setup-rail">
      <div className="setup-rail-heading"><span>Runtime setup</span><em>{readyCount} / 5 ready</em></div>
      <div className="setup-progress"><i style={{ width: `${readyCount * 20}%` }} /></div>
      <nav aria-label="Agent setup steps">
        {setupSteps.map((step, index) => (
          <div key={step.label} className={`setup-step ${index === nextIndex ? 'active' : ''}`} aria-current={index === nextIndex ? 'step' : undefined}>
            <span className={`step-index ${step.ready ? 'ready' : index === nextIndex ? 'next' : 'waiting'}`}>{step.ready ? <Check size={12} /> : index + 1}</span>
            <span><strong>{step.label}</strong><small>{step.detail}</small></span>
          </div>
        ))}
      </nav>
      <div className="setup-rail-note"><ShieldCheck size={14} /><span>Execution stays read-only until every launch check passes.</span></div>
    </aside>
  )
}

const agents = [
  { name: 'Query planner', note: 'Intent · grain · filters', icon: Waypoints, tone: 'cyan' },
  { name: 'Knowledge retrieval', note: 'OKF · joins · policy', icon: Network, tone: 'violet' },
  { name: 'SQL generation', note: 'Dialect-aware SQL', icon: Database, tone: 'amber' },
  { name: 'Validation', note: 'Safety · cost · quality', icon: ShieldCheck, tone: 'rose' },
] as const

type ConversationEntry =
  | { role: 'user'; content: string }
  | { role: 'assistant'; response: ChatResponse }

const exampleQuestions = [
  'How many customers are there by gender?',
  'What percentage of card transactions are fraud?',
  'Explain the approved joins for transaction amount by branch.',
]

function AgentSetupWorkspace({ runtime }: { runtime: RuntimeStatus | null }) {
  const [entries, setEntries] = useState<ConversationEntry[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [conversationId, setConversationId] = useState<string>()
  const transcriptRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (transcriptRef.current) transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
  }, [entries, pending])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const message = input.trim()
    if (!message || pending) return
    const previous = entries
    setEntries([...previous, { role: 'user', content: message }])
    setInput('')
    setPending(true)
    try {
      const history = previous.map<{ role: 'user' | 'assistant'; content: string }>((entry) => entry.role === 'user'
        ? { role: 'user', content: entry.content }
        : { role: 'assistant', content: entry.response.answer }).slice(-10)
      const response = await postChat({ message, conversation_id: conversationId, history })
      setConversationId(response.conversation_id)
      setEntries((current) => [...current, { role: 'assistant', response }])
    } catch (reason) {
      const messageText = reason instanceof Error ? reason.message : 'Chat request failed'
      setEntries((current) => [...current, {
        role: 'assistant',
        response: {
          conversation_id: conversationId || 'error', status: 'blocked', answer: messageText, sql: null,
          columns: [], rows: [], row_count: 0, truncated: false,
          semantic_version: runtime?.semantic_version || 'unknown', evidence_ids: [], warnings: [], trace: [],
        },
      }])
    } finally {
      setPending(false)
    }
  }

  return (
    <section className="agent-workspace">
      <header className="agent-intro">
        <div><span className="eyebrow"><CircleDot size={11} /> Governed database chat</span><h1>Turn governed meaning<br />into trusted queries.</h1></div>
        <p>Every question is grounded in the active OKF bundle. Only validated, read-only SQL can reach DuckDB.</p>
      </header>
      <div className="runtime-summary" aria-label="Runtime status">
        <span className={runtime?.llm_configured ? 'ready' : 'blocked'}><Bot size={13} /> {runtime?.model || 'Model not configured'}</span>
        <span className={runtime?.database_reachable ? 'ready' : 'blocked'}><Database size={13} /> {runtime?.database_reachable ? 'DuckDB read-only' : 'Database unavailable'}</span>
        <span className="ready"><ShieldCheck size={13} /> {runtime?.query_row_limit || 100} row cap · {runtime?.query_timeout_seconds || 10}s</span>
      </div>

      <div className="chat-shell">
        <section className="chat-panel" aria-label="Database conversation">
          <div className="chat-heading">
            <div><Bot size={16} /><span>Database conversation</span></div>
            {entries.length > 0 && <button onClick={() => { setEntries([]); setConversationId(undefined) }}>Clear chat</button>}
          </div>
          <div className="chat-transcript" ref={transcriptRef} aria-live="polite">
            {entries.length === 0 && <div className="chat-empty">
              <span><Sparkles size={20} /></span>
              <h2>Ask the bank workshop database</h2>
              <p>Answers disclose the generated SQL, the OKF evidence used, and every safety decision.</p>
              <div>{exampleQuestions.map((question) => <button key={question} onClick={() => setInput(question)}>{question}</button>)}</div>
            </div>}
            {entries.map((entry, index) => entry.role === 'user' ? (
              <article className="chat-message user" key={index}><span>You</span><p>{entry.content}</p></article>
            ) : (
              <article className={`chat-message assistant ${entry.response.status}`} key={index}>
                <span>Cerebro · {entry.response.status}</span><p>{entry.response.answer}</p>
                {entry.response.sql && <details className="chat-detail"><summary><Terminal size={13} /> Generated SQL</summary><pre>{entry.response.sql}</pre></details>}
                {entry.response.columns.length > 0 && <div className="result-table-wrap"><table><thead><tr>{entry.response.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{entry.response.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, cellIndex) => <td key={cellIndex}>{String(value ?? 'NULL')}</td>)}</tr>)}</tbody></table>{entry.response.truncated && <small>Results truncated by the governed row cap.</small>}</div>}
                {entry.response.evidence_ids.length > 0 && <details className="chat-detail"><summary><Network size={13} /> Semantic evidence · {entry.response.semantic_version}</summary><div className="evidence-chips">{entry.response.evidence_ids.map((id) => <code key={id}>{id}</code>)}</div></details>}
                {entry.response.warnings.length > 0 && <details className="chat-detail warning-detail"><summary><AlertTriangle size={13} /> Warnings</summary><ul>{entry.response.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details>}
                {entry.response.trace.length > 0 && <details className="chat-detail"><summary><Waypoints size={13} /> Agent trace</summary><ol className="trace-list">{entry.response.trace.map((item, traceIndex) => <li className={item.status} key={`${item.agent}-${traceIndex}`}><strong>{item.agent.replaceAll('_', ' ')}</strong><span>{item.summary}</span></li>)}</ol></details>}
              </article>
            ))}
            {pending && <article className="chat-message assistant pending"><span>Cerebro</span><p><Clock size={13} /> Planning, grounding, and validating…</p></article>}
          </div>
          <form className="chat-composer" onSubmit={submit}>
            <label><span className="sr-only">Ask about the database</span><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="Ask a question about the database…" rows={2} /></label>
            <button type="submit" disabled={!input.trim() || pending} aria-label="Send question"><Send size={17} /></button>
          </form>
        </section>

        <aside className="runtime-panel">
          <div className="runtime-panel-heading"><Table2 size={15} /><span>Execution contract</span></div>
          <dl>
            <div><dt>Bundle</dt><dd>{runtime?.bundle || 'Loading'}</dd></div>
            <div><dt>Version</dt><dd>{runtime?.semantic_version || '—'}</dd></div>
            <div><dt>Mode</dt><dd>{runtime?.generation_mode || '—'}</dd></div>
            <div><dt>Response</dt><dd>{runtime?.response_mode || '—'}</dd></div>
          </dl>
          <p><ShieldCheck size={14} /> Restricted fields and database writes are blocked before execution.</p>
          <details className="topology-panel compact">
            <summary>Five-agent topology</summary>
            <div className="specialist-grid">
              <article className="agent-card violet"><strong>Orchestrator</strong><small>Lifecycle · final answer</small></article>
              {agents.map(({ name, note, tone }) => <article key={name} className={`agent-card ${tone}`}><strong>{name}</strong><small>{note}</small></article>)}
            </div>
          </details>
        </aside>
      </div>
    </section>
  )
}

export default function App() {
  const [graph, setGraph] = useState<GraphResponse | null>(null)
  const [candidateGraph, setCandidateGraph] = useState<GraphResponse | null>(null)
  const [bundle, setBundle] = useState<BundleInfo | null>(null)
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [types, setTypes] = useState<Set<NodeType>>(new Set(nodeTypes))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<SemanticObject | null>(null)
  const [inspectorLoading, setInspectorLoading] = useState(false)
  const [sidePanel, setSidePanel] = useState<'inspect' | 'build'>('inspect')
  const [graphMode, setGraphMode] = useState<'active' | 'candidate'>('active')
  const [generationRun, setGenerationRun] = useState<GenerationRun | null>(null)
  const [generationEvents, setGenerationEvents] = useState<GenerationEvent[]>([])
  const [generationStarting, setGenerationStarting] = useState(false)
  const [generationError, setGenerationError] = useState('')
  const [generationActioning, setGenerationActioning] = useState(false)
  const [workspace, setWorkspace] = useState<Workspace>('semantic')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const graphHandle = useRef<GraphHandle>(null)

  const load = useCallback(() => {
    const controller = new AbortController()
    setError('')
    Promise.all([getGraph(controller.signal), getBundle(controller.signal), getRuntimeStatus(controller.signal)])
      .then(([nextGraph, nextBundle, nextRuntime]) => { setGraph(nextGraph); setBundle(nextBundle); setRuntime(nextRuntime) })
      .catch((reason: Error) => { if (reason.name !== 'AbortError') setError(reason.message) })
    return () => controller.abort()
  }, [])

  useEffect(load, [load])

  useEffect(() => {
    if (!generationRun || generationRun.status === 'succeeded' || generationRun.status === 'failed') return
    const source = new EventSource(generationEventsUrl(generationRun.id))
    const progress = (event: Event) => {
      const next = JSON.parse((event as MessageEvent<string>).data) as GenerationEvent
      setGenerationEvents((current) => current.some((item) => item.sequence === next.sequence) ? current : [...current, next])
    }
    const complete = (event: Event) => {
      const completed = JSON.parse((event as MessageEvent<string>).data) as GenerationRun
      setGenerationRun(completed)
      setGenerationEvents(completed.events)
      if (completed.status === 'failed') setGenerationError(completed.error?.message || 'Candidate generation failed.')
      source.close()
    }
    source.addEventListener('progress', progress)
    source.addEventListener('complete', complete)
    return () => source.close()
  }, [generationRun?.id, generationRun?.status])

  const startCandidate = async (sourceMode: 'configured' | 'database_only') => {
    setSidePanel('build')
    setGenerationStarting(true)
    setGenerationError('')
    setGenerationEvents([])
    setGenerationRun(null)
    setCandidateGraph(null)
    setGraphMode('active')
    try {
      const run = await startGeneration(sourceMode)
      setGenerationRun(run)
      setGenerationEvents(run.events)
    } catch (reason) {
      setGenerationRun(null)
      setGenerationError(reason instanceof Error ? reason.message : 'Could not start candidate generation.')
    } finally {
      setGenerationStarting(false)
    }
  }

  const reviewCandidate = async (payload: { decision: 'approve' | 'reject'; reviewer: string; comment: string; acknowledge_ai_risk: boolean }) => {
    if (!generationRun?.candidate) return
    setGenerationActioning(true)
    setGenerationError('')
    try {
      await reviewGeneration(generationRun.id, payload)
      setGenerationRun(await getGeneration(generationRun.id))
    } catch (reason) {
      setGenerationError(reason instanceof Error ? reason.message : 'Could not record the review decision.')
    } finally {
      setGenerationActioning(false)
    }
  }

  const activateCandidate = async () => {
    if (!generationRun?.candidate) return
    setGenerationActioning(true)
    setGenerationError('')
    try {
      await activateGeneration(generationRun.id)
      const [nextGraph, nextBundle, nextRuntime, nextRun] = await Promise.all([
        getGraph(), getBundle(), getRuntimeStatus(), getGeneration(generationRun.id),
      ])
      setGraph(nextGraph)
      setBundle(nextBundle)
      setRuntime(nextRuntime)
      setGenerationRun(nextRun)
      setGraphMode('active')
      setSelectedId(null)
      setSelected(null)
    } catch (reason) {
      setGenerationError(reason instanceof Error ? reason.message : 'Could not activate the reviewed bundle.')
    } finally {
      setGenerationActioning(false)
    }
  }

  const previewCandidate = async () => {
    if (!generationRun?.candidate) return
    setGenerationError('')
    try {
      const nextGraph = await getGenerationGraph(generationRun.id)
      setCandidateGraph(nextGraph)
      setGraphMode('candidate')
      setSelectedId(null)
      setSelected(null)
    } catch (reason) {
      setGenerationError(reason instanceof Error ? reason.message : 'Could not load the candidate graph.')
    }
  }

  const returnToActive = () => {
    setGraphMode('active')
    setSelectedId(null)
    setSelected(null)
  }

  const displayedGraph = graphMode === 'candidate' && candidateGraph ? candidateGraph : graph

  const select = useCallback((id: string) => {
    setSelectedId(id)
    setSidePanel('inspect')
    setInspectorLoading(true)
    const request = graphMode === 'candidate' && generationRun
      ? getGenerationConcept(generationRun.id, id)
      : getConcept(id)
    request.then(setSelected).catch((reason: Error) => setError(reason.message)).finally(() => setInspectorLoading(false))
  }, [generationRun, graphMode])

  const visibleIds = useMemo(() => {
    if (!displayedGraph) return new Set<string>()
    const needle = query.trim().toLowerCase()
    return new Set(displayedGraph.nodes.filter((node) => types.has(node.type) && (!needle || `${node.label} ${node.id} ${node.description}`.toLowerCase().includes(needle))).map((node) => node.id))
  }, [displayedGraph, query, types])

  const toggleType = (type: NodeType) => setTypes((current) => {
    const next = new Set(current)
    if (next.has(type)) next.delete(type); else next.add(type)
    return next
  })

  const reset = () => {
    setQuery('')
    setTypes(new Set(nodeTypes))
    setSelectedId(null)
    setSelected(null)
    graphHandle.current?.reset()
  }

  if (error && !graph) {
    return <main className="state-page"><Network size={44} /><h1>Constellation unavailable</h1><p>{error}</p><button onClick={load}><RefreshCw size={16} /> Try again</button></main>
  }
  if (!graph || !displayedGraph) return <main className="state-page loading"><Sparkles size={40} /><h1>Mapping semantic space…</h1><p>Loading the reviewed OKF bundle.</p></main>

  return (
    <main className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <header className="topbar">
        <div className="brand-zone">
          <WorkspaceMenu active={workspace} collapsed={sidebarCollapsed} onCollapse={() => setSidebarCollapsed((current) => !current)} onSelect={setWorkspace} />
        </div>
        {workspace === 'semantic'
          ? <div className={`bundle-chip ${graphMode === 'candidate' ? 'candidate' : ''}`}><i />{graphMode === 'candidate' ? 'Candidate preview' : bundle?.name}<code>v{graphMode === 'candidate' ? generationRun?.candidate?.version : bundle?.version}</code></div>
          : <div className="runtime-chip"><i />Governed runtime <code>{runtime?.chat_ready ? 'ready' : 'setup'}</code></div>}
        {workspace === 'semantic'
          ? <div className="top-actions"><span>{visibleIds.size} / {displayedGraph.nodes.length} objects</span>{graphMode === 'candidate' && <button title="Return to active graph" onClick={returnToActive}><Network size={17} /></button>}<button title="Open candidate builder" onClick={() => setSidePanel('build')}><Terminal size={17} /></button><button title="Fit graph" onClick={() => graphHandle.current?.fit()}><Focus size={17} /></button><button title="Reset view" onClick={reset}><RefreshCw size={17} /></button></div>
          : <div className="top-actions setup-actions"><span>{runtime?.model || 'Model not configured'}</span><span className="setup-phase">{runtime?.chat_ready ? 'Ready' : 'Setup'}</span></div>}
      </header>

      {!sidebarCollapsed && (workspace === 'semantic' ? (
        <aside className="discovery-rail">
          <div className="rail-heading"><span>Discover</span><kbd>⌘ K</kbd></div>
          <label className="search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search meaning, table, metric…" aria-label="Search semantic objects" />{query && <button onClick={() => setQuery('')} aria-label="Clear search"><X size={14} /></button>}</label>
          <div className="filter-title"><Filter size={13} /> Object layers</div>
          <div className="type-filters">
            {nodeTypes.map((type) => <label key={type}><input type="checkbox" checked={types.has(type)} onChange={() => toggleType(type)} /><span className={`type-symbol ${type}`} /><span>{type}</span><em>{displayedGraph.nodes.filter((node) => node.type === type).length}</em></label>)}
          </div>
          <div className="navigator-heading"><Box size={13} /> Keyboard navigator</div>
          <NodeNavigator nodes={displayedGraph.nodes} visibleIds={visibleIds} selectedId={selectedId} onSelect={select} />
          <GraphLegend />
        </aside>
      ) : <AgentSetupRail runtime={runtime} />)}

      {workspace === 'semantic' ? <>
        <section className="canvas-wrap">
          <div className="canvas-label"><span>{graphMode === 'candidate' ? 'Candidate build' : 'Bank workshop'}</span><small>{graphMode === 'candidate' ? 'Validated preview · not active' : 'Drag to pan · Scroll to zoom · Select to trace'}</small></div>
          <GenerationRibbon events={generationEvents} run={generationRun} />
          <GraphView ref={graphHandle} graph={displayedGraph} visibleIds={visibleIds} selectedId={selectedId} onSelect={select} />
          <div className="layer-caption"><span>Physical structures</span><i /><span>Business meaning</span><i /><span>Governed metrics</span></div>
        </section>

        {sidePanel === 'inspect'
          ? <Inspector object={selected} loading={inspectorLoading} onBuild={() => setSidePanel('build')} sourceBase={graphMode === 'candidate' && generationRun ? `/api/generation/runs/${encodeURIComponent(generationRun.id)}/documents` : '/knowledge'} />
          : <GenerationPanel runtime={runtime} run={generationRun} events={generationEvents} starting={generationStarting} actioning={generationActioning} error={generationError} previewing={graphMode === 'candidate'} onStart={startCandidate} onInspect={() => setSidePanel('inspect')} onPreview={previewCandidate} onReturnActive={returnToActive} onReview={reviewCandidate} onActivate={activateCandidate} />}
      </> : <AgentSetupWorkspace runtime={runtime} />}

      {error && <div className="toast">{error}<button onClick={() => setError('')}><X size={14} /></button></div>}
    </main>
  )
}
