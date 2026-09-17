import { type FormEvent, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, ChevronRight, Clock, Copy, Download, FileText, ListChecks, Send, Sparkles, Square, Terminal, Waypoints } from 'lucide-react'
import { cancelReport, getReportDocument, reportEventsUrl, reportPdfUrl, startReport } from './api'
import { ResultPanel } from './ResultPanel'
import type { ReportDocument, ReportEvent, ReportRun, ReportSectionResult } from './types'

const TOC_MIN_SECTIONS = 3

type StatusTone = 'good' | 'warn' | 'bad' | 'muted' | 'accent'

const STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  completed: { label: 'Completed', tone: 'good' },
  answered: { label: 'Answered', tone: 'good' },
  partial: { label: 'Partial', tone: 'warn' },
  clarification: { label: 'Needs clarification', tone: 'warn' },
  failed: { label: 'Failed', tone: 'bad' },
  blocked: { label: 'Blocked', tone: 'bad' },
  cancelled: { label: 'Cancelled', tone: 'muted' },
  running: { label: 'Running', tone: 'accent' },
}

function StatusBadge({ status, small }: { status: string; small?: boolean }) {
  const meta = STATUS_META[status] ?? { label: status, tone: 'muted' as StatusTone }
  return <span className={`status-badge tone-${meta.tone}${small ? ' small' : ''}`}>{meta.label}</span>
}

