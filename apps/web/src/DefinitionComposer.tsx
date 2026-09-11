import { Braces, Check, Clock, Plus, ShieldCheck, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import {
  addDefinition,
  createDefinitionRevision,
  getDefinitionContext,
  reviewDefinitionRevision,
  translateDefinition,
} from './api'
import type {
  BundleVersionSummary,
  DefinitionContext,
  DefinitionKind,
  DefinitionPayload,
  DefinitionRevision,
  DefinitionScope,
  RuntimeStatus,
  SemanticObject,
} from './types'

const classifications = ['public', 'internal', 'confidential', 'restricted'] as const
const aggregations = ['count', 'count_distinct', 'sum', 'avg', 'min', 'max'] as const
const predicateOperators = ['eq', 'neq', 'in', 'not_in', 'gt', 'gte', 'lt', 'lte', 'is_null', 'not_null'] as const
const DRAFT_STORAGE_KEY = 'cerebro.definitionComposerDraft'

type Origin = 'declared' | 'ai_proposed'
type DefinitionRecord = Record<string, unknown>
type AggregateRecord = Record<string, unknown>

function slug(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'new-definition'
}

function record(value: unknown): DefinitionRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as DefinitionRecord : {}
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

function preferredEntity(context: DefinitionContext, selectedObject?: SemanticObject | null) {
  if (selectedObject?.profile_kind === 'entity') return selectedObject.id
  const owner = selectedObject?.cerebro?.entity
  return typeof owner === 'string' && context.entities.some((entity) => entity.id === owner)
    ? owner
    : context.entities[0]?.id || ''
}

function preferredTable(context: DefinitionContext, selectedObject?: SemanticObject | null) {
  return selectedObject?.profile_kind === 'physical_table'
    ? context.tables.find((table) => table.id === selectedObject.id) || context.tables[0]
    : context.tables[0]
}

function aggregate(context: DefinitionContext, selectedObject?: SemanticObject | null): AggregateRecord {
  const table = preferredTable(context, selectedObject)
  return {
    kind: 'aggregate',
    aggregation: 'count_distinct',
    source: { table: table?.id || '', column: table?.columns[0]?.name || '' },
    predicates: [],
  }
}

function manualDefinition(
  kind: DefinitionKind,
  context: DefinitionContext,
  selectedObject?: SemanticObject | null,
): DefinitionPayload {
  const entity = preferredEntity(context, selectedObject)
  const table = preferredTable(context, selectedObject)
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
      measure: aggregate(context, selectedObject),
      dependencies: table ? [table.id] : [],
      grain: { type: 'aggregate', description: 'Requested compatible dimensions', key: [] },
      compatible_dimensions: [],
      time_dimension: null,
      relative_time_anchor: null,
    } }
  }
  return { kind, definition: {
    ...common,
    rule_kind: 'predicate',
    output_type: 'boolean',
    dependencies: table ? [table.id] : [],
    logic: 'Describe the governed rule.',
    grain: { type: 'entity', description: 'One entity', key: [] },
  } }
}

function measureTables(measure: DefinitionRecord): string[] {
  const terms = measure.kind === 'ratio'
    ? [record(measure.numerator), record(measure.denominator)]
    : [measure]
  const ids = terms.flatMap((term) => {
    const source = record(term.source)
    const predicates = Array.isArray(term.predicates) ? term.predicates.map(record) : []
    return [source.table, ...predicates.map((predicate) => record(predicate.source).table)]
      .filter((value): value is string => typeof value === 'string' && Boolean(value))
  })
  return [...new Set(ids)]
}

