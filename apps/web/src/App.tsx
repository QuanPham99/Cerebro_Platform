import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Bookmark,
  Bot,
  Box,
  Check,
  ChevronRight,
  ChevronsUpDown,
  Clock,
  Database,
  FileText,
  Filter,
  Focus,
  ListChecks,
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
  Square,
  Terminal,
  Trash2,
  Waypoints,
  X,
} from 'lucide-react'
import { cancelChat, deleteBundleVersion, deleteSavedChart, generationEventsUrl, getBundle, getBundleVersionGraph, getBundleVersionObject, getBundleVersions, getConcept, getDefinitionContext, getDefinitionGraph, getDefinitionObject, getDefinitionRevision, getGeneration, getGenerationGraph, getGenerationTrace, getGraph, getRuntimeStatus, listSavedCharts, postChat, reviewGeneration, saveChart, SemanticApiError, setDefaultBundle, startGeneration } from './api'
import { DefinitionComposer } from './DefinitionComposer'
import { GenerationPanel, GenerationProgressTab, GenerationWorkspace, type GenerationReviewDraft } from './GenerationPanel'
import { GraphLegend, GraphView, type GraphHandle } from './GraphView'
import { Inspector } from './Inspector'
import { NodeNavigator, nodeTypes } from './NodeNavigator'
import { kindsForLayer, LAYER_PRESETS, PROFILE_PRESENTATION, type LayerPreset } from './profilePresentation'
import presetQuestionLevels from './presetQuestions.json'
import { ReportPanel } from './ReportPanel'
import { ResultPanel } from './ResultPanel'
import { SavedCharts } from './SavedCharts'
import type { BundleInfo, BundleVersionCatalog, BundleVersionSummary, ChatResponse, DefinitionContext, DefinitionRevision, GenerationEvent, GenerationRun, GenerationStage, GenerationTrace, GraphResponse, ProfileKind, RuntimeStatus, SavedChart, SemanticObject } from './types'
import { VersionLibrary } from './VersionLibrary'

type Workspace = 'semantic' | 'text-to-sql' | 'report'
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
  report: {
    label: 'Report agent',
    description: 'Multi-SQL reports, exported as PDF',
    icon: FileText,
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
        <img className="collapsed-brand-logo" src="/icons8-brain-96.png" alt="" aria-hidden="true" />
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
        <span className="brand-mark"><img src="/icons8-brain-96.png" alt="" aria-hidden="true" /></span>
        <span className="workspace-trigger-copy"><strong>Cerebro</strong><span>{workspaceDetails[active].label}</span></span>
        <ChevronsUpDown className="workspace-chevrons" size={14} />
      </button>
      <button className="collapse-sidebar" onClick={onCollapse} aria-label="Collapse sidebar" title="Collapse sidebar">
        <PanelLeftClose size={17} />
      </button>

      {open && (
        <div className="workspace-menu" role="menu" aria-label="Switch workspace">
          <div className="workspace-menu-heading"><span>Workspaces</span><kbd>3</kbd></div>
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
          <div className="workspace-menu-foot">One knowledge layer. Three ways to work.</div>
        </div>
      )}
    </div>
  )
}