function formatReportDateTime(iso: string): string {
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return parsed.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function buildReportText(document: ReportDocument): string {
  const lines = [document.title, '']
  if (document.overview) lines.push('Executive summary:', document.overview, '')
  document.sections.forEach((section, index) => {
    lines.push(`${index + 1}. ${section.title}`, section.question, section.answer, '')
  })
  return lines.join('\n').trim()
}

interface ReportPreset {
  id: string
  label: string
  request: string
}

const REPORT_PRESETS: ReportPreset[] = [
  { id: 'executive-summary', label: 'Tổng quan điều hành', request: 'Tạo cho tôi báo cáo tổng quan điều hành' },
  { id: 'credit-risk', label: 'Rủi ro tín dụng & nợ xấu', request: 'Tạo cho tôi báo cáo rủi ro tín dụng và nợ xấu' },
  { id: 'card-fraud', label: 'Gian lận thẻ', request: 'Tạo cho tôi báo cáo gian lận thẻ' },
  { id: 'branch-performance', label: 'Hiệu suất chi nhánh', request: 'Tạo cho tôi báo cáo hiệu suất chi nhánh' },
  { id: 'customer-support', label: 'Hỗ trợ khách hàng', request: 'Tạo cho tôi báo cáo hỗ trợ khách hàng' },
  { id: 'cash-flow', label: 'Dòng tiền khách hàng', request: 'Tạo cho tôi báo cáo dòng tiền khách hàng' },
]

function ReportPresetPanel({ onSelect, disabled }: { onSelect: (text: string) => void; disabled: boolean }) {
  return (
    <details className="preset-panel" open>
      <summary className="preset-panel-heading rail-toggle">
        <ChevronRight size={12} className="chevron-icon" />
        <ListChecks size={14} />
        <span>Preset Reports</span>
        {disabled && <em className="preset-panel-status">Running…</em>}
      </summary>
      <div className="preset-levels">
        <div className="preset-level-questions">
          {REPORT_PRESETS.map((preset) => (
            <button type="button" key={preset.id} disabled={disabled} onClick={() => onSelect(preset.request)}>{preset.label}</button>
          ))}

        </div>
      </div>
    </details>
  )
}

interface ReportTranscriptEntry {
  role: 'user' | 'assistant'
  content?: string
  localId?: string
  run?: ReportRun
  document?: ReportDocument
  error?: string
}

function stageLabel(stage: string): string {
  if (stage === 'planning') return 'Planning'
  if (stage === 'synthesizing') return 'Synthesis'
  const match = /^s(\d+)$/.exec(stage)
  return match ? `Section ${match[1]}` : stage
}

function deriveTraceSteps(events: ReportEvent[]): Array<{ stage: string; status: string; summary: string }> {
  const order: string[] = []
  for (const event of events) {
    if (!order.includes(event.stage)) order.push(event.stage)
  }
  return order.map((stage) => {
    const stageEvents = events.filter((event) => event.stage === stage)
    const latest = stageEvents[stageEvents.length - 1]
    return { stage, status: latest.status, summary: latest.summary }
  })
}

type StepStatus = 'pending' | 'active' | 'done' | 'warn' | 'bad'

function stepStatusFor(events: ReportEvent[], stage: string): StepStatus {
  const stageEvents = events.filter((event) => event.stage === stage)
  const latest = stageEvents[stageEvents.length - 1]
  if (!latest) return 'pending'
  if (latest.status === 'started') return 'active'
  if (latest.status === 'blocked' || latest.status === 'failed') return 'bad'
  if (latest.status === 'clarification') return 'warn'
  return 'done'
}

function ReportProgress({ run, onStop, cancelling }: { run: ReportRun; onStop?: () => void; cancelling: boolean }) {
  const planningStarted = run.events.some((event) => event.stage === 'planning' && event.status === 'started')
  const planningEvent = [...run.events].reverse().find((event) => event.stage === 'planning' && event.status === 'completed')
  const sectionStages = [...new Set(run.events.filter((event) => /^s\d+$/.test(event.stage)).map((event) => event.stage))]
  const planValue = planningEvent?.details.sections
  const plan = Array.isArray(planValue) ? planValue.filter(
    (item): item is { id: string; title: string; question: string } =>
      typeof item === 'object' && item !== null && typeof item.id === 'string'
      && typeof item.title === 'string' && typeof item.question === 'string',
  ) : []
  const stages = plan.length ? plan.map((section) => section.id) : sectionStages
  const completedSections = stages.filter((stage) => ['done', 'warn', 'bad'].includes(stepStatusFor(run.events, stage))).length
  const synthesizing = run.events.some((event) => event.stage === 'synthesizing')
  const synthesizingDone = run.events.some((event) => event.stage === 'synthesizing' && event.status === 'completed')

  const headline = !planningStarted
    ? 'Starting report…'
    : !planningEvent
      ? 'Planning report…'
      : synthesizingDone
        ? 'Preparing report…'
        : synthesizing
          ? 'Synthesizing report…'
          : 'Running report sections…'

  return (
    <article className="chat-message assistant pending">
      <div className="chat-message-head">
        <span>Report Agent</span>
        {onStop && <button type="button" className="stop-query" onClick={onStop} disabled={cancelling}>
          <Square size={10} />{cancelling ? 'Stopping…' : 'Stop query'}
        </button>}
      </div>
      <div className="query-progress report-progress">
        <p><Clock size={13} /> {headline}</p>
        <div className={`progress-bar${!planningEvent ? ' report-progress-indeterminate' : ''}`}
          role="progressbar" aria-label="Report progress" aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={planningEvent ? Math.round(((1 + completedSections + (synthesizingDone ? 1 : 0)) / (stages.length + 2)) * 100) : undefined}
          aria-valuetext={headline}>
          <div className="progress-bar-fill" style={{ width: planningEvent ? `${((1 + completedSections + (synthesizingDone ? 1 : 0)) / (stages.length + 2)) * 100}%` : '30%' }} />
        </div>
        {plan.length > 0 && (
          <div className="report-plan">
            <strong>Report plan</strong>
            <ul aria-label="Report plan">
              {plan.map((section) => (
                <li key={section.id} data-status={stepStatusFor(run.events, section.id)}>
                  <strong>{section.title}</strong>
                  <p>{section.question}</p>
                  <small>{({ pending: 'Pending', active: 'Running', done: 'Completed', warn: 'Needs clarification', bad: 'Blocked' })[stepStatusFor(run.events, section.id)]}</small>
                </li>
              ))}
            </ul>
          </div>
        )}
        {planningEvent && plan.length === 0 && <p className="report-plan-summary">{planningEvent.summary}</p>}
        {sectionStages.length > 0 && (
          <ul className="report-section-list">
            {sectionStages.map((stage) => {
              const events = run.events.filter((event) => event.stage === stage)
              const latest = events[events.length - 1]
              const started = events.find((event) => event.status === 'started')
              const done = latest.status !== 'started'
              return (
                <li className={`report-section-item ${latest.status}`} key={stage}>
                  {done ? <Check size={12} /> : <Clock size={12} />}
                  <div>
                    <strong>{plan.find((section) => section.id === stage)?.title || started?.summary || stage}</strong>
                    {done && <p>{latest.summary}</p>}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </article>
  )
}

function anchorId(runId: string, sectionId: string): string {
  return `report-section-${runId}-${sectionId}`
}

function ReportSection({ section, index, runId }: { section: ReportSectionResult; index: number; runId: string }) {
  const meta = STATUS_META[section.status]
  const hasTable = section.status === 'answered' && section.columns.length > 0
  const isEmpty = section.status === 'answered' && section.row_count === 0
  return (
    <section className={`report-section-card tone-${meta.tone}`} id={anchorId(runId, section.id)}>
      <header className="report-section-card-head">
        <span className="report-section-index">{index + 1}</span>
        <div className="report-section-card-title">
          <h4>{section.title}</h4>
          <span className="report-section-question-chip">{section.question}</span>
        </div>
        <StatusBadge status={section.status} small />
      </header>
      <p className="report-section-answer">{section.answer}</p>
      {hasTable && (
        isEmpty ? (
          <p className="report-section-empty">No matching data.</p>
        ) : (
          <>
            <p className="report-section-rowcount">
              {section.row_count} result rows{section.truncated ? ' · truncated to the display limit' : ''}
            </p>
            <ResultPanel columns={section.columns} rows={section.rows} rowCount={section.row_count} truncated={section.truncated} />
          </>
        )
      )}
      {section.sql && (
        <details className="chat-detail">
          <summary><Terminal size={13} /> Generated SQL</summary>
          <pre>{section.sql}</pre>
        </details>
      )}
      {section.warnings.length > 0 && (
        <details className="chat-detail warning-detail">
          <summary><AlertTriangle size={13} /> Warnings</summary>
          <ul>{section.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </details>
      )}
    </section>
  )
}

function ReportToc({ sections, runId }: { sections: ReportSectionResult[]; runId: string }) {
  if (sections.length < TOC_MIN_SECTIONS) return null
  return (
    <nav className="report-toc" aria-label="Report contents">
      <span className="report-toc-label">Contents</span>
      <ol>
        {sections.map((section, index) => (
          <li key={section.id}>
            <a href={`#${anchorId(runId, section.id)}`}>{index + 1}. {section.title}</a>
          </li>
        ))}
      </ol>
    </nav>
  )
}

function ReportHeader({ document }: { document: ReportDocument }) {
  const [copied, setCopied] = useState(false)

  const copyReport = async () => {
    try {
      await navigator.clipboard.writeText(buildReportText(document))
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      // clipboard unavailable in this context; silently ignore
    }
  }

  return (
    <header className="report-header">
      <div className="chat-message-head">
        <span>Report Agent</span>
        <div className="report-header-actions">
          {document.status !== 'failed' && (
            <a className="report-download primary" href={reportPdfUrl(document.run_id)} download={`report-${document.run_id}.pdf`}>
              <Download size={13} /> Export PDF
            </a>
          )}
          <button type="button" className="report-copy" onClick={() => void copyReport()}>
            {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
      <div className="report-header-top">
        <div className="report-header-title">
          <FileText size={17} />
          <h3>{document.title}</h3>
        </div>
        <StatusBadge status={document.status} />
      </div>
      <div className="report-header-meta">
        <span><Clock size={12} /> {formatReportDateTime(document.generated_at)}</span>
        <span><ListChecks size={12} /> {document.sections.length} sections</span>
      </div>
    </header>
  )
}

function ReportResult({ document, run }: { document: ReportDocument; run?: ReportRun }) {
  const trace = run ? deriveTraceSteps(run.events) : []
  return (
    <article className={`chat-message assistant has-results report-result ${document.status}`}>
      <ReportHeader document={document} />
      {document.overview && (
        <div className="report-overview">
          <div className="report-overview-label"><Sparkles size={13} /> Executive summary</div>
          <p>{document.overview}</p>
        </div>
      )}
      <ReportToc sections={document.sections} runId={document.run_id} />
      <div className="report-sections">
        {document.sections.map((section, index) => (
          <ReportSection key={section.id} section={section} index={index} runId={document.run_id} />
        ))}
      </div>
      {trace.length > 0 && (
        <details className="chat-detail">
          <summary><Waypoints size={13} /> Agent trace · {trace.length} steps</summary>
          <ol className="trace-list">
            {trace.map((step) => (
              <li className={step.status} key={step.stage}><strong>{stageLabel(step.stage)}</strong><span>{step.summary}</span></li>
            ))}
          </ol>
        </details>
      )}
    </article>
  )
}

function ReportAssistantMessage({ entry, onStop, cancelling }: { entry: ReportTranscriptEntry; onStop?: () => void; cancelling: boolean }) {
  if (entry.document) return <ReportResult document={entry.document} run={entry.run} />
  if (entry.error) {
    return <article className="chat-message assistant blocked"><span>Report Agent</span><p>{entry.error}</p></article>
  }
  if (!entry.run) {
    return <article className="chat-message assistant pending"><span>Report Agent</span><p><Clock size={13} /> Starting report…</p></article>
  }
  if (entry.run.status === 'failed') {
    return <article className="chat-message assistant blocked"><span>Report Agent · failed</span><p>{entry.run.error || 'Could not create report.'}</p></article>
  }
  if (entry.run.status === 'cancelled') {
    return <article className="chat-message assistant cancelled"><span>Report Agent · cancelled</span><p>Report stopped at your request.</p></article>
  }
  return <ReportProgress run={entry.run} onStop={onStop} cancelling={cancelling} />
}

export function ReportPanel({ active }: { active: boolean }) {
  const [entries, setEntries] = useState<ReportTranscriptEntry[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const sourceRef = useRef<EventSource | null>(null)
  const activeRunRef = useRef<string | null>(null)

  useEffect(() => {
    if (transcriptRef.current) transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
  }, [entries])

  useEffect(() => () => sourceRef.current?.close(), [])

  const updateEntry = (localId: string, patch: Partial<ReportTranscriptEntry>) => {
    setEntries((current) => current.map((entry) => (entry.localId === localId ? { ...entry, ...patch } : entry)))
  }

  const startRun = async (requestText: string) => {
    const trimmed = requestText.trim()
    if (!trimmed || pending) return
    setInput('')
    setPending(true)
    setCancelling(false)
    const localId = crypto.randomUUID()
    setEntries((current) => [...current, { role: 'user', content: trimmed }, { role: 'assistant', localId }])
    try {
      const run = await startReport(trimmed)
      updateEntry(localId, { run })
      if (run.status !== 'running') {
        setPending(false)
        return
      }
      activeRunRef.current = run.run_id
      const source = new EventSource(reportEventsUrl(run.run_id))
      sourceRef.current = source
      source.addEventListener('progress', (event) => {
        const next = JSON.parse((event as MessageEvent<string>).data) as ReportEvent
        setEntries((current) => current.map((entry) => {
          if (entry.localId !== localId || !entry.run) return entry
          const events = [...entry.run.events.filter((item) => item.sequence !== next.sequence), next].sort((a, b) => a.sequence - b.sequence)
          return { ...entry, run: { ...entry.run, events } }
        }))
      })
      source.addEventListener('complete', (event) => {
        const completedRun = JSON.parse((event as MessageEvent<string>).data) as ReportRun
        updateEntry(localId, { run: completedRun })
        source.close()
        activeRunRef.current = null
        setCancelling(false)
        if (completedRun.status === 'cancelled' || (completedRun.status === 'failed' && completedRun.error)) {
          setPending(false)
          return
        }
        getReportDocument(completedRun.run_id)
          .then((document) => updateEntry(localId, { document }))
          .catch((reason: Error) => updateEntry(localId, { error: reason.message || 'Could not load report.' }))
          .finally(() => setPending(false))
      })
    } catch (reason) {
      updateEntry(localId, { error: reason instanceof Error ? reason.message : 'Could not create report.' })
      setPending(false)
    }
  }

  const stopActiveRun = () => {
    const runId = activeRunRef.current
    if (!runId) return
    setCancelling(true)
    void cancelReport(runId).catch(() => undefined)
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void startRun(input)
  }

  return (
    <section className="agent-workspace" hidden={!active}>
      <div className="chat-shell">
        <section className="chat-panel" aria-label="Report Agent">
          <div className="chat-heading">
            <div className="chat-heading-title"><FileText size={16} /><span>Report Agent</span></div>
            <div className="chat-heading-actions">

              {entries.length > 0 && <button type="button" onClick={() => setEntries([])} disabled={pending}>Clear chat</button>}
            </div>
          </div>
          <div className="chat-transcript" ref={transcriptRef} aria-live="polite">
            {entries.length === 0 && (
              <div className="chat-empty">
                <span><Sparkles size={20} /></span>
                <h2>Create a report</h2>
                <p>Describe what you want to analyze, or choose a preset report. Review the plan and follow each section as your report is created.</p>
              </div>
            )}
            {entries.map((entry, index) => entry.role === 'user' ? (
              <article className="chat-message user" key={index}><span>You</span><p>{entry.content}</p></article>
            ) : (
              <ReportAssistantMessage key={entry.localId ?? index} entry={entry} cancelling={cancelling}
                onStop={pending && entry.run?.run_id === activeRunRef.current ? stopActiveRun : undefined} />
            ))}
          </div>
          <form className="chat-composer" onSubmit={submit}>
            <label>
              <span className="sr-only">Report request</span>
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }}
                placeholder="Describe the report you need…"
                rows={2}
              />
            </label>

            {!pending && <button type="submit" disabled={!input.trim()} aria-label="Create report"><Send size={17} /></button>}
          </form>
        </section>

        <aside className="chat-tools" aria-label="Report tools">
          <ReportPresetPanel onSelect={(text) => void startRun(text)} disabled={pending} />
        </aside>
      </div>
    </section>
  )
}
