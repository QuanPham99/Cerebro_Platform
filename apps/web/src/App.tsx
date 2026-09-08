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
  PanelRightClose,
  PanelRightOpen,
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
import { activateGeneration, generationEventsUrl, getBundle, getConcept, getDefinitionGraph, getDefinitionObject, getDefinitionRevision, getGeneration, getGenerationConcept, getGenerationGraph, getGenerationTrace, getGoldenBundle, getGoldenGraph, getGoldenObject, getGraph, getRuntimeStatus, postChat, reviewGeneration, startGeneration } from './api'
import { DefinitionComposer } from './DefinitionComposer'
import { GenerationPanel, GenerationProgressTab, GenerationWorkspace, type GenerationReviewDraft } from './GenerationPanel'
import { GraphLegend, GraphView, type GraphHandle } from './GraphView'
import { Inspector } from './Inspector'
import { NodeNavigator, nodeTypes } from './NodeNavigator'
import { kindsForLayer, LAYER_PRESETS, PROFILE_PRESENTATION, type LayerPreset } from './profilePresentation'
import type { BundleInfo, ChatResponse, DefinitionRevision, GenerationEvent, GenerationRun, GenerationStage, GenerationTrace, GraphResponse, ProfileKind, RuntimeStatus, SemanticObject } from './types'