function SchemaOverview() {
  const [context, setContext] = useState<DefinitionContext | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    getDefinitionContext({}, controller.signal)
      .then(setContext)
      .catch((reason: Error) => { if (reason.name !== 'AbortError') setError(reason.message || 'Could not load table schemas.') })
    return () => controller.abort()
  }, [])

  return (
    <details className="schema-overview">
      <summary className="schema-overview-heading rail-toggle">
        <ChevronRight size={12} className="chevron-icon" />
        <Database size={13} />
        <span>Database tables</span>
        <em>{context ? context.tables.length : '—'}</em>
      </summary>
      {error && <p className="schema-overview-status error">{error}</p>}
      {!context && !error && <p className="schema-overview-status">Loading table schemas…</p>}
      {context && context.tables.length === 0 && <p className="schema-overview-status">No physical tables in the active bundle.</p>}
      {context && context.tables.length > 0 && (
        <div className="schema-overview-list">
          {context.tables.map((table) => (
            <details className="schema-table" key={table.id}>
              <summary><ChevronRight size={12} /><span>{table.name}</span><em>{table.columns.length}</em></summary>
              <ul className="schema-table-columns">
                {table.columns.map((column) => (
                  <li key={column.name}><code>{column.name}</code><span>{column.data_type}</span></li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}
    </details>
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
    { label: 'Prototype access', detail: 'All metadata available · database read-only', ready: true },
  ]
  const readyCount = setupSteps.filter((step) => step.ready).length
  const nextIndex = setupSteps.findIndex((step) => !step.ready)
  return (
    <aside className="setup-rail">
      <details className="setup-rail-section" open>
        <summary className="setup-rail-heading rail-toggle">
          <span className="rail-heading-label"><ChevronRight size={12} className="chevron-icon" />Runtime setup</span>
          <em>{readyCount} / 5 ready</em>
        </summary>
        <div className="setup-progress"><i style={{ width: `${readyCount * 20}%` }} /></div>
        <nav aria-label="Agent setup steps">
          {setupSteps.map((step, index) => (
            <div key={step.label} className={`setup-step ${index === nextIndex ? 'active' : ''}`} aria-current={index === nextIndex ? 'step' : undefined}>
              <span className={`step-index ${step.ready ? 'ready' : index === nextIndex ? 'next' : 'waiting'}`}>{step.ready ? <Check size={12} /> : index + 1}</span>
              <span><strong>{step.label}</strong><small>{step.detail}</small></span>
            </div>
          ))}
        </nav>
      </details>
      <div className="setup-rail-note"><ShieldCheck size={14} /><span>Execution stays read-only until every launch check passes.</span></div>
    </aside>
  )
}

type ConversationEntry =
  | { role: 'user'; content: string }
  | { role: 'assistant'; question: string; response: ChatResponse }
  | { role: 'status'; requestId: string; content: string; warning?: boolean }

type ActiveChatRequest = {
  requestId: string
  controller: AbortController
}

/**
 * Difficulty-tiered evaluation prompts for the bank-workshop OKF bundle. Each
 * tier increases how many entities/joins, business rules, and semantic
 * ambiguity a correct answer must resolve, so a level-by-level run surfaces
 * where retrieval, the semantic profile, or the agent itself breaks down.
 * Source of truth is presetQuestions.json (shared with
 * scripts/validate_preset_questions.py) so the UI and the validation harness
 * never drift apart.
 */
const PRESET_QUESTION_LEVELS: { level: string; requiresSql: boolean; questions: string[] }[] = presetQuestionLevels

function PresetQuestions({ onSelect, disabled }: { onSelect: (question: string) => void; disabled: boolean }) {
  return (
    <details className="preset-panel" open>
      <summary className="preset-panel-heading rail-toggle">
        <ChevronRight size={12} className="chevron-icon" />
        <ListChecks size={14} />
        <span>Preset questions</span>
        {disabled && <em className="preset-panel-status">Sending…</em>}
      </summary>
      <div className="preset-levels">
        {PRESET_QUESTION_LEVELS.map((group) => (
          <section className="preset-level" key={group.level} aria-label={group.level}>
            <header><strong>{group.level}</strong><span>{group.questions.length}</span></header>
            <div className="preset-level-questions">
              {group.questions.map((question) => (
                <button type="button" key={question} disabled={disabled} onClick={() => onSelect(question)}>{question}</button>
              ))}
            </div>
          </section>
        ))}
      </div>
    </details>
  )
}

const PENDING_STAGES: { after: number; label: string }[] = [
  { after: 0, label: 'Retrieving semantic grounding' },
  { after: 2, label: 'Planning query intent' },
  { after: 6, label: 'Generating governed SQL' },
  { after: 14, label: 'Validating & executing query' },
  { after: 20, label: 'Synthesizing the answer' },
]
const PENDING_EXPECTED_SECONDS = 24

function QueryProgress({ elapsed, onStop }: { elapsed: number; onStop: () => void }) {
  const stage = [...PENDING_STAGES].reverse().find((entry) => elapsed >= entry.after) ?? PENDING_STAGES[0]
  const percent = Math.min(94, (elapsed / PENDING_EXPECTED_SECONDS) * 100)
  return (
    <article className="chat-message assistant pending">
      <div className="chat-message-head">
        <span>Cerebro</span>
        <button type="button" className="stop-query" onClick={onStop}><Square size={10} />Stop query</button>
      </div>
      <div className="query-progress">
        <p><Clock size={13} /> {stage.label}…</p>
        <div className="progress-bar" role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}>
          <div className="progress-bar-fill" style={{ width: `${percent}%` }} />
        </div>
        <small>{elapsed.toFixed(1)}s elapsed{elapsed > PENDING_EXPECTED_SECONDS ? ' · this one is taking longer than usual' : ''}</small>
      </div>
    </article>
  )
}

function SaveResultButton({ question, response, onSaved }: {
  question: string
  response: ChatResponse
  onSaved: () => void
}) {
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')

  const handleClick = async () => {
    if (state === 'saving') return
    setState('saving')
    try {
      await saveChart({
        question,
        sql: response.sql,
        columns: response.columns,
        rows: response.rows,
        row_count: response.row_count,
        truncated: response.truncated,
      })
      setState('saved')
      onSaved()
      window.setTimeout(() => setState('idle'), 2000)
    } catch (reason) {
      console.error('[Cerebro chat] Failed to save chart', reason)
      setState('error')
      window.setTimeout(() => setState('idle'), 2000)
    }
  }

  return (
    <button type="button" className="save-result" onClick={() => { void handleClick() }} disabled={state === 'saving'}>
      {state === 'saved' ? <Check size={12} /> : <Bookmark size={12} />}
      {state === 'saved' ? 'Saved' : state === 'error' ? 'Save failed' : 'Save'}
    </button>
  )
}

function DeleteEntryButton({ onDelete }: { onDelete: () => void }) {
  const [confirming, setConfirming] = useState(false)

  if (confirming) {
    return (
      <span className="chat-message-delete-confirm">
        <button type="button" onClick={onDelete}>Delete</button>
        <button type="button" onClick={() => setConfirming(false)}>Cancel</button>
      </span>
    )
  }

  return (
    <button type="button" className="chat-message-delete" onClick={() => setConfirming(true)}>
      <Trash2 size={12} />
      Delete
    </button>
  )
}

function AgentSetupWorkspace({
  runtime,
  onEvidence,
  active,
}: {
  runtime: RuntimeStatus | null
  onEvidence: (ids: Set<string>) => void
  active: boolean
}) {
  const [entries, setEntries] = useState<ConversationEntry[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [pendingElapsed, setPendingElapsed] = useState(0)
  const [conversationId, setConversationId] = useState<string>()
  const [savedRefreshKey, setSavedRefreshKey] = useState(0)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const activeRequestRef = useRef<ActiveChatRequest | null>(null)

  useEffect(() => {
    if (transcriptRef.current) transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
  }, [entries, pending])

  useEffect(() => {
    if (!pending) return
    setPendingElapsed(0)
    const start = Date.now()
    const timer = window.setInterval(() => setPendingElapsed((Date.now() - start) / 1000), 200)
    return () => window.clearInterval(timer)
  }, [pending])

  useEffect(() => () => {
    const active = activeRequestRef.current
    if (!active) return
    activeRequestRef.current = null
    void cancelChat(active.requestId).catch(() => undefined)
    active.controller.abort()
  }, [])

  const sendMessage = async (message: string) => {
    if (!message || activeRequestRef.current) return
    const previous = entries
    const requestId = crypto.randomUUID()
    const controller = new AbortController()
    activeRequestRef.current = { requestId, controller }
    setEntries([...previous, { role: 'user', content: message }])
    setInput('')
    setPending(true)
    try {
      const history = previous.reduce<Array<{ role: 'user' | 'assistant'; content: string }>>((messages, entry) => {
        if (entry.role === 'user') messages.push({ role: 'user', content: entry.content })
        if (entry.role === 'assistant') messages.push({ role: 'assistant', content: entry.response.answer })
        return messages
      }, []).slice(-10)
      const response = await postChat({ message, request_id: requestId, conversation_id: conversationId, history }, controller.signal)
      if (activeRequestRef.current?.requestId !== requestId) return
      setConversationId(response.conversation_id)
      setEntries((current) => [...current, { role: 'assistant', question: message, response }])
      onEvidence(new Set(response.evidence_ids))
    } catch (reason) {
      if (activeRequestRef.current?.requestId !== requestId) return
      if (reason instanceof Error && reason.name === 'AbortError') return
      if (!(reason instanceof SemanticApiError) || reason.status >= 500) {
        void cancelChat(requestId).then((response) => {
          console.info('[Cerebro chat] Background cancellation requested after API failure', response)
        }).catch((cancellationError) => {
          console.error('[Cerebro chat] Background cancellation failed after API failure', {
            requestId,
            error: cancellationError,
          })
        })
      }
      const messageText = reason instanceof Error ? reason.message : 'Chat request failed'
      setEntries((current) => [...current, {
        role: 'assistant',
        question: message,
        response: {
          conversation_id: conversationId || 'error', status: 'blocked', answer: messageText, sql: null,
          columns: [], rows: [], row_count: 0, truncated: false,
          semantic_version: runtime?.semantic_version || 'unknown', evidence_ids: [], warnings: [], trace: [],
        },
      }])
      onEvidence(new Set())
    } finally {
      if (activeRequestRef.current?.requestId === requestId) {
        activeRequestRef.current = null
        setPending(false)
      }
    }
  }

  const cancelActiveRequest = (clear: boolean) => {
    const active = activeRequestRef.current
    if (active) {
      activeRequestRef.current = null
      setPending(false)
      const cancellation = cancelChat(active.requestId)
      active.controller.abort()
      if (!clear) {
        setEntries((current) => [...current, {
          role: 'status', requestId: active.requestId, content: 'Query cancelled.',
        }])
        void cancellation.catch(() => {
          setEntries((current) => current.map((entry) => (
            entry.role === 'status' && entry.requestId === active.requestId
              ? { ...entry, content: 'Query cancelled in this browser, but server cancellation could not be confirmed.', warning: true }
              : entry
          )))
        })
      } else {
        void cancellation.catch(() => undefined)
      }
    }
    if (clear) {
      setEntries([])
      setConversationId(undefined)
      onEvidence(new Set())
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void sendMessage(input.trim())
  }

  const deleteEntry = (index: number) => {
    setEntries((current) => {
      const precedingIsQuestion = index > 0 && current[index - 1]?.role === 'user'
      const cutStart = precedingIsQuestion ? index - 1 : index
      return [...current.slice(0, cutStart), ...current.slice(index + 1)]
    })
  }

  const openSavedChart = (saved: SavedChart) => {
    setEntries((current) => [
      ...current,
      { role: 'user', content: saved.question },
      {
        role: 'assistant',
        question: saved.question,
        response: {
          conversation_id: 'saved', status: 'answered', answer: 'Reopened saved result.', sql: saved.sql,
          columns: saved.columns, rows: saved.rows, row_count: saved.row_count, truncated: saved.truncated,
          semantic_version: runtime?.semantic_version || 'unknown', evidence_ids: [], warnings: [], trace: [],
        },
      },
    ])
  }

  return (
    <section className="agent-workspace" hidden={!active}>
      <div className="chat-shell">
        <section className="chat-panel" aria-label="Cerebro Agent">
          <div className="chat-heading">
            <div className="chat-heading-title"><Bot size={16} /><span>Cerebro Agent</span></div>
            <div className="chat-heading-actions">
              {entries.length > 0 && <button type="button" onClick={() => cancelActiveRequest(true)}>Clear chat</button>}
            </div>
          </div>
          <div className="chat-transcript" ref={transcriptRef} aria-live="polite">
            {entries.length === 0 && <div className="chat-empty">
              <span><Sparkles size={20} /></span>
              <h2>Ask Cerebro</h2>
              <p>Explore the live database schema and every object in the active semantic bundle.</p>
              <p className="chat-empty-hint">Pick a question from the preset panel on the right to get started.</p>
            </div>}
            {entries.map((entry, index) => entry.role === 'user' ? (
              <article className="chat-message user" key={index}><span>You</span><p>{entry.content}</p></article>
            ) : entry.role === 'status' ? (
              <article className={`chat-message assistant cancelled${entry.warning ? ' warning' : ''}`} key={index}><span>Cerebro · cancelled</span><p>{entry.content}</p></article>
            ) : (
              <article className={`chat-message assistant ${entry.response.status}${entry.response.columns.length > 0 ? ' has-results' : ''}`} key={index}>
                <div className="chat-message-head">
                  <span>Cerebro · {entry.response.status}</span>
                  <div className="chat-message-actions">
                    {entry.response.status === 'answered' && entry.response.columns.length > 0 && (
                      <SaveResultButton question={entry.question} response={entry.response} onSaved={() => setSavedRefreshKey((key) => key + 1)} />
                    )}
                    <DeleteEntryButton onDelete={() => deleteEntry(index)} />
                  </div>
                </div>
                <p>{entry.response.answer}</p>
                {entry.response.sql && <details className="chat-detail"><summary><Terminal size={13} /> Generated SQL</summary><pre>{entry.response.sql}</pre></details>}
                {entry.response.columns.length > 0 && (
                  <ResultPanel columns={entry.response.columns} rows={entry.response.rows} rowCount={entry.response.row_count} truncated={entry.response.truncated} />
                )}
                {entry.response.evidence_ids.length > 0 && <details className="chat-detail"><summary><Network size={13} /> Semantic evidence · {entry.response.semantic_version}</summary><div className="evidence-chips">{entry.response.evidence_ids.map((id) => <code key={id}>{id}</code>)}</div></details>}
                {entry.response.warnings.length > 0 && <details className="chat-detail warning-detail"><summary><AlertTriangle size={13} /> Warnings</summary><ul>{entry.response.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details>}
                {entry.response.trace.length > 0 && <details className="chat-detail"><summary><Waypoints size={13} /> Agent trace</summary><ol className="trace-list">{entry.response.trace.map((item, traceIndex) => <li className={item.status} key={`${item.agent}-${traceIndex}`}><strong>{item.agent.replaceAll('_', ' ')}</strong><span>{item.summary}</span></li>)}</ol></details>}
              </article>
            ))}
            {pending && <QueryProgress elapsed={pendingElapsed} onStop={() => cancelActiveRequest(false)} />}
          </div>
          <form className="chat-composer" onSubmit={submit}>
            <label><span className="sr-only">Ask about the database</span><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="Ask a question about the database…" rows={2} /></label>
            {!pending && <button type="submit" disabled={!input.trim()} aria-label="Send question"><Send size={17} /></button>}
          </form>
        </section>

        <aside className="chat-tools" aria-label="Chat tools">
          <SavedCharts onOpen={openSavedChart} refreshKey={savedRefreshKey} />
          <PresetQuestions onSelect={sendMessage} disabled={pending} />
          <SchemaOverview />
        </aside>
      </div>
    </section>
  )
}

export default function App() {
  const [graph, setGraph] = useState<GraphResponse | null>(null)
  const [definitionGraph, setDefinitionGraph] = useState<GraphResponse | null>(null)
  const [candidateGraph, setCandidateGraph] = useState<GraphResponse | null>(null)
  const [versionGraph, setVersionGraph] = useState<GraphResponse | null>(null)
  const [bundle, setBundle] = useState<BundleInfo | null>(null)
  const [versionCatalog, setVersionCatalog] = useState<BundleVersionCatalog | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<BundleVersionSummary | null>(null)
  const [versionFocusId, setVersionFocusId] = useState<string | null>(null)
  const [versionLoading, setVersionLoading] = useState(true)
  const [versionError, setVersionError] = useState('')
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [types, setTypes] = useState<Set<ProfileKind>>(new Set(nodeTypes))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<SemanticObject | null>(null)
  const [inspectorLoading, setInspectorLoading] = useState(false)
  const [sidePanel, setSidePanel] = useState<'inspect' | 'define'>('inspect')
  const [graphMode, setGraphMode] = useState<'active' | 'candidate' | 'definition' | 'version'>('active')
  const [semanticTab, setSemanticTab] = useState<'default' | 'versions' | 'generation'>('default')
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
  const [workspace, setWorkspace] = useState<Workspace>('text-to-sql')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false)
  const [usedIds, setUsedIds] = useState<Set<string>>(new Set())
  const graphHandle = useRef<GraphHandle>(null)

  useEffect(() => {
    const resizeAfterTransition = window.setTimeout(() => graphHandle.current?.resize(), 240)
    return () => window.clearTimeout(resizeAfterTransition)
  }, [rightPanelCollapsed, sidebarCollapsed])

  const refreshVersions = useCallback((signal?: AbortSignal) => {
    setVersionLoading(true)
    setVersionError('')
    return getBundleVersions(signal)
      .then((catalog) => { setVersionCatalog(catalog); return catalog })
      .catch((reason: Error) => {
        if (reason.name !== 'AbortError') setVersionError(reason.message || 'Could not load saved graph versions.')
        throw reason
      })
      .finally(() => setVersionLoading(false))
  }, [])

  const load = useCallback(() => {
    const controller = new AbortController()
    setError('')
    Promise.all([getGraph(controller.signal), getBundle(controller.signal), getRuntimeStatus(controller.signal)])
      .then(([nextGraph, nextBundle, nextRuntime]) => {
        setGraph(nextGraph); setBundle(nextBundle); setRuntime(nextRuntime)
      })
      .catch((reason: Error) => { if (reason.name !== 'AbortError') setError(reason.message) })
    void refreshVersions(controller.signal).catch(() => undefined)
    return () => controller.abort()
  }, [refreshVersions])

  useEffect(load, [load])
  const onChatEvidence = useCallback((ids: Set<string>) => {
    setSelectedId(null)
    setSelected(null)
    setUsedIds(ids)
  }, [])

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
        setSemanticTab('generation')
        setGraphMode('candidate')
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
    setSemanticTab('generation')
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
      const reviewRecord = await reviewGeneration(generationRun.id, payload)
      setGenerationRun((current) => current?.id === generationRun.id && current.candidate
        ? {
            ...current,
            candidate: {
              ...current.candidate,
              review_state: reviewRecord.decision === 'approve' ? 'approved' : 'rejected',
              review_record: reviewRecord,
            },
          }
        : current)
      if (payload.decision === 'approve') {
        window.sessionStorage.removeItem(GENERATION_RUN_STORAGE_KEY)
        try { await refreshVersions() } catch { /* the library renders its retryable error */ }
        setVersionFocusId(generationRun.id)
        setVersionGraph(null)
        setSelectedVersion(null)
        setSemanticTab('versions')
        setSelectedId(null)
        setSelected(null)
      }
    } catch (reason) {
      setGenerationError(reason instanceof Error ? reason.message : 'Could not record the review decision.')
    } finally {
      setGenerationActioning(false)
    }
  }

  const selectWorkspaceDefault = async (version: BundleVersionSummary) => {
    setGenerationActioning(true)
    setVersionError('')
    let committed = false
    try {
      await setDefaultBundle(version.id)
      committed = true
      const [nextGraph, nextBundle, nextRuntime] = await Promise.all([getGraph(), getBundle(), getRuntimeStatus()])
      setGraph(nextGraph)
      setBundle(nextBundle)
      setRuntime(nextRuntime)
      setVersionCatalog((current) => current ? {
        ...current,
        default_id: version.id,
        versions: current.versions.map((item) => ({ ...item, is_default: item.id === version.id })),
      } : current)
      setSemanticTab('default')
      setGraphMode('active')
      setSidePanel('inspect')
      setVersionGraph(null)
      setSelectedVersion(null)
      setDefinitionRevision(null)
      setDefinitionGraph(null)
      window.sessionStorage.removeItem(DEFINITION_REVISION_STORAGE_KEY)
      setSelectedId(null)
      setSelected(null)
      void refreshVersions().catch(() => undefined)
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : 'The workspace could not reload.'
      setVersionError(committed ? `Default changed, but the workspace could not reload: ${detail}` : detail)
    } finally {
      setGenerationActioning(false)
    }
  }

  const deleteSavedVersion = async (version: BundleVersionSummary) => {
    setGenerationActioning(true)
    setVersionError('')
    try {
      await deleteBundleVersion(version.id)
      await refreshVersions()
    } catch (reason) {
      setVersionError(reason instanceof Error ? reason.message : 'The version could not be deleted.')
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

  const showSavedVersions = async (focusId: string | null = null) => {
    setVersionFocusId(focusId)
    setVersionGraph(null)
    setSelectedVersion(null)
    setSelectedId(null)
    setSelected(null)
    setSemanticTab('versions')
    setGraphMode('version')
    setSidePanel('inspect')
    try { await refreshVersions() } catch { /* the library renders its retryable error */ }
  }

  const definitionApproved = (revisionId: string) => {
    setDefinitionRevision(null)
    setDefinitionGraph(null)
    window.sessionStorage.removeItem(DEFINITION_REVISION_STORAGE_KEY)
    void showSavedVersions(revisionId)
  }

  const definitionReviewed = (revisionId: string, decision: 'approve' | 'reject') => {
    if (decision === 'approve') {
      definitionApproved(revisionId)
      return
    }
    setDefinitionRevision(null)
    setDefinitionGraph(null)
    window.sessionStorage.removeItem(DEFINITION_REVISION_STORAGE_KEY)
    setGraphMode(selectedVersion ? 'version' : 'active')
    setSidePanel('inspect')
  }

  const previewSavedVersion = async (version: BundleVersionSummary) => {
    setVersionError('')
    try {
      const nextGraph = await getBundleVersionGraph(version.id)
      setVersionGraph(nextGraph)
      setSelectedVersion(version)
      setVersionFocusId(version.id)
      setSemanticTab('versions')
      setGraphMode('version')
      setSidePanel('inspect')
      setSelectedId(null)
      setSelected(null)
    } catch (reason) {
      setVersionError(reason instanceof Error ? reason.message : 'Could not preview this graph version.')
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
    setSemanticTab('default')
    setGraphMode('active')
    setSidePanel('inspect')
    setSelectedId(null)
    setSelected(null)
  }

  const openDefinitions = () => {
    if (definitionGraph && definitionRevision) {
      const base = versionCatalog?.versions.find((version) => version.id === definitionRevision.base_bundle_id)
      setSemanticTab(base && !base.is_default ? 'versions' : 'default')
      setGraphMode('definition')
    }
    setSidePanel('define')
  }

  const openGeneration = () => {
    setSemanticTab('generation')
    setGraphMode('candidate')
    setSelectedId(null)
    setSelected(null)
    if (generationRun?.candidate && !candidateGraph) void previewCandidate()
  }

  const showCandidateGraph = semanticTab === 'generation' && graphMode === 'candidate' && generationSurface === 'graph' && Boolean(candidateGraph)
  const showVersionLibrary = semanticTab === 'versions' && !versionGraph && graphMode !== 'definition'
  const displayedGraph = graphMode === 'definition' && definitionGraph
    ? definitionGraph
    : semanticTab === 'default'
      ? graph
      : semanticTab === 'versions'
        ? versionGraph
        : showCandidateGraph ? candidateGraph : null

  const select = useCallback((id: string) => {
    setUsedIds(new Set())
    setSelectedId(id)
    if (semanticTab === 'generation') return
    setSidePanel('inspect')
    setInspectorLoading(true)
    const request = graphMode === 'version' && selectedVersion
      ? getBundleVersionObject(selectedVersion.id, id)
      : graphMode === 'definition' && definitionRevision
        ? getDefinitionObject(definitionRevision.id, id)
        : getConcept(id)
    request.then(setSelected).catch((reason: Error) => setError(reason.message)).finally(() => setInspectorLoading(false))
  }, [definitionRevision, graphMode, selectedVersion, semanticTab])

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
    setUsedIds(new Set())
    graphHandle.current?.reset()
  }

  const defaultVersion = versionCatalog?.versions.find((version) => version.is_default) || null
  const definitionBaseVersion = definitionRevision?.base_bundle_id
    ? versionCatalog?.versions.find((version) => version.id === definitionRevision.base_bundle_id) || null
    : graphMode === 'version' && selectedVersion
      ? selectedVersion
      : defaultVersion

  if (error && !graph) {
    return <main className="state-page"><Network size={44} /><h1>Constellation unavailable</h1><p>{error}</p><button onClick={load}><RefreshCw size={16} /> Try again</button></main>
  }
  if (!graph) return <main className="state-page loading"><Sparkles size={40} /><h1>Mapping semantic space…</h1><p>Loading the reviewed OKF bundle.</p></main>

  return (
    <main className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''} ${workspace === 'semantic' && rightPanelCollapsed && !showVersionLibrary ? 'right-panel-collapsed' : ''}`}>
      <header className="topbar">
        <div className="brand-zone">
          <WorkspaceMenu active={workspace} collapsed={sidebarCollapsed} onCollapse={() => setSidebarCollapsed((current) => !current)} onSelect={setWorkspace} />
        </div>
        {workspace === 'semantic'
          ? <nav className="bundle-tabs" role="tablist" aria-label="Semantic versions">
              <button type="button" className={`semantic-version-tab live ${semanticTab === 'default' ? 'active' : ''}`} role="tab" aria-selected={semanticTab === 'default'} aria-label={`Default graph: ${bundle?.name || 'loading'}`} onClick={returnToActive}>
                <span className="version-tab-copy"><strong>{bundle?.name || 'Default graph'}</strong><small>Workspace default</small></span>
                <span className="version-tab-status"><i className="live-status" aria-hidden="true" /><code>v{bundle?.version || '—'}</code></span>
              </button>
              <button type="button" className={`semantic-version-tab versions ${semanticTab === 'versions' ? 'active' : ''}`} role="tab" aria-selected={semanticTab === 'versions'} aria-label={`Saved graph versions: ${versionCatalog?.versions.length || 0} versions`} onClick={() => { void showSavedVersions() }}>
                <span className="version-tab-copy"><strong>Versions</strong><small>Approved graph history</small></span>
                <span className="version-tab-status"><ListChecks size={13} /><code>{versionCatalog?.versions.length || 0}</code></span>
              </button>
              <GenerationProgressTab events={generationEvents} trace={generationTrace} run={generationRun} starting={generationStarting} active={semanticTab === 'generation'} onSelect={openGeneration} />
            </nav>
          : <h1 className="workspace-title"><span>Your Curiosity - Cerebro Reliable Answers</span></h1>}
        {workspace === 'semantic'
          ? showVersionLibrary
            ? <div className="top-actions generation-status"><span>{versionCatalog?.versions.length || 0} approved versions</span><code>Immutable history</code></div>
            : displayedGraph
            ? <div className="top-actions"><span>{visibleIds.size} / {displayedGraph.nodes.length} objects</span><button title="Fit graph" onClick={() => graphHandle.current?.fit()}><Focus size={17} /></button><button title="Reset view" onClick={reset}><RefreshCw size={17} /></button>{graphMode === 'candidate' && <button title="View pipeline trace" onClick={() => setGenerationSurface('trace')}><Terminal size={17} /></button>}{graphMode === 'version' && <button title="Back to versions" onClick={() => { void showSavedVersions(selectedVersion?.id || null) }}><ListChecks size={17} /></button>}</div>
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

      {workspace === 'semantic' ? showVersionLibrary
        ? <VersionLibrary catalog={versionCatalog} loading={versionLoading} error={versionError} focusId={versionFocusId} actioning={generationActioning} onPreview={previewSavedVersion} onSetDefault={selectWorkspaceDefault} onDelete={(version) => { void deleteSavedVersion(version) }} onRetry={() => { void refreshVersions().catch(() => undefined) }} onGenerate={openGeneration} />
        : <>
        {displayedGraph ? <section className="canvas-wrap">
          <div className="canvas-label"><span>{graphMode === 'candidate' ? 'Candidate build' : graphMode === 'definition' ? 'Definition draft' : graphMode === 'version' ? `${selectedVersion?.name || 'Saved graph'} · v${selectedVersion?.version || displayedGraph.version}` : 'Workspace default'}</span><small>{graphMode === 'candidate' || graphMode === 'definition' || graphMode === 'version' ? 'Validated preview · runtime unchanged' : 'Drag to pan · Scroll to zoom · Select to trace'}</small></div>
          <GraphView ref={graphHandle} graph={displayedGraph} visibleIds={visibleIds} selectedId={selectedId} usedIds={usedIds} onSelect={select} />
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
          {semanticTab === 'generation' ? <GenerationPanel
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
                onSelectStage={(stage) => { setSelectedGenerationStage(stage); setGenerationSurface('trace') }}
                onStart={startCandidate}
                onShowCandidate={previewCandidate}
                onShowTrace={() => setGenerationSurface('trace')}
                onReturnActive={returnToActive}
                onReview={reviewCandidate}
                onOpenVersions={() => { void showSavedVersions(generationRun?.id || null) }}
              /> : <div className="graph-side-panel">
                <div className="side-panel-tabs graph-panel-tabs" role="tablist" aria-label="Graph details">
                  <button className={sidePanel === 'inspect' ? 'active' : ''} onClick={() => setSidePanel('inspect')} role="tab" aria-selected={sidePanel === 'inspect'}>Inspect</button>
                  <button className={sidePanel === 'define' ? 'active' : ''} onClick={openDefinitions} role="tab" aria-selected={sidePanel === 'define'}>Define</button>
                </div>
                {sidePanel === 'inspect'
                  ? <Inspector object={selected} loading={inspectorLoading} sourceBase={graphMode === 'definition' || graphMode === 'version' ? null : '/knowledge'} />
                  : <DefinitionComposer runtime={runtime} baseVersion={definitionBaseVersion} selectedObject={selected} revision={definitionRevision} onRevision={registerDefinitionRevision} onGraphChange={showDefinitionGraph} onReviewed={definitionReviewed} />}
              </div>}
        </div>
      </> : null}

      <AgentSetupWorkspace runtime={runtime} onEvidence={onChatEvidence} active={workspace === 'text-to-sql'} />
      <ReportPanel active={workspace === 'report'} />

      {error && <div className="toast">{error}<button onClick={() => setError('')}><X size={14} /></button></div>}
    </main>
  )
}