function definitionErrors(payload: DefinitionPayload | null): string[] {
  if (!payload) return []
  const value = payload.definition
  const errors: string[] = []
  if (!String(value.name || '').trim()) errors.push('Name is required.')
  if (!String(value.id || '').trim()) errors.push('ID is required.')
  if (!String(value.description || '').trim()) errors.push('Description is required.')
  if (!String(value.entity || '').trim()) errors.push('Owning entity is required.')
  const grain = record(value.grain)
  if (!String(grain.type || '').trim() || !String(grain.description || '').trim()) errors.push('Grain type and description are required.')
  if (payload.kind === 'metric') {
    const measure = record(value.measure)
    const terms = measure.kind === 'ratio' ? [record(measure.numerator), record(measure.denominator)] : [measure]
    if (terms.some((term) => !String(term.aggregation || '').trim())) errors.push('Every measure term needs an aggregation.')
    if (terms.some((term) => {
      const source = record(term.source)
      return !String(source.table || '').trim() || !String(source.column || '').trim()
    })) errors.push('Every measure term needs a source table and column.')
  } else {
    if (!String(value.logic || '').trim()) errors.push('Rule logic is required.')
    if (strings(value.dependencies).length === 0) errors.push('Select at least one rule dependency.')
  }
  return errors
}

function CheckList({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: Array<{ id: string; name: string }>
  value: string[]
  onChange: (value: string[]) => void
}) {
  return <fieldset className="definition-checklist"><legend>{label}</legend>
    {options.length === 0
      ? <small>No compatible objects in this graph.</small>
      : options.map((option) => <label key={option.id}><input type="checkbox" checked={value.includes(option.id)} onChange={(event) => onChange(event.target.checked ? [...value, option.id] : value.filter((item) => item !== option.id))} /><span>{option.name}<code>{option.id}</code></span></label>)}
  </fieldset>
}