type Workspace = 'semantic' | 'text-to-sql'
const GENERATION_RUN_STORAGE_KEY = 'cerebro.semanticGenerationRunId'
const DEFINITION_REVISION_STORAGE_KEY = 'cerebro.definitionRevisionId'

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
  const [goldenGraph, setGoldenGraph] = useState<GraphResponse | null>(null)
  const [definitionGraph, setDefinitionGraph] = useState<GraphResponse | null>(null)
  const [candidateGraph, setCandidateGraph] = useState<GraphResponse | null>(null)
  const [bundle, setBundle] = useState<BundleInfo | null>(null)
  const [goldenBundle, setGoldenBundle] = useState<BundleInfo | null>(null)
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [types, setTypes] = useState<Set<ProfileKind>>(new Set(nodeTypes))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<SemanticObject | null>(null)
  const [inspectorLoading, setInspectorLoading] = useState(false)
  const [sidePanel, setSidePanel] = useState<'inspect' | 'build' | 'define'>('inspect')
  const [graphMode, setGraphMode] = useState<'golden' | 'active' | 'candidate' | 'definition'>('golden')
  const [generationRun, setGenerationRun] = useState<GenerationRun | null>(null)
  const [generationEvents, setGenerationEvents] = useState<GenerationEvent[]>([])
  const [generationTrace, setGenerationTrace] = useState<GenerationTrace | null>(null)
  const [selectedGenerationStage, setSelectedGenerationStage] = useState<GenerationStage>('source_check')
  const [generationSurface, setGenerationSurface] = useState<'trace' | 'graph'>('trace')
  const [definitionRevision, setDefinitionRevision] = useState<DefinitionRevision | null>(null)
  const [generationReviewDraft, setGenerationReviewDraft] = useState<GenerationReviewDraft>({
    decision: 'approve', reviewer: '', comment: '', acknowledged: false,
  })
  const [generationStarting, setGenerationStarting] = useState(false)
  const [generationError, setGenerationError] = useState('')
  const [generationActioning, setGenerationActioning] = useState(false)
  const [workspace, setWorkspace] = useState<Workspace>('semantic')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false)
  const graphHandle = useRef<GraphHandle>(null)

  useEffect(() => {
    const resizeAfterTransition = window.setTimeout(() => graphHandle.current?.resize(), 240)
    return () => window.clearTimeout(resizeAfterTransition)
  }, [rightPanelCollapsed, sidebarCollapsed])

  const load = useCallback(() => {
    const controller = new AbortController()
    setError('')
    Promise.all([getGraph(controller.signal), getBundle(controller.signal), getGoldenGraph(controller.signal), getGoldenBundle(controller.signal), getRuntimeStatus(controller.signal)])
      .then(([nextGraph, nextBundle, nextGoldenGraph, nextGoldenBundle, nextRuntime]) => {
        setGraph(nextGraph); setBundle(nextBundle); setGoldenGraph(nextGoldenGraph); setGoldenBundle(nextGoldenBundle); setRuntime(nextRuntime)
      })
      .catch((reason: Error) => { if (reason.name !== 'AbortError') setError(reason.message) })
    return () => controller.abort()
  }, [])

  useEffect(load, [load])

  const refreshGenerationTrace = useCallback((runId: string) => {
    return getGenerationTrace(runId).then((nextTrace) => {
      setGenerationTrace(nextTrace)
      return nextTrace
    })
  }, [])

  useEffect(() => {
    const runId = window.sessionStorage.getItem(GENERATION_RUN_STORAGE_KEY)
    if (!runId) return
    const controller = new AbortController()
    Promise.all([getGeneration(runId, controller.signal), getGenerationTrace(runId, controller.signal)])
      .then(([restoredRun, restoredTrace]) => {
        setGenerationRun(restoredRun)
        setGenerationEvents(restoredRun.events)
        setGenerationTrace(restoredTrace)
        setSelectedGenerationStage(restoredTrace.steps.at(-1)?.stage || restoredRun.events.at(-1)?.stage || 'source_check')
        setGraphMode('candidate')
        setSidePanel('build')
        if (restoredRun.status === 'succeeded' && restoredRun.candidate) {
          getGenerationGraph(restoredRun.id, controller.signal)
            .then((nextGraph) => { setCandidateGraph(nextGraph); setGenerationSurface('graph') })
            .catch((reason: Error) => { if (reason.name !== 'AbortError') setGenerationError(reason.message) })
        }
      })
      .catch((reason: Error) => {
        if (reason.name !== 'AbortError') window.sessionStorage.removeItem(GENERATION_RUN_STORAGE_KEY)
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const revisionId = window.sessionStorage.getItem(DEFINITION_REVISION_STORAGE_KEY)
    if (!revisionId) return
    const controller = new AbortController()
    Promise.all([
      getDefinitionRevision(revisionId, controller.signal),
      getDefinitionGraph(revisionId, controller.signal),
    ]).then(([restoredRevision, restoredGraph]) => {
      setDefinitionRevision(restoredRevision)
      setDefinitionGraph(restoredGraph)
    }).catch((reason: Error) => {
      if (reason.name !== 'AbortError') window.sessionStorage.removeItem(DEFINITION_REVISION_STORAGE_KEY)
    })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!generationRun || generationRun.status === 'succeeded' || generationRun.status === 'failed') return
    const source = new EventSource(generationEventsUrl(generationRun.id))
    const progress = (event: Event) => {
      const next = JSON.parse((event as MessageEvent<string>).data) as GenerationEvent
      setGenerationEvents((current) => current.some((item) => item.sequence === next.sequence) ? current : [...current, next])
      setSelectedGenerationStage(next.stage)
      setGenerationSurface('trace')
      void refreshGenerationTrace(generationRun.id).catch(() => undefined)
    }
    const complete = (event: Event) => {
      const completed = JSON.parse((event as MessageEvent<string>).data) as GenerationRun
      setGenerationRun(completed)
      setGenerationEvents(completed.events)
      void refreshGenerationTrace(completed.id).catch(() => undefined)
      if (completed.status === 'failed') setGenerationError(completed.error?.message || 'Candidate generation failed.')
      if (completed.status === 'succeeded' && completed.candidate) {
        getGenerationGraph(completed.id)
          .then((nextGraph) => { setCandidateGraph(nextGraph); setGenerationSurface('graph') })
          .catch((reason: Error) => setGenerationError(reason.message || 'Could not load the candidate graph.'))
      }
      source.close()
    }
    source.addEventListener('progress', progress)
    source.addEventListener('complete', complete)
    return () => source.close()
  }, [generationRun?.id, generationRun?.status, refreshGenerationTrace])

  const startCandidate = async (sourceMode: 'configured' | 'database_only') => {
    setSidePanel('build')
    setGenerationStarting(true)
    setGenerationError('')
    window.sessionStorage.removeItem(GENERATION_RUN_STORAGE_KEY)
    setGenerationEvents([])
    setGenerationTrace(null)
    setGenerationRun(null)
    setCandidateGraph(null)
    setGraphMode('candidate')
    setGenerationSurface('trace')
    setSelectedGenerationStage('source_check')
    setGenerationReviewDraft({ decision: 'approve', reviewer: '', comment: '', acknowledged: false })
    try {
      const run = await startGeneration(sourceMode)
      setGenerationRun(run)
      setGenerationEvents(run.events)
      window.sessionStorage.setItem(GENERATION_RUN_STORAGE_KEY, run.id)
      void refreshGenerationTrace(run.id).catch(() => undefined)
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
      setSidePanel('inspect')
      setSelectedId(null)
      setSelected(null)
    } catch (reason) {
      setGenerationError(reason instanceof Error ? reason.message : 'Could not activate the reviewed bundle.')
    } finally {
      setGenerationActioning(false)
    }
  }

  const registerDefinitionRevision = (revision: DefinitionRevision) => {
    setDefinitionRevision(revision)
    window.sessionStorage.setItem(DEFINITION_REVISION_STORAGE_KEY, revision.id)
  }

  const showDefinitionGraph = async (revision: DefinitionRevision, objectId: string) => {
    registerDefinitionRevision(revision)
    try {
      const nextGraph = await getDefinitionGraph(revision.id)
      setDefinitionGraph(nextGraph)
      setGraphMode('definition')
      setSidePanel('define')
      const kind = objectId.startsWith('metric.') ? 'metric' : 'business_rule'
      setTypes((current) => new Set([...current, kind]))
      setSelectedId(objectId)
      setSelected(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not load the definition draft graph.')
    }
  }

  const refreshAfterDefinitionActivation = async () => {
    try {
      const [nextGraph, nextBundle, nextRuntime] = await Promise.all([getGraph(), getBundle(), getRuntimeStatus()])
      setGraph(nextGraph); setBundle(nextBundle); setRuntime(nextRuntime)
      setGraphMode('active'); setSidePanel('inspect'); setSelectedId(null); setSelected(null)
      setDefinitionRevision(null); setDefinitionGraph(null)
      window.sessionStorage.removeItem(DEFINITION_REVISION_STORAGE_KEY)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not reload the activated definition revision.')
    }
  }

  const previewCandidate = async () => {
    if (!generationRun?.candidate) return
    setGenerationError('')
    try {
      const nextGraph = await getGenerationGraph(generationRun.id)
      setCandidateGraph(nextGraph)
      setGraphMode('candidate')
      setGenerationSurface('graph')
      setSelectedId(null)
      setSelected(null)
    } catch (reason) {
      setGenerationError(reason instanceof Error ? reason.message : 'Could not load the candidate graph.')
    }
  }

  const returnToActive = () => {
    setGraphMode('active')
    setSidePanel('inspect')
    setSelectedId(null)
    setSelected(null)
  }

  const returnToGolden = () => {
    setGraphMode('golden')
    setSidePanel('inspect')
    setSelectedId(null)
    setSelected(null)
  }

  const openDefinitions = () => {
    setGraphMode(definitionGraph ? 'definition' : 'active')
    setSidePanel('define')
    setSelectedId(null)
    setSelected(null)
  }

  const openGeneration = () => {
    setGraphMode('candidate')
    setSidePanel('build')
    setSelectedId(null)
    setSelected(null)
    if (generationRun?.candidate && !candidateGraph) void previewCandidate()
  }

  const openGeneratedWorkspace = () => {
    if (definitionGraph && definitionRevision) {
      setGraphMode('definition'); setSidePanel('define')
    } else if (bundle && goldenBundle && bundle.version !== goldenBundle.version) {
      setGraphMode('active'); setSidePanel('inspect')
    } else {
      openGeneration()
    }
  }

  const showCandidateGraph = graphMode === 'candidate' && generationSurface === 'graph' && Boolean(candidateGraph)
  const displayedGraph = graphMode === 'golden'
    ? goldenGraph
    : graphMode === 'active'
      ? graph
      : graphMode === 'definition'
        ? definitionGraph
        : showCandidateGraph ? candidateGraph : null

  const select = useCallback((id: string) => {
    setSelectedId(id)
    setSidePanel('inspect')
    setInspectorLoading(true)
    const request = graphMode === 'golden'
      ? getGoldenObject(id)
      : graphMode === 'definition' && definitionRevision
        ? getDefinitionObject(definitionRevision.id, id)
        : graphMode === 'candidate' && candidateGraph && generationRun
          ? getGenerationConcept(generationRun.id, id)
          : getConcept(id)
    request.then(setSelected).catch((reason: Error) => setError(reason.message)).finally(() => setInspectorLoading(false))
  }, [candidateGraph, definitionRevision, generationRun, graphMode])

  const visibleIds = useMemo(() => {
    if (!displayedGraph) return new Set<string>()
    const needle = query.trim().toLowerCase()
    return new Set(displayedGraph.nodes.filter((node) => types.has(node.profile_kind) && (!needle || `${node.label} ${node.id} ${node.description} ${PROFILE_PRESENTATION[node.profile_kind].label}`.toLowerCase().includes(needle))).map((node) => node.id))
  }, [displayedGraph, query, types])

  const toggleType = (type: ProfileKind) => setTypes((current) => {
    const next = new Set(current)
    if (next.has(type)) next.delete(type); else next.add(type)
    return next
  })

  const activeLayerPreset = useMemo(() => LAYER_PRESETS.find((preset) => {
    const kinds = kindsForLayer(preset)
    return kinds.length === types.size && kinds.every((kind) => types.has(kind))
  }) ?? null, [types])

  const applyLayerPreset = (preset: LayerPreset) => {
    setTypes(new Set(kindsForLayer(preset)))
    setSelectedId(null)
    setSelected(null)
  }

  const reset = () => {
    setQuery('')
    setTypes(new Set(nodeTypes))
    setSelectedId(null)
    setSelected(null)
    graphHandle.current?.reset()
  }

  if (error && (!graph || !goldenGraph)) {
    return <main className="state-page"><Network size={44} /><h1>Constellation unavailable</h1><p>{error}</p><button onClick={load}><RefreshCw size={16} /> Try again</button></main>
  }
  if (!graph || !goldenGraph) return <main className="state-page loading"><Sparkles size={40} /><h1>Mapping semantic space…</h1><p>Loading the reviewed OKF bundle.</p></main>

  return (
    <main className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''} ${workspace === 'semantic' && rightPanelCollapsed ? 'right-panel-collapsed' : ''}`}>
      <header className="topbar">
        <div className="brand-zone">
          <WorkspaceMenu active={workspace} collapsed={sidebarCollapsed} onCollapse={() => setSidebarCollapsed((current) => !current)} onSelect={setWorkspace} />
        </div>
        {workspace === 'semantic'
          ? <nav className="bundle-tabs" role="tablist" aria-label="Semantic versions">
              <button type="button" className={`semantic-version-tab live ${graphMode === 'golden' ? 'active' : ''}`} role="tab" aria-selected={graphMode === 'golden'} onClick={returnToGolden}>
                <span className="version-tab-copy"><strong>{goldenBundle?.name || 'bank-workshop'}</strong><small>Golden graph</small></span>
                <span className="version-tab-status"><i className="live-status" aria-hidden="true" /><code>v{goldenBundle?.version || '0.2.0'}</code></span>
              </button>
              <GenerationProgressTab events={generationEvents} trace={generationTrace} run={generationRun} starting={generationStarting} active={graphMode !== 'golden'} onSelect={openGeneratedWorkspace} />
            </nav>
          : <div className="runtime-chip"><i />Governed runtime <code>{runtime?.chat_ready ? 'ready' : 'setup'}</code></div>}
        {workspace === 'semantic'
          ? displayedGraph
            ? <div className="top-actions"><span>{visibleIds.size} / {displayedGraph.nodes.length} objects</span><button title="Fit graph" onClick={() => graphHandle.current?.fit()}><Focus size={17} /></button><button title="Reset view" onClick={reset}><RefreshCw size={17} /></button>{graphMode === 'candidate' && <button title="View pipeline trace" onClick={() => { setGenerationSurface('trace'); setSidePanel('build') }}><Terminal size={17} /></button>}</div>
            : <div className="top-actions generation-status"><span>{generationRun ? generationRun.status : 'Ready for raw catalog'}</span><code>{generationRun?.id || 'No run'}</code></div>
          : <div className="top-actions setup-actions"><span>{runtime?.model || 'Model not configured'}</span><span className="setup-phase">{runtime?.chat_ready ? 'Ready' : 'Setup'}</span></div>}
      </header>

      {!sidebarCollapsed && (workspace === 'semantic' && displayedGraph ? (
        <aside className="discovery-rail">
          <div className="rail-heading"><span>Discover</span><kbd>⌘ K</kbd></div>
          <label className="search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search meaning, table, metric…" aria-label="Search semantic objects" />{query && <button onClick={() => setQuery('')} aria-label="Clear search"><X size={14} /></button>}</label>
          <div className="filter-title"><Filter size={13} /> Object layers</div>
          <div className="layer-presets" role="group" aria-label="Graph layer presets">
            {LAYER_PRESETS.map((preset) => <button type="button" key={preset} aria-pressed={activeLayerPreset === preset} onClick={() => applyLayerPreset(preset)}>{preset}</button>)}
          </div>
          <div className="type-filters">
            {nodeTypes.map((type) => <label key={type}><input type="checkbox" checked={types.has(type)} onChange={() => toggleType(type)} /><span className={`type-symbol ${type}`} /><span>{PROFILE_PRESENTATION[type].label}</span><em>{displayedGraph.nodes.filter((node) => node.profile_kind === type).length}</em></label>)}
          </div>
          <div className="navigator-heading"><Box size={13} /> Keyboard navigator</div>
          <NodeNavigator nodes={displayedGraph.nodes} visibleIds={visibleIds} selectedId={selectedId} onSelect={select} />
        </aside>
      ) : workspace === 'text-to-sql' ? <AgentSetupRail runtime={runtime} /> : null)}

      {workspace === 'semantic' ? <>
        {displayedGraph ? <section className="canvas-wrap">
          <div className="canvas-label"><span>{graphMode === 'golden' ? 'Bank workshop · Golden' : graphMode === 'candidate' ? 'Candidate build' : graphMode === 'definition' ? 'Definition draft' : 'Activated graph'}</span><small>{graphMode === 'candidate' || graphMode === 'definition' ? 'Validated preview · not active' : 'Drag to pan · Scroll to zoom · Select to trace'}</small></div>
          <GraphView ref={graphHandle} graph={displayedGraph} visibleIds={visibleIds} selectedId={selectedId} onSelect={select} />
          <div className="layer-caption"><span>Physical structures</span><i /><span>Business meaning</span><i /><span>Governed metrics</span></div>
          <GraphLegend />
        </section> : <GenerationWorkspace run={generationRun} trace={generationTrace} selectedStage={selectedGenerationStage} />}

        <div className={`right-panel-slot ${rightPanelCollapsed ? 'collapsed' : ''}`}>
          <button
            type="button"
            className="right-panel-toggle"
            aria-label={`${rightPanelCollapsed ? 'Expand' : 'Collapse'} details panel`}
            title={`${rightPanelCollapsed ? 'Expand' : 'Collapse'} details panel`}
            onClick={() => setRightPanelCollapsed((current) => !current)}
          >
            {rightPanelCollapsed ? <PanelRightOpen size={17} /> : <PanelRightClose size={17} />}
          </button>
          {sidePanel === 'inspect'
            ? <Inspector object={selected} loading={inspectorLoading} onBuild={openGeneration} onDefine={openDefinitions} canDefine={Boolean(runtime?.review_state === 'approved' && (graphMode === 'active' || graphMode === 'definition'))} sourceBase={graphMode === 'candidate' && candidateGraph && generationRun ? `/api/generation/runs/${encodeURIComponent(generationRun.id)}/documents` : graphMode === 'definition' ? null : '/knowledge'} />
            : sidePanel === 'build' ? <GenerationPanel
                runtime={runtime}
                run={generationRun}
                events={generationEvents}
                trace={generationTrace}
                selectedStage={selectedGenerationStage}
                reviewDraft={generationReviewDraft}
                starting={generationStarting}
                actioning={generationActioning}
                error={generationError}
                previewing={showCandidateGraph}
                onReviewDraftChange={setGenerationReviewDraft}
                onSelectStage={(stage) => { setSelectedGenerationStage(stage); setGenerationSurface('trace'); setSidePanel('build') }}
                onStart={startCandidate}
                onInspect={() => { void previewCandidate(); setSidePanel('inspect') }}
                onDefine={openDefinitions}
                canDefine={Boolean(runtime?.review_state === 'approved' && (graphMode === 'active' || graphMode === 'definition'))}
                onShowCandidate={previewCandidate}
                onShowTrace={() => setGenerationSurface('trace')}
                onReturnActive={returnToActive}
                onReview={reviewCandidate}
                onActivate={activateCandidate}
              /> : <DefinitionComposer runtime={runtime} revision={definitionRevision} onRevision={registerDefinitionRevision} onGraphChange={showDefinitionGraph} onActivated={refreshAfterDefinitionActivation} onInspect={() => setSidePanel('inspect')} onBuild={openGeneration} />}
        </div>
      </> : <AgentSetupWorkspace runtime={runtime} />}

      {error && <div className="toast">{error}<button onClick={() => setError('')}><X size={14} /></button></div>}
    </main>
  )
}
