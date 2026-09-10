import { AlertTriangle, ArrowLeft, Check, Clock, Database, Eye, GitBranch, Network, Play, ShieldCheck, Terminal, X } from 'lucide-react'
import type { GenerationEvent, GenerationRun, GenerationStage, GenerationTrace, GenerationTraceStep, RuntimeStatus } from './types'

export const generationStages: Array<{ id: GenerationStage; short: string; label: string; actor: string }> = [
  { id: 'source_check', short: 'Source', label: 'Check source', actor: 'DuckDB adapter' },
  { id: 'catalog_scan', short: 'Catalog', label: 'Scan catalog', actor: 'Catalog scanner' },
  { id: 'business_semantics', short: 'Inventory', label: 'Semantic inventory', actor: 'SemanticInventoryAgent' },
  { id: 'relationship_semantics', short: 'Links', label: 'Relationship semantics', actor: 'RelationshipAgent' },
  { id: 'query_semantics', short: 'Deferred', label: 'Metrics and rules · after activation', actor: 'Definition composer' },
  { id: 'compile_okf', short: 'OKF', label: 'Link and compile OKF', actor: 'Deterministic compiler' },
  { id: 'validate_candidate', short: 'Check', label: 'Validate candidate', actor: 'Contract validator' },
  { id: 'candidate_ready', short: 'Ready', label: 'Candidate ready', actor: 'Governance boundary' },
]

export interface GenerationReviewDraft {
  decision: 'approve' | 'reject'
  reviewer: string
  comment: string
  acknowledged: boolean
}

function stageStatus(events: GenerationEvent[], trace: GenerationTrace | null, stage: GenerationStage) {
  const traced = trace?.steps.find((step) => step.stage === stage)?.status
  if (traced) return traced
  const event = [...events].reverse().find((item) => item.stage === stage)?.status
  return event === 'started' ? 'running' : event || 'waiting'
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'completed') return <Check size={12} />
  if (status === 'failed') return <X size={12} />
  if (status === 'running' || status === 'started') return <Clock size={12} />
  return <span>—</span>
}

export function GenerationProgressTab({
  events,
  trace,
  run,
  active,
  starting,
  onSelect,
}: {
  events: GenerationEvent[]
  trace: GenerationTrace | null
  run: GenerationRun | null
  active: boolean
  starting: boolean
  onSelect: () => void
}) {
  const completedCount = generationStages.filter((stage) => ['completed', 'skipped', 'degraded'].includes(stageStatus(events, trace, stage.id))).length
  const status = starting ? 'starting' : run?.status || 'idle'
  const statusLabel = status === 'succeeded'
    ? 'Candidate ready'
    : status === 'failed'
      ? 'Needs attention'
      : ['starting', 'queued', 'running'].includes(status)
        ? `${completedCount} / ${generationStages.length} stages`
        : 'Idle'

  return (
    <button
      type="button"
      className={`semantic-version-tab generation ${active ? 'active' : ''} ${status}`}
      role="tab"
      aria-selected={active}
      aria-label={`Semantic generation: ${statusLabel}`}
      onClick={onSelect}
    >
      <span className="version-tab-copy"><strong>Semantic generation</strong><small>Raw catalog to candidate</small></span>
      <code>{run?.candidate ? `v${run.candidate.version}` : statusLabel}</code>
      <span className="generation-tab-track" aria-hidden="true">
        {generationStages.map((stage) => {
          const state = stageStatus(events, trace, stage.id)
          return <i className={state} key={stage.id} title={`${stage.label}: ${state}`} />
        })}
      </span>
    </button>
  )
}

function JsonPane({ label, value, waiting }: { label: string; value: Record<string, unknown> | null | undefined; waiting: string }) {
  return (
    <section className="trace-json-pane" aria-label={`${label} payload`}>
      <header><span>{label}</span><code>{value ? 'JSON' : '—'}</code></header>
      {value
        ? <pre>{JSON.stringify(value, null, 2)}</pre>
        : <div className="trace-json-empty"><Terminal size={18} /><p>{waiting}</p></div>}
    </section>
  )
}

