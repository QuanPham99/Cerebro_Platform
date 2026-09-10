import { AlertTriangle, Braces, Database, ExternalLink, ShieldCheck } from 'lucide-react'
import { PROFILE_PRESENTATION } from './profilePresentation'
import type { SemanticObject } from './types'

function values(value: unknown): string[] {
  if (value === undefined || value === null || value === '') return []
  return Array.isArray(value) ? value.map(formatValue) : [formatValue(value)]
}

function formatValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'object' && value !== null) return JSON.stringify(value)
  return String(value)
}

function normalizeStatus(status: SemanticObject['status']) {
  return status === 'active' ? 'stable' : status
}

function bindings(value: unknown): string[] {
  if (!value || typeof value !== 'object') return []
  if (Array.isArray(value)) return value.flatMap(bindings)
  const item = value as Record<string, unknown>
  const current = item.table && item.column ? [`${String(item.table)}.${String(item.column)}`] : []
  return [...current, ...Object.values(item).flatMap(bindings)]
}

function ValueList({ title, value }: { title: string; value: unknown }) {
  const items = values(value)
  if (items.length === 0) return null
  return <section><h3>{title}</h3><ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>
}

function Contract({ title, rows }: { title: string; rows: Array<[string, unknown]> }) {
  const visible = rows.filter(([, value]) => values(value).length > 0)
  if (visible.length === 0) return null
  return <section><h3>{title}</h3><dl>{visible.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{values(value).join(', ')}</dd></div>)}</dl></section>
}

function ProfileContract({ object }: { object: SemanticObject }) {
  const c = object.cerebro || {}
  const kind = object.profile_kind
  const physicalMapping = c.physical_mapping as Record<string, unknown> | undefined
  const physical = c.physical as Record<string, unknown> | undefined
  const semantic = c.semantic as Record<string, unknown> | undefined
  const joinType = c.join_type as Record<string, unknown> | undefined
  const validation = c.validation as Record<string, unknown> | undefined
  const grain = c.grain as Record<string, unknown> | string | undefined

  if (kind === 'entity') return <>
    <Contract title="Entity contract" rows={[
      ['Physical table', physicalMapping?.table],
      ['Entity key', physicalMapping?.key],
      ['Grain', typeof grain === 'object' ? grain.description : grain],
      ['Grain key', typeof grain === 'object' ? grain.key : undefined],
      ['Aliases', object.aliases],
    ]} />
  </>

  if (kind === 'dimension') return <>
    <Contract title="Dimension contract" rows={[
      ['Owning entity', c.entity],
      ['Data type', c.semantic_type],
      ['Derivation', c.derivation],
      ['Compatible metrics', c.compatible_metrics],
    ]} />
    <ValueList title="Physical bindings" value={(c.physical_mappings as unknown[] | undefined)?.map((item) => {
      const binding = item as Record<string, unknown>
      return `${String(binding.table)}.${String(binding.column)}`
    })} />
  </>

  if (kind === 'metric') return <>
    <Contract title="Metric contract" rows={[
      ['Owning entity', c.entity],
      ['Grain', typeof grain === 'object' ? grain.description : grain],
      ['Compatible dimensions', c.compatible_dimensions],
      ['Time dimension', c.time_dimension],
      ['Relative time anchor', c.relative_time_anchor],
    ]} />
    {c.measure !== undefined && <section><h3>Aggregation or ratio</h3><pre>{JSON.stringify(c.measure, null, 2)}</pre></section>}
    <ValueList title="Source fields" value={bindings(c.measure)} />
    <ValueList title="Filters" value={c.filters} />
    <ValueList title="Dependencies" value={c.dependencies} />
  </>

  if (kind === 'business_rule') return <>
    <Contract title="Business rule contract" rows={[
      ['Owning entity', c.entity],
      ['Rule kind', c.rule_kind],
      ['Output type', c.output_type],
      ['Grain', typeof grain === 'object' ? grain.description : grain],
      ['Classification', c.classification],
    ]} />
    <ValueList title="Dependencies" value={c.dependencies} />
    <ValueList title="Constraints" value={c.logic ?? c.constraints} />
  </>

  if (kind === 'relationship') return <>
    <Contract title="Relationship contract" rows={[
      ['Semantic source', semantic?.from],
      ['Semantic target', semantic?.to],
      ['Physical source', physical?.source ?? (c.source_table && c.source_column ? `${String(c.source_table)}.${String(c.source_column)}` : undefined)],
      ['Physical target', physical?.target ?? (c.target_table && c.target_column ? `${String(c.target_table)}.${String(c.target_column)}` : undefined)],
      ['Cardinality', c.cardinality],
      ['Join type', joinType?.default ?? c.join_type],
      ['Target uniqueness', validation?.target_unique ?? c.target_unique],
      ['Source coverage', validation?.source_fk_coverage ?? c.coverage],
      ['Fanout risk', validation?.fanout ?? c.fanout_risk],
      ['Confidence', c.confidence],
    ]} />
    <ValueList title="Evidence" value={c.evidence} />
  </>

  if (kind === 'physical_table') {
    const columns = Array.isArray(c.columns) ? c.columns as Array<Record<string, unknown>> : []
    return <>
      <Contract title="Physical table contract" rows={[
        ['Schema', c.schema ?? physical?.schema],
        ['Grain', c.grain],
        ['Primary key', c.primary_key],
        ['Classification', c.classification],
      ]} />
      {columns.length > 0 && <section><h3><Braces size={14} /> Fields <span>{columns.length}</span></h3><div className="field-list">{columns.map((column) => <div className="field" key={String(column.name)}><code>{String(column.name)}</code><span>{String(column.data_type)}</span><em>{String(column.classification)}</em></div>)}</div></section>}
    </>
  }

  if (kind === 'policy') return <>
    <Contract title="Policy contract" rows={[
      ['Rule', c.rule],
      ['Scope', c.applies_to],
      ['Confidence', c.confidence],
    ]} />
    <ValueList title="Evidence" value={c.evidence} />
  </>

  return <>
    <Contract title={`${PROFILE_PRESENTATION[kind].label} contract`} rows={[
      ['Grain', typeof grain === 'object' ? grain.description : grain],
      ['Classification', c.classification],
    ]} />
    <ValueList title="Semantic mappings" value={c.maps_to} />
    <ValueList title="Dependencies" value={c.dependencies} />
  </>
}

