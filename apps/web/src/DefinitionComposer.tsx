import { Braces, Check, Clock, Plus, Rocket, ShieldCheck, Sparkles } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import {
  activateDefinitionRevision,
  addDefinition,
  createDefinitionRevision,
  getDefinitionContext,
  reviewDefinitionRevision,
  translateDefinition,
} from './api'
import type { DefinitionContext, DefinitionKind, DefinitionPayload, DefinitionRevision, RuntimeStatus } from './types'

const classifications = ['public', 'internal', 'confidential', 'restricted'] as const

function slug(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'new-definition'
}

function manualDefinition(kind: DefinitionKind, context: DefinitionContext): DefinitionPayload {
  const entity = context.entities[0]?.id || ''
  const table = context.tables[0]
  const column = table?.columns[0]?.name || ''
  const common = {
    id: `${kind === 'metric' ? 'metric' : 'rule'}.new-definition`,
    name: 'New definition',
    description: 'User-authored semantic definition.',
    entity,
    classification: 'internal',
    warnings: [],
  }
  if (kind === 'metric') {
    return { kind, definition: {
      ...common,
      measure: { kind: 'aggregate', aggregation: 'count_distinct', source: { table: table?.id || '', column }, predicates: [] },
      dependencies: table ? [table.id] : [],
      grain: { type: 'aggregate', description: 'Requested compatible dimensions', key: [] },
      compatible_dimensions: [],
      time_dimension: null,
      relative_time_anchor: null,
    } }
  }
  return { kind, definition: {
    ...common,
    rule_kind: 'predicate', output_type: 'boolean', dependencies: table ? [table.id] : [],
    logic: 'Describe the governed rule.', grain: { type: 'entity', description: 'One entity', key: [] },
  } }
}