export function GenerationWorkspace({
  run,
  trace,
  selectedStage,
}: {
  run: GenerationRun | null
  trace: GenerationTrace | null
  selectedStage: GenerationStage
}) {
  const definition = generationStages.find((stage) => stage.id === selectedStage) || generationStages[0]
  const step = trace?.steps.find((item) => item.stage === selectedStage)

  return (
    <section className="generation-workspace" aria-label="Generation pipeline workspace">
      <header className="workbench-heading">
        <div>
          <span className="workbench-kicker"><Network size={13} /> Full pipeline smoke test</span>
          <h1>{run ? definition.label : 'Generate semantics from zero'}</h1>
          <p>{run
            ? step?.summary || 'This stage is waiting for its upstream input.'
            : 'Start with the raw DuckDB catalog, observe every bounded agent, then validate a separate OKF candidate.'}</p>
        </div>
        <div className={`run-identity ${run?.status || 'idle'}`}>
          <small>{run ? 'Current run' : 'Run state'}</small>
          <code>{run?.id || 'Not started'}</code>
          <span>{run?.status || 'idle'}</span>
        </div>
      </header>

      {!run && (
        <div className="pipeline-blueprint" aria-label="Pipeline blueprint">
          {generationStages.map((stage, index) => (
            <div key={stage.id}>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <strong>{stage.short}</strong>
              <small>{stage.actor}</small>
            </div>
          ))}
        </div>
      )}

      <div className="trace-stage-meta">
        <span className={`trace-stage-state ${step?.status || 'waiting'}`}><StatusIcon status={step?.status || 'waiting'} />{step?.status || 'waiting'}</span>
        <span>{definition.actor}</span>
        <code>{step?.agent_id || definition.id}</code>
      </div>

      <div className="trace-io-grid">
        <JsonPane label="Input" value={step?.input} waiting={run ? 'Input becomes available when this stage starts.' : 'Start a smoke test to inspect the exact sanitized stage input.'} />
        <JsonPane label="Output" value={step?.output} waiting={step?.status === 'running' ? 'Waiting for the validated stage output.' : 'No output has been produced for this stage.'} />
      </div>

      <footer className="trace-command"><Terminal size={13} /><span>CLI equivalent</span><code>{step?.command || 'cerebro generate --source-mode database-only'}</code></footer>
    </section>
  )
}