function AggregateEditor({
  title,
  value,
  context,
  onChange,
}: {
  title: string
  value: AggregateRecord
  context: DefinitionContext
  onChange: (value: AggregateRecord) => void
}) {
  const source = record(value.source)
  const table = context.tables.find((item) => item.id === source.table) || context.tables[0]
  const predicates = Array.isArray(value.predicates) ? value.predicates.map(record) : []
  const updateSource = (next: DefinitionRecord) => onChange({ ...value, source: { ...source, ...next } })
  const updatePredicate = (index: number, next: DefinitionRecord) => {
    const updated = predicates.map((item, itemIndex) => itemIndex === index ? { ...item, ...next } : item)
    onChange({ ...value, predicates: updated })
  }

  return <section className="aggregate-editor">
    <header><strong>{title}</strong><code>Aggregate</code></header>
    <label>Aggregation<select aria-label={`${title} aggregation`} value={String(value.aggregation || 'count_distinct')} onChange={(event) => onChange({ ...value, aggregation: event.target.value })}>{aggregations.map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</select></label>
    <div className="definition-pair">
      <label>Source table<select aria-label={`${title} source table`} value={String(source.table || '')} onChange={(event) => { const nextTable = context.tables.find((item) => item.id === event.target.value); updateSource({ table: event.target.value, column: nextTable?.columns[0]?.name || '' }) }}>{context.tables.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>Source column<select aria-label={`${title} source column`} value={String(source.column || '')} onChange={(event) => updateSource({ column: event.target.value })}>{table?.columns.map((column) => <option key={column.name} value={column.name}>{column.name}</option>)}</select></label>
    </div>
    <div className="predicate-heading"><span>Filters</span><button type="button" onClick={() => onChange({ ...value, predicates: [...predicates, { source: { table: table?.id || '', column: table?.columns[0]?.name || '' }, operator: 'eq', value: '' }] })}><Plus size={12} /> Add filter</button></div>
    {predicates.map((predicate, index) => {
      const predicateSource = record(predicate.source)
      const predicateTable = context.tables.find((item) => item.id === predicateSource.table) || context.tables[0]
      const operator = String(predicate.operator || 'eq')
      return <div className="predicate-row" key={index}>
        <select aria-label={`${title} filter ${index + 1} table`} value={String(predicateSource.table || '')} onChange={(event) => { const nextTable = context.tables.find((item) => item.id === event.target.value); updatePredicate(index, { source: { table: event.target.value, column: nextTable?.columns[0]?.name || '' } }) }}>{context.tables.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        <select aria-label={`${title} filter ${index + 1} column`} value={String(predicateSource.column || '')} onChange={(event) => updatePredicate(index, { source: { ...predicateSource, column: event.target.value } })}>{predicateTable?.columns.map((column) => <option key={column.name} value={column.name}>{column.name}</option>)}</select>
        <select aria-label={`${title} filter ${index + 1} operator`} value={operator} onChange={(event) => updatePredicate(index, { operator: event.target.value })}>{predicateOperators.map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}</select>
        {!['is_null', 'not_null'].includes(operator) && <input aria-label={`${title} filter ${index + 1} value`} value={String(predicate.value ?? '')} onChange={(event) => updatePredicate(index, { value: event.target.value })} placeholder="Value" />}
        <button type="button" aria-label={`Remove ${title} filter ${index + 1}`} onClick={() => onChange({ ...value, predicates: predicates.filter((_, itemIndex) => itemIndex !== index) })}><Trash2 size={12} /></button>
      </div>
    })}
  </section>
}

export function DefinitionComposer({
  runtime,
  baseVersion,
  selectedObject,
  revision,
  onRevision,
  onGraphChange,
  onReviewed,
}: {
  runtime: RuntimeStatus | null
  baseVersion: BundleVersionSummary | null
  selectedObject?: SemanticObject | null
  revision: DefinitionRevision | null
  onRevision: (revision: DefinitionRevision) => void
  onGraphChange: (revision: DefinitionRevision, objectId: string) => void
  onReviewed: (revisionId: string, decision: 'approve' | 'reject') => void
}) {
  const [context, setContext] = useState<DefinitionContext | null>(null)
  const [kind, setKind] = useState<DefinitionKind>('metric')
  const [intent, setIntent] = useState('')
  const [entityId, setEntityId] = useState('')
  const [draft, setDraft] = useState<DefinitionPayload | null>(null)
  const [origin, setOrigin] = useState<Origin>('declared')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reviewer, setReviewer] = useState('')
  const [decision, setDecision] = useState<'approve' | 'reject'>('approve')
  const [comment, setComment] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)

  const scope: DefinitionScope = revision ? { revision_id: revision.id } : baseVersion ? { base_bundle_id: baseVersion.id } : {}
  const scopeKey = revision?.id || baseVersion?.id || 'active'

  useEffect(() => {
    const controller = new AbortController()
    setContext(null)
    setError('')
    getDefinitionContext(scope, controller.signal)
      .then((value) => {
        setContext(value)
        setEntityId(preferredEntity(value, selectedObject))
      })
      .catch((reason: Error) => { if (reason.name !== 'AbortError') setError(reason.message) })
    return () => controller.abort()
  }, [baseVersion?.id, revision?.id])

  useEffect(() => {
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(DRAFT_STORAGE_KEY) || 'null') as { scopeKey?: string; kind?: DefinitionKind; intent?: string; draft?: DefinitionPayload; origin?: Origin } | null
      if (saved?.scopeKey !== scopeKey) return
      if (saved.kind) setKind(saved.kind)
      if (typeof saved.intent === 'string') setIntent(saved.intent)
      if (saved.draft) {
        setDraft(saved.draft)
        setEntityId(String(saved.draft.definition.entity || ''))
      }
      if (saved.origin) setOrigin(saved.origin)
    } catch { window.sessionStorage.removeItem(DRAFT_STORAGE_KEY) }
  }, [scopeKey])

  useEffect(() => {
    if (!draft && !intent) {
      window.sessionStorage.removeItem(DRAFT_STORAGE_KEY)
      return
    }
    window.sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ scopeKey, kind, intent, draft, origin }))
  }, [draft, intent, kind, origin, scopeKey])

  const definition = draft?.definition || null
  const errors = useMemo(() => definitionErrors(draft), [draft])
  const visibleDimensions = useMemo(() => context?.dimensions.filter((dimension) => !entityId || !dimension.entity || dimension.entity === entityId) || [], [context, entityId])
  const ruleDependencies = useMemo(() => context ? [
    ...context.tables,
    ...context.entities,
    ...context.dimensions,
    ...(context.metrics || []),
    ...(context.business_rules || []),
  ].filter((item) => item.id !== definition?.id) : [], [context, definition?.id])

  const update = (field: string, value: unknown) => {
    if (!draft) return
    setDraft({ ...draft, definition: { ...draft.definition, [field]: value } })
    if (field === 'entity') setEntityId(String(value))
  }

  const updateGrain = (field: string, value: unknown) => update('grain', { ...record(definition?.grain), [field]: value })

  const translate = async () => {
    if (!intent.trim()) return
    setBusy(true); setError('')
    try {
      const result = await translateDefinition({ kind, intent, ...(entityId ? { entity_id: entityId } : {}), ...scope })
      setDraft(result.payload)
      setEntityId(String(result.payload.definition.entity || entityId))
      setOrigin('ai_proposed')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not translate this definition.')
    } finally { setBusy(false) }
  }

  const startManual = () => {
    if (!context) return
    const next = manualDefinition(kind, context, selectedObject)
    setDraft(next)
    setEntityId(String(next.definition.entity || ''))
    setOrigin('declared')
    setError('')
  }

  const save = async () => {
    if (!draft || errors.length > 0) return
    setBusy(true); setError('')
    try {
      const payload = draft.kind === 'metric'
        ? { ...draft, definition: { ...draft.definition, dependencies: measureTables(record(draft.definition.measure)) } } as DefinitionPayload
        : draft
      const next = revision
        ? await addDefinition(revision.id, payload, origin)
        : await createDefinitionRevision(payload, origin, baseVersion?.id)
      onRevision(next)
      onGraphChange(next, String(payload.definition.id))
      setDraft(null); setIntent('')
      window.sessionStorage.removeItem(DRAFT_STORAGE_KEY)
      const nextContext = await getDefinitionContext({ revision_id: next.id })
      setContext(nextContext)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not add this definition.')
    } finally { setBusy(false) }
  }

  const review = async () => {
    if (!revision) return
    setBusy(true); setError('')
    try {
      const reviewRecord = await reviewDefinitionRevision(revision.id, {
        decision,
        reviewer,
        comment,
        acknowledge_ai_risk: decision === 'approve' && acknowledged,
      })
      onRevision({ ...revision, review_state: decision === 'approve' ? 'approved' : 'rejected', review_record: reviewRecord })
      onReviewed(revision.id, decision)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not record this definition review.')
    } finally { setBusy(false) }
  }

  if (!baseVersion && !revision) return <aside className="definition-composer"><div className="definition-empty"><Clock size={30} /><h2>Loading graph context</h2><p>Definition controls will open when the approved graph version is ready.</p></div></aside>

  return <aside className="definition-composer" aria-label="Definition composer">
    <header>
      <span className="inspector-kicker"><Sparkles size={12} /> Guided definition</span>
      <h2>Add governed meaning</h2>
      <p>Create a metric or business rule against one approved graph. The base stays unchanged until review.</p>
      <div className="definition-base"><span>Base graph</span><strong>{baseVersion?.name || 'Definition draft'}</strong><code>v{revision?.base_version || baseVersion?.version}</code></div>
    </header>

    <div className="definition-kind" role="group" aria-label="Definition type">
      <button className={kind === 'metric' ? 'active' : ''} onClick={() => { setKind('metric'); setDraft(null) }}>Metric</button>
      <button className={kind === 'business_rule' ? 'active' : ''} onClick={() => { setKind('business_rule'); setDraft(null) }}>Business rule</button>
    </div>
    <label>Describe your idea<textarea aria-label="Definition idea" value={intent} onChange={(event) => setIntent(event.target.value)} placeholder={kind === 'metric' ? 'Percentage of late payments by loan type' : 'A loan is non-performing when…'} /></label>
    <label>Entity hint<select value={entityId} onChange={(event) => setEntityId(event.target.value)}><option value="">Let Cerebro resolve it</option>{context?.entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}</select></label>
    <div className="definition-actions"><button disabled={busy || !runtime?.llm_configured || !intent.trim()} onClick={translate}>{busy ? <Clock size={13} /> : <Sparkles size={13} />} Translate with LLM</button><button disabled={busy || !context} onClick={startManual}><Braces size={13} /> Use guided form</button></div>
    {!runtime?.llm_configured && <p className="definition-note">No model provider is configured. Guided manual authoring remains available.</p>}

    {draft && definition && context && <section className="definition-draft">
      <header><span>Typed draft</span><code>{origin === 'ai_proposed' ? 'AI proposed' : 'Manual'}</code></header>
      <label>Name<input aria-label="Name" value={String(definition.name || '')} onChange={(event) => { update('name', event.target.value); update('id', `${kind === 'metric' ? 'metric' : 'rule'}.${slug(event.target.value)}`) }} /></label>
      <label>ID<input aria-label="ID" value={String(definition.id || '')} onChange={(event) => update('id', event.target.value)} /></label>
      <label>Description<textarea aria-label="Description" value={String(definition.description || '')} onChange={(event) => update('description', event.target.value)} /></label>
      <div className="definition-pair">
        <label>Owning entity<select aria-label="Owning entity" value={String(definition.entity || '')} onChange={(event) => update('entity', event.target.value)}>{context.entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}</select></label>
        <label>Classification<select aria-label="Classification" value={String(definition.classification || 'internal')} onChange={(event) => update('classification', event.target.value)}>{classifications.map((value) => <option key={value}>{value}</option>)}</select></label>
      </div>

      {draft.kind === 'metric' ? <>
        <div className="definition-kind measure-kind" role="group" aria-label="Measure type">
          <button className={record(definition.measure).kind !== 'ratio' ? 'active' : ''} onClick={() => update('measure', aggregate(context, selectedObject))}>Aggregate</button>
          <button className={record(definition.measure).kind === 'ratio' ? 'active' : ''} onClick={() => update('measure', { kind: 'ratio', numerator: aggregate(context, selectedObject), denominator: aggregate(context, selectedObject), scale: 100 })}>Ratio</button>
        </div>
        {record(definition.measure).kind === 'ratio' ? <>
          <AggregateEditor title="Numerator" value={record(record(definition.measure).numerator)} context={context} onChange={(value) => update('measure', { ...record(definition.measure), numerator: value })} />
          <AggregateEditor title="Denominator" value={record(record(definition.measure).denominator)} context={context} onChange={(value) => update('measure', { ...record(definition.measure), denominator: value })} />
          <label>Ratio scale<input type="number" aria-label="Ratio scale" value={Number(record(definition.measure).scale ?? 100)} onChange={(event) => update('measure', { ...record(definition.measure), scale: Number(event.target.value) })} /></label>
        </> : <AggregateEditor title="Measure" value={record(definition.measure)} context={context} onChange={(value) => update('measure', value)} />}
        <CheckList label="Compatible dimensions" options={visibleDimensions} value={strings(definition.compatible_dimensions)} onChange={(value) => update('compatible_dimensions', value)} />
        <label>Time dimension<select aria-label="Time dimension" value={String(definition.time_dimension || '')} onChange={(event) => update('time_dimension', event.target.value || null)}><option value="">None</option>{visibleDimensions.filter((dimension) => dimension.semantic_type === 'temporal').map((dimension) => <option key={dimension.id} value={dimension.id}>{dimension.name}</option>)}</select></label>
        <label className="risk-check"><input type="checkbox" checked={definition.relative_time_anchor === 'max_available_date'} onChange={(event) => update('relative_time_anchor', event.target.checked ? 'max_available_date' : null)} /> Anchor relative time to the latest available date.</label>
        <p className="definition-note">Dependencies are derived from the selected measure and filter tables.</p>
      </> : <>
        <div className="definition-pair">
          <label>Rule kind<select aria-label="Rule kind" value={String(definition.rule_kind || 'predicate')} onChange={(event) => update('rule_kind', event.target.value)}><option value="predicate">Predicate</option><option value="classification">Classification</option><option value="time_anchor">Time anchor</option><option value="aggregation_constraint">Aggregation constraint</option></select></label>
          <label>Output type<select aria-label="Output type" value={String(definition.output_type || 'boolean')} onChange={(event) => update('output_type', event.target.value)}><option value="boolean">Boolean</option><option value="category">Category</option><option value="direction">Direction</option><option value="date">Date</option></select></label>
        </div>
        <label>Rule logic<textarea aria-label="Rule logic" value={String(definition.logic || '')} onChange={(event) => update('logic', event.target.value)} /></label>
        <CheckList label="Dependencies" options={ruleDependencies} value={strings(definition.dependencies)} onChange={(value) => update('dependencies', value)} />
      </>}

      <section className="grain-editor">
        <header><strong>Result grain</strong><code>Required</code></header>
        <div className="definition-pair"><label>Type<input aria-label="Grain type" value={String(record(definition.grain).type || '')} onChange={(event) => updateGrain('type', event.target.value)} /></label><label>Key columns<input aria-label="Grain key columns" value={strings(record(definition.grain).key).join(', ')} onChange={(event) => updateGrain('key', event.target.value.split(',').map((item) => item.trim()).filter(Boolean))} /></label></div>
        <label>Description<input aria-label="Grain description" value={String(record(definition.grain).description || '')} onChange={(event) => updateGrain('description', event.target.value)} /></label>
      </section>
      <label>Warnings<textarea aria-label="Warnings" value={strings(definition.warnings).join('\n')} onChange={(event) => update('warnings', event.target.value.split('\n').map((item) => item.trim()).filter(Boolean))} placeholder="One business assumption per line" /></label>
      {errors.length > 0 && <ul className="definition-validation" aria-label="Definition validation">{errors.map((item) => <li key={item}>{item}</li>)}</ul>}
      <button className="definition-save" disabled={busy || errors.length > 0} onClick={save}><Plus size={13} /> Add to graph draft</button>
    </section>}
    {error && <p className="definition-error" role="alert">{error}</p>}
    {revision && <section className="definition-revision">
      <header><Check size={13} /><span><strong>Draft revision</strong><small>{revision.version}</small></span></header>
      <div className="authored-list">{(revision.definitions || []).map((item) => <div key={item.id}><span className={`type-dot ${item.kind}`} /><span><strong>{item.name}</strong><code>{item.id}</code></span></div>)}</div>
      {revision.review_state === 'candidate' && <>
        <label>Reviewer<span className="required-mark"> *</span><input aria-label="Definition reviewer" value={reviewer} onChange={(event) => setReviewer(event.target.value)} /></label>
        <div className="review-decisions"><label><input type="radio" name="definition-review" checked={decision === 'approve'} onChange={() => setDecision('approve')} /> Approve</label><label><input type="radio" name="definition-review" checked={decision === 'reject'} onChange={() => setDecision('reject')} /> Reject</label></div>
        <label>Comment{decision === 'reject' && <span className="required-mark"> *</span>}<textarea aria-label="Definition review comment" value={comment} onChange={(event) => setComment(event.target.value)} required={decision === 'reject'} /></label>
        {decision === 'approve' && <label className="risk-check"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /> I reviewed the authored definitions and acknowledge AI risk.</label>}
        <button disabled={busy || !reviewer.trim() || (decision === 'approve' && !acknowledged) || (decision === 'reject' && !comment.trim())} onClick={review}><ShieldCheck size={13} /> {decision === 'approve' ? 'Approve Graph' : 'Record rejection'}</button>
      </>}
    </section>}
  </aside>
}
