import { useState } from 'react'
import { AlertTriangle, ArrowLeft, Bot, Check, ChevronDown, Clock, Database, Eye, Network, Play, Rocket, ShieldCheck, Terminal, X } from 'lucide-react'
import type { GenerationEvent, GenerationRun, GenerationStage, RuntimeStatus } from './types'

export const generationStages: Array<{ id: GenerationStage; short: string; label: string }> = [
  { id: 'source_check', short: 'Source', label: 'Check source' },
  { id: 'catalog_scan', short: 'Catalog', label: 'Scan catalog' },
  { id: 'business_semantics', short: 'Meaning', label: 'Business semantics' },
  { id: 'relationship_semantics', short: 'Links', label: 'Relationship semantics' },
  { id: 'query_semantics', short: 'Query', label: 'Query semantics' },
  { id: 'compile_okf', short: 'OKF', label: 'Compile OKF' },
  { id: 'validate_candidate', short: 'Check', label: 'Validate candidate' },
  { id: 'candidate_ready', short: 'Ready', label: 'Candidate ready' },
]

function stageStatus(events: GenerationEvent[], stage: GenerationStage) {
  return [...events].reverse().find((event) => event.stage === stage)?.status || 'waiting'
}

export function GenerationRibbon({ events, run }: { events: GenerationEvent[]; run?: GenerationRun | null }) {
  const [expanded, setExpanded] = useState(false)
  if (events.length === 0) return null
  const current = events[events.length - 1]
  const completedCount = generationStages.filter((stage) => ['completed', 'skipped'].includes(stageStatus(events, stage.id))).length
  return (
    <section className={`generation-ribbon ${expanded ? 'expanded' : ''}`} aria-label="Candidate generation progress">
      <button className="generation-ribbon-toggle" type="button" aria-expanded={expanded} aria-controls="generation-process-detail" aria-label={expanded ? 'Hide detailed generation process' : 'Show detailed generation process'} onClick={() => setExpanded((value) => !value)}>
        <div className="generation-ribbon-track">
          {generationStages.map((stage) => {
            const status = stageStatus(events, stage.id)
            return <span className={`generation-ribbon-step ${status}`} key={stage.id} title={`${stage.label}: ${status}`}><i />{stage.short}</span>
          })}
        </div>
        <div className="generation-ribbon-summary">
          <span><strong>{run?.source_mode === 'database_only' ? 'Database only' : 'Configured source'}</strong><em>{completedCount} / {generationStages.length} stages</em></span>
          <small>{current.summary}</small>
          <ChevronDown size={14} aria-hidden="true" />
        </div>
      </button>

      {expanded && (
        <div className="generation-process-detail" id="generation-process-detail" role="region" aria-label="Detailed generation process" aria-live="polite">
          <header><div><span>Live execution log</span><strong>{run?.id || 'Starting run'}</strong></div><em>{run?.status || 'running'}</em></header>
          <ol>
            {generationStages.map((stage, index) => {
              const stageEvents = events.filter((event) => event.stage === stage.id)
              const status = stageStatus(events, stage.id)
              return (
                <li className={`generation-process-step ${status}`} key={stage.id}>
                  <div className="generation-process-marker"><span>{String(index + 1).padStart(2, '0')}</span><i /></div>
                  <div className="generation-process-body">
                    <div className="generation-process-title"><strong>{stage.label}</strong><em>{status}</em></div>
                    {stageEvents.length === 0
                      ? <p className="generation-process-waiting">Waiting for the previous step to finish.</p>
                      : stageEvents.map((event) => (
                        <div className="generation-process-event" key={event.sequence}>
                          <div><time>{new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time><span>{event.status}</span></div>
                          <p>{event.summary}</p>
                          {Object.keys(event.details).length > 0 && <dl>{Object.entries(event.details).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{String(value)}</dd></div>)}</dl>}
                          <code><Terminal size={11} aria-hidden="true" />{event.command}</code>
                        </div>
                      ))}
                  </div>
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </section>
  )
}

function StatusIcon({ status }: { status: GenerationEvent['status'] }) {
  if (status === 'completed') return <Check size={12} />
  if (status === 'failed') return <X size={12} />
  if (status === 'started') return <Clock size={12} />
  return <span>—</span>
}

export function GenerationPanel({
  runtime,
  run,
  events,
  starting,
  error,
  previewing,
  actioning,
  onStart,
  onInspect,
  onPreview,
  onReturnActive,
  onReview,
  onActivate,
}: {
  runtime: RuntimeStatus | null
  run: GenerationRun | null
  events: GenerationEvent[]
  starting: boolean
  error: string
  previewing: boolean
  actioning: boolean
  onStart: (sourceMode: 'configured' | 'database_only') => void
  onInspect: () => void
  onPreview: () => void
  onReturnActive: () => void
  onReview: (payload: { decision: 'approve' | 'reject'; reviewer: string; comment: string; acknowledge_ai_risk: boolean }) => void
  onActivate: () => void
}) {
  const [sourceMode, setSourceMode] = useState<'' | 'configured' | 'database_only'>('')
  const [decision, setDecision] = useState<'approve' | 'reject'>('approve')
  const [reviewer, setReviewer] = useState('')
  const [comment, setComment] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const running = starting || run?.status === 'queued' || run?.status === 'running'
  const canStart = Boolean(runtime?.database_reachable) && Boolean(sourceMode) && !running && !actioning
  const objectCount = run?.candidate ? Object.values(run.candidate.counts).reduce((sum, count) => sum + count, 0) : 0

  return (
    <aside className="generation-panel" aria-label="Candidate generation">
      <div className="side-panel-tabs" role="tablist" aria-label="Semantic side panel">
        <button onClick={onInspect} role="tab" aria-selected="false">Inspect</button>
        <button className="active" role="tab" aria-selected="true">Build</button>
      </div>

      <header className="generation-heading">
        <span className="inspector-kicker"><Network size={12} /> Candidate observatory</span>
        <h2>Build meaning,<br />not database tables.</h2>
        <p>Cerebro reads the existing catalog, proposes semantics, and produces a separate OKF candidate. Downstream agents keep using the active reviewed bundle.</p>
      </header>

      <div className="generation-actions">
        <fieldset className="source-mode-picker">
          <legend>Source mode</legend>
          <label><input type="radio" name="source-mode" checked={sourceMode === 'database_only'} onChange={() => setSourceMode('database_only')} /><span><strong>Database only</strong><small>Raw DuckDB catalog; no source YAML or web content.</small></span></label>
          <label><input type="radio" name="source-mode" checked={sourceMode === 'configured'} onChange={() => setSourceMode('configured')} /><span><strong>Configured</strong><small>Include reviewed source declarations.</small></span></label>
        </fieldset>
        <button className="generate-button" disabled={!canStart} onClick={() => sourceMode && onStart(sourceMode)}>
          {running ? <Clock size={14} /> : <Play size={14} />}
          {running ? 'Generating candidate…' : 'Generate candidate'}
        </button>
        {!runtime?.database_reachable && <p><AlertTriangle size={12} /> Configure a readable DuckDB source, then restart the server.</p>}
      </div>

      <div className="generation-transcript" aria-live="polite">
        {!run && !error && (
          <div className="generation-empty">
            <div><Database size={16} /><span><strong>1. Discover</strong><small>Read schema metadata only—never source rows.</small></span></div>
            <div><Bot size={16} /><span><strong>2. Propose</strong><small>Generate typed business, relationship, and query semantics.</small></span></div>
            <div><ShieldCheck size={16} /><span><strong>3. Compile and validate</strong><small>Write a reviewable OKF candidate without activating it.</small></span></div>
          </div>
        )}

        {run && <article className="generation-message user"><span>You</span><p>Generate a semantic candidate in <code>{run.source_mode.replace('_', '-')}</code> mode.</p></article>}
        {events.map((event) => (
          <article className={`generation-message system ${event.status}`} key={event.sequence}>
            <div className="generation-message-title"><span className="stage-status"><StatusIcon status={event.status} /></span><strong>{generationStages.find((stage) => stage.id === event.stage)?.label}</strong><em>{event.status}</em></div>
            <p>{event.summary}</p>
            {Object.keys(event.details).length > 0 && <div className="generation-detail-chips">{Object.entries(event.details).map(([key, value]) => <code key={key}>{key.replaceAll('_', ' ')}: {String(value)}</code>)}</div>}
            <details><summary><Terminal size={11} /> CLI equivalent</summary><code>{event.command}</code></details>
          </article>
        ))}

        {error && <article className="generation-message system failed"><div className="generation-message-title"><span className="stage-status"><X size={12} /></span><strong>Generation stopped</strong><em>failed</em></div><p>{error}</p></article>}

        {run?.candidate && (
          <article className="candidate-card">
            <span><Check size={14} /> Validated candidate</span>
            <h3>{run.candidate.name}</h3>
            <code>v{run.candidate.version}</code>
            <dl><div><dt>Objects</dt><dd>{objectCount}</dd></div><div><dt>Mode</dt><dd>{run.candidate.generation_mode}</dd></div><div><dt>Model</dt><dd>{run.candidate.model || 'none'}</dd></div></dl>
            <div className="discovery-evidence" aria-label="Discovery evidence">
              {Object.entries(run.candidate.discovery_evidence).map(([key, value]) => <span key={key}><small>{key.replaceAll('_', ' ')}</small><strong>{String(value)}</strong></span>)}
            </div>
            {previewing
              ? <button onClick={onReturnActive}><ArrowLeft size={13} /> Return to active graph</button>
              : <button onClick={onPreview}><Eye size={13} /> Preview candidate graph</button>}
            {run.candidate.review_state === 'candidate' && (
              <form className="review-form" onSubmit={(event) => { event.preventDefault(); onReview({ decision, reviewer, comment, acknowledge_ai_risk: acknowledged }) }}>
                <h4>Whole-candidate decision</h4>
                <label>Reviewer<input aria-label="Reviewer name" value={reviewer} onChange={(event) => setReviewer(event.target.value)} required /></label>
                <div className="review-decisions">
                  <label><input type="radio" name="review-decision" checked={decision === 'approve'} onChange={() => setDecision('approve')} /> Approve</label>
                  <label><input type="radio" name="review-decision" checked={decision === 'reject'} onChange={() => setDecision('reject')} /> Reject</label>
                </div>
                <label>Comment<textarea aria-label="Review comment" value={comment} onChange={(event) => setComment(event.target.value)} required={decision === 'reject'} /></label>
                {decision === 'approve' && <label className="risk-check"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /> I acknowledge that AI-proposed semantics require human judgment.</label>}
                <button className="review-submit" disabled={actioning || !reviewer.trim() || (decision === 'approve' && !acknowledged) || (decision === 'reject' && !comment.trim())} type="submit"><ShieldCheck size={13} /> Record {decision}</button>
              </form>
            )}
            {run.candidate.review_state === 'approved' && <div className="review-result approved"><strong>Approved by {run.candidate.review_record?.reviewer}</strong><small>Approval does not activate the bundle.</small><button disabled={actioning} onClick={onActivate}><Rocket size={13} /> Activate reviewed bundle</button></div>}
            {run.candidate.review_state === 'rejected' && <div className="review-result rejected"><strong>Candidate rejected</strong><small>{run.candidate.review_record?.comment}</small></div>}
          </article>
        )}
      </div>

      <footer className="generation-boundary"><ShieldCheck size={13} /><span>Review and activation are separate recorded actions. AI provenance is preserved after approval.</span></footer>
    </aside>
  )
}