export function GenerationPanel({
  runtime,
  run,
  events,
  trace,
  selectedStage,
  reviewDraft,
  starting,
  error,
  previewing,
  actioning,
  onReviewDraftChange,
  onSelectStage,
  onStart,
  onShowCandidate,
  onShowTrace,
  onReturnActive,
  onReview,
  onOpenVersions,
}: {
  runtime: RuntimeStatus | null
  run: GenerationRun | null
  events: GenerationEvent[]
  trace: GenerationTrace | null
  selectedStage: GenerationStage
  reviewDraft: GenerationReviewDraft
  starting: boolean
  error: string
  previewing: boolean
  actioning: boolean
  onReviewDraftChange: (draft: GenerationReviewDraft) => void
  onSelectStage: (stage: GenerationStage) => void
  onStart: (sourceMode: 'configured' | 'database_only') => void
  onShowCandidate: () => void
  onShowTrace: () => void
  onReturnActive: () => void
  onReview: (payload: { decision: 'approve' | 'reject'; reviewer: string; comment: string; acknowledge_ai_risk: boolean }) => void
  onOpenVersions: () => void
}) {
  const running = starting || run?.status === 'queued' || run?.status === 'running'
  const canStart = Boolean(runtime?.database_reachable) && !running && !actioning
  const objectCount = run?.candidate ? Object.values(run.candidate.counts).reduce((sum, count) => sum + count, 0) : 0
  const selectedStep: GenerationTraceStep | undefined = trace?.steps.find((step) => step.stage === selectedStage)

  return (
    <aside className="generation-panel" aria-label="Candidate generation">
      <header className="generation-heading">
        <span className="inspector-kicker"><Network size={12} /> Pipeline control</span>
        <h2>Raw catalog in.<br />Governed candidate out.</h2>
        <p>Run the complete catalog-to-OKF path and inspect each stage without reading source rows.</p>
      </header>

      <div className="generation-actions">
        <label className="smoke-source-card selected">
          <input type="radio" name="source-mode" checked readOnly />
          <Database size={15} />
          <span><strong>Raw DuckDB smoke test</strong><small>Catalog metadata only · zero source rows</small></span>
        </label>
        <p className="definition-note">Metrics and business rules are intentionally deferred until the graph is activated.</p>
        <button className="generate-button" disabled={!canStart} onClick={() => onStart('database_only')}>
          {running ? <Clock size={14} /> : <Play size={14} />}
          {running ? 'Running smoke test…' : run ? 'Start new smoke test' : 'Run full pipeline'}
        </button>
        {!runtime?.database_reachable && <p><AlertTriangle size={12} /> Configure a readable DuckDB source, then restart the server.</p>}
      </div>

      <div className="generation-timeline" aria-live="polite">
        <div className="timeline-heading"><span>Pipeline signal</span><code>{run?.source_mode.replace('_', '-') || 'database-only'}</code></div>
        {generationStages.map((stage, index) => {
          const status = stageStatus(events, trace, stage.id)
          const step = trace?.steps.find((item) => item.stage === stage.id)
          const event = [...events].reverse().find((item) => item.stage === stage.id)
          return (
            <button
              type="button"
              className={`pipeline-step ${status} ${selectedStage === stage.id ? 'selected' : ''}`}
              key={stage.id}
              aria-current={selectedStage === stage.id ? 'step' : undefined}
              onClick={() => onSelectStage(stage.id)}
            >
              <span className="pipeline-node"><StatusIcon status={status} /></span>
              <span className="pipeline-copy"><strong><em>{String(index + 1).padStart(2, '0')}</em>{stage.label}</strong><small>{step?.summary || event?.summary || stage.actor}</small></span>
              <code>{status}</code>
            </button>
          )
        })}
        {error && <article className="generation-error"><X size={13} /><span><strong>Generation stopped</strong><small>{error}</small></span></article>}

        {run?.candidate && (
          <article className="candidate-card">
            <span><Check size={14} /> Validated candidate</span>
            <h3>{run.candidate.name}</h3>
            <code>v{run.candidate.version}</code>
            <dl><div><dt>Objects</dt><dd>{objectCount}</dd></div><div><dt>Mode</dt><dd>{run.candidate.generation_mode}</dd></div><div><dt>Model</dt><dd>{run.candidate.model || 'none'}</dd></div></dl>
            <div className="candidate-view-actions">
              {previewing
                ? <button onClick={onShowTrace}><Terminal size={13} /> View pipeline trace</button>
                : <button onClick={onShowCandidate}><Eye size={13} /> View candidate graph</button>}
              <button onClick={onReturnActive}><ArrowLeft size={13} /> Active graph</button>
            </div>

            <section className="governance-gate">
              <header><ShieldCheck size={13} /><span><strong>Human governance gate</strong><small>The automated smoke test stops here.</small></span></header>
              {run.candidate.review_state === 'candidate' && (
                <form className="review-form" onSubmit={(event) => { event.preventDefault(); onReview({ decision: reviewDraft.decision, reviewer: reviewDraft.reviewer, comment: reviewDraft.comment, acknowledge_ai_risk: reviewDraft.acknowledged }) }}>
                  <label>Reviewer<input aria-label="Reviewer name" value={reviewDraft.reviewer} onChange={(event) => onReviewDraftChange({ ...reviewDraft, reviewer: event.target.value })} required /></label>
                  <div className="review-decisions">
                    <label><input type="radio" name="review-decision" checked={reviewDraft.decision === 'approve'} onChange={() => onReviewDraftChange({ ...reviewDraft, decision: 'approve' })} /> Approve</label>
                    <label><input type="radio" name="review-decision" checked={reviewDraft.decision === 'reject'} onChange={() => onReviewDraftChange({ ...reviewDraft, decision: 'reject' })} /> Reject</label>
                  </div>
                  <label>Comment<textarea aria-label="Review comment" value={reviewDraft.comment} onChange={(event) => onReviewDraftChange({ ...reviewDraft, comment: event.target.value })} required={reviewDraft.decision === 'reject'} /></label>
                  {reviewDraft.decision === 'approve' && <label className="risk-check"><input type="checkbox" checked={reviewDraft.acknowledged} onChange={(event) => onReviewDraftChange({ ...reviewDraft, acknowledged: event.target.checked })} /> I acknowledge that AI-proposed semantics require human judgment.</label>}
                  <button className="review-submit" disabled={actioning || !reviewDraft.reviewer.trim() || (reviewDraft.decision === 'approve' && !reviewDraft.acknowledged) || (reviewDraft.decision === 'reject' && !reviewDraft.comment.trim())} type="submit"><ShieldCheck size={13} /> {reviewDraft.decision === 'approve' ? 'Approve and save version' : 'Record rejection'}</button>
                </form>
              )}
              {run.candidate.review_state === 'approved' && <div className="review-result approved"><strong>Saved by {run.candidate.review_record?.reviewer}</strong><small>This immutable version is available without changing the workspace default.</small><button disabled={actioning} onClick={onOpenVersions}><GitBranch size={13} /> Open saved versions</button></div>}
              {run.candidate.review_state === 'rejected' && <div className="review-result rejected"><strong>Candidate rejected</strong><small>{run.candidate.review_record?.comment}</small></div>}
            </section>
          </article>
        )}
      </div>

      <footer className="generation-boundary"><ShieldCheck size={13} /><span>{selectedStep?.agent_id ? `${selectedStep.agent_id} input/output is sanitized.` : 'Review and activation remain separate recorded actions.'}</span></footer>
    </aside>
  )
}