export function DefinitionComposer({
  runtime,
  revision,
  onRevision,
  onGraphChange,
  onActivated,
  onInspect,
  onBuild,
}: {
  runtime: RuntimeStatus | null
  revision: DefinitionRevision | null
  onRevision: (revision: DefinitionRevision) => void
  onGraphChange: (revision: DefinitionRevision, objectId: string) => void
  onActivated: () => void
  onInspect: () => void
  onBuild: () => void
}) {
  const [context, setContext] = useState<DefinitionContext | null>(null)
  const [kind, setKind] = useState<DefinitionKind>('metric')
  const [intent, setIntent] = useState('')
  const [entityId, setEntityId] = useState('')
  const [draft, setDraft] = useState<DefinitionPayload | null>(null)
  const [measureText, setMeasureText] = useState('')
  const [origin, setOrigin] = useState<'declared' | 'ai_proposed'>('declared')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reviewer, setReviewer] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    getDefinitionContext(controller.signal)
      .then((value) => { setContext(value); setEntityId(value.entities[0]?.id || '') })
      .catch((reason: Error) => setError(reason.message))
    return () => controller.abort()
  }, [runtime?.semantic_version])

  const definition = draft?.definition || null
  useEffect(() => {
    if (draft?.kind === 'metric') setMeasureText(JSON.stringify(draft.definition.measure, null, 2))
  }, [draft?.kind, draft?.definition.measure])
  const selectedTable = useMemo(() => {
    if (!context || !definition || draft?.kind !== 'metric') return null
    const measure = definition.measure as Record<string, unknown> | undefined
    const source = measure?.source as Record<string, unknown> | undefined
    return context.tables.find((table) => table.id === source?.table) || context.tables[0] || null
  }, [context, definition, draft?.kind])

  const update = (field: string, value: unknown) => {
    if (!draft) return
    setDraft({ ...draft, definition: { ...draft.definition, [field]: value } })
  }

  const translate = async () => {
    if (!intent.trim()) return
    setBusy(true); setError('')
    try {
      const result = await translateDefinition({ kind, intent, ...(entityId ? { entity_id: entityId } : {}) })
      setDraft(result.payload)
      setOrigin('ai_proposed')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not translate this definition.')
    } finally { setBusy(false) }
  }

  const startManual = () => {
    if (!context) return
    setDraft(manualDefinition(kind, context))
    setOrigin('declared')
    setError('')
  }

  const save = async () => {
    if (!draft) return
    setBusy(true); setError('')
    try {
      const next = revision
        ? await addDefinition(revision.id, draft, origin)
        : await createDefinitionRevision(draft, origin)
      onRevision(next)
      onGraphChange(next, String(draft.definition.id))
      setDraft(null); setIntent('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not add this definition.')
    } finally { setBusy(false) }
  }

  const review = async () => {
    if (!revision) return
    setBusy(true); setError('')
    try {
      const record = await reviewDefinitionRevision(revision.id, {
        decision: 'approve', reviewer, comment: 'User-authored definitions reviewed.', acknowledge_ai_risk: acknowledged,
      })
      onRevision({ ...revision, review_state: 'approved', review_record: record })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not approve this definition revision.')
    } finally { setBusy(false) }
  }

  const activate = async () => {
    if (!revision) return
    setBusy(true); setError('')
    try { await activateDefinitionRevision(revision.id); onActivated() }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not activate this revision.') }
    finally { setBusy(false) }
  }

  const tabs = <div className="side-panel-tabs" role="tablist" aria-label="Semantic side panel"><button onClick={onInspect} role="tab" aria-selected="false">Inspect</button><button onClick={onBuild} role="tab" aria-selected="false">Build</button><button className="active" role="tab" aria-selected="true">Define</button></div>
  if (!runtime || runtime.review_state !== 'approved') return <aside className="definition-composer">{tabs}<div className="definition-empty"><ShieldCheck size={30} /><h2>Activate an approved graph</h2><p>Metrics and business rules are authored only after graph activation.</p></div></aside>

  return <aside className="definition-composer" aria-label="Definition composer">
    {tabs}
    <header><span className="inspector-kicker"><Sparkles size={12} /> Definition composer</span><h2>Add governed meaning</h2><p>Describe an idea or start with the compact form. Nothing changes until you add it to a draft revision.</p></header>
    <div className="definition-kind" role="group" aria-label="Definition type">
      <button className={kind === 'metric' ? 'active' : ''} onClick={() => { setKind('metric'); setDraft(null) }}>Metric</button>
      <button className={kind === 'business_rule' ? 'active' : ''} onClick={() => { setKind('business_rule'); setDraft(null) }}>Business rule</button>
    </div>
    <label>Describe your idea<textarea aria-label="Definition idea" value={intent} onChange={(event) => setIntent(event.target.value)} placeholder={kind === 'metric' ? 'Percentage of late payments by loan type' : 'A loan is non-performing when…'} /></label>
    <label>Entity hint<select value={entityId} onChange={(event) => setEntityId(event.target.value)}><option value="">Let Cerebro resolve it</option>{context?.entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}</select></label>
    <div className="definition-actions"><button disabled={busy || !runtime.llm_configured || !intent.trim()} onClick={translate}>{busy ? <Clock size={13} /> : <Sparkles size={13} />} Translate with LLM</button><button disabled={busy || !context} onClick={startManual}><Braces size={13} /> Use manual form</button></div>
    {!runtime.llm_configured && <p className="definition-note">No model provider is configured. The manual form remains available.</p>}

    {draft && definition && <section className="definition-draft">
      <header><span>Typed draft</span><code>{origin === 'ai_proposed' ? 'AI proposed' : 'Manual'}</code></header>
      <label>Name<input value={String(definition.name || '')} onChange={(event) => { update('name', event.target.value); update('id', `${kind === 'metric' ? 'metric' : 'rule'}.${slug(event.target.value)}`) }} /></label>
      <label>ID<input value={String(definition.id || '')} onChange={(event) => update('id', event.target.value)} /></label>
      <label>Description<textarea value={String(definition.description || '')} onChange={(event) => update('description', event.target.value)} /></label>
      <label>Owning entity<select value={String(definition.entity || '')} onChange={(event) => update('entity', event.target.value)}>{context?.entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}</select></label>
      <label>Classification<select value={String(definition.classification || 'internal')} onChange={(event) => update('classification', event.target.value)}>{classifications.map((value) => <option key={value}>{value}</option>)}</select></label>
      {draft.kind === 'metric' ? <>
        <label>Measure JSON<textarea aria-label="Measure JSON" value={measureText} onChange={(event) => { const value = event.target.value; setMeasureText(value); try { update('measure', JSON.parse(value)); setError('') } catch { setError('Measure must be valid JSON.') } }} /></label>
        <label>Dependencies<input value={(definition.dependencies as string[] || []).join(', ')} onChange={(event) => update('dependencies', event.target.value.split(',').map((item) => item.trim()).filter(Boolean))} /></label>
        <label>Compatible dimensions<input value={(definition.compatible_dimensions as string[] || []).join(', ')} onChange={(event) => update('compatible_dimensions', event.target.value.split(',').map((item) => item.trim()).filter(Boolean))} /></label>
        {selectedTable && <small>Available fields in {selectedTable.name}: {selectedTable.columns.map((column) => column.name).join(', ')}</small>}
      </> : <>
        <label>Rule logic<textarea value={String(definition.logic || '')} onChange={(event) => update('logic', event.target.value)} /></label>
        <label>Dependencies<input value={(definition.dependencies as string[] || []).join(', ')} onChange={(event) => update('dependencies', event.target.value.split(',').map((item) => item.trim()).filter(Boolean))} /></label>
      </>}
      <button className="definition-save" disabled={busy || Boolean(error)} onClick={save}><Plus size={13} /> Add to graph</button>
    </section>}
    {error && <p className="definition-error">{error}</p>}
    {revision && <section className="definition-revision">
      <header><Check size={13} /><span><strong>Draft revision</strong><small>{revision.version}</small></span></header>
      {revision.review_state === 'candidate' && <><label>Reviewer<input aria-label="Definition reviewer" value={reviewer} onChange={(event) => setReviewer(event.target.value)} /></label><label className="risk-check"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /> I reviewed the authored definitions and acknowledge AI risk.</label><button disabled={busy || !reviewer.trim() || !acknowledged} onClick={review}><ShieldCheck size={13} /> Approve revision</button></>}
      {revision.review_state === 'approved' && <button disabled={busy} onClick={activate}><Rocket size={13} /> Activate definition revision</button>}
    </section>}
  </aside>
}