function MetadataList({ value, empty }: { value: unknown; empty: string }) {
  const items = values(value)
  if (items.length === 0) return <p className="metadata-empty">{empty}</p>
  return <ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
}

export function Inspector({ object, loading, sourceBase = '/knowledge' }: { object: SemanticObject | null; loading: boolean; sourceBase?: string | null }) {
  if (loading) return <aside className="inspector"><div className="skeleton wide" /><div className="skeleton" /><div className="skeleton tall" /></aside>
  if (!object) return <aside className="inspector empty-inspector"><Braces size={30} /><h2>Select a semantic object</h2><p>Follow a semantic object through physical bindings, governed relationships, metrics, rules, and policy.</p></aside>

  const c = object.cerebro || {}
  const generated = object.generated ?? null
  const verified = object.verified ? (Array.isArray(object.verified) ? object.verified : [object.verified]) : []
  const sourceHref = sourceBase ? `${sourceBase}/${object.path.split('/').map(encodeURIComponent).join('/')}` : null

  return (
    <aside className="inspector" aria-live="polite">
      <div className="inspector-kicker"><span className={`type-dot ${object.profile_kind}`} />{PROFILE_PRESENTATION[object.profile_kind].label}<em>{PROFILE_PRESENTATION[object.profile_kind].layer}</em></div>
      <h2>{object.name}</h2>
      <code>{object.id}</code>
      <p className="definition">{object.description}</p>

      <ProfileContract object={object} />
      {values(c.formula).length > 0 && <section><h3>Formula</h3><pre>{String(c.formula)}</pre></section>}
      <ValueList title="Warnings" value={c.warnings} />

      <section><h3><ShieldCheck size={14} /> Governance and provenance</h3><dl>
        <div><dt>Status</dt><dd>{normalizeStatus(object.status)}</dd></div>
        <div><dt>Classification</dt><dd>{String(c.classification || object.classification || 'internal')}</dd></div>
        <div><dt>Raw OKF type</dt><dd>{object.type}</dd></div>
        <div><dt>Provenance</dt><dd>{String(object.provenance.origin || 'declared')}</dd></div>
      </dl></section>
      <section><h3>Sources</h3><MetadataList value={object.sources} empty="No source records." /></section>
      <section><h3>Generated metadata</h3><MetadataList value={generated} empty="Not generated." /></section>
      <section><h3>Verification records</h3><MetadataList value={verified} empty="No verification records." /></section>
      {sourceHref && <a className="source-link" href={sourceHref}><ExternalLink size={14} /> Open OKF source</a>}
    </aside>
  )
}
