import { AlertTriangle, Braces, Database, ExternalLink, ShieldCheck } from 'lucide-react'
import type { SemanticObject } from './types'

function values(value: unknown): string[] {
  if (!value) return []
  return Array.isArray(value) ? value.map((item) => (typeof item === 'string' ? item : JSON.stringify(item))) : [String(value)]
}

export function Inspector({ object, loading }: { object: SemanticObject | null; loading: boolean }) {
  if (loading) return <aside className="inspector"><div className="skeleton wide" /><div className="skeleton" /><div className="skeleton tall" /></aside>
  if (!object) {
    return (
      <aside className="inspector empty-inspector">
        <Braces size={30} />
        <h2>Select a semantic object</h2>
        <p>Follow a concept through its tables, joins, metrics, and governance context.</p>
      </aside>
    )
  }
  const c = object.cerebro || {}
  const columns = Array.isArray(c.columns) ? (c.columns as Array<Record<string, unknown>>) : []
  return (
    <aside className="inspector" aria-live="polite">
      <div className="inspector-kicker"><span className={`type-dot ${object.type}`} />{object.type}</div>
      <h2>{object.name}</h2>
      <code>{object.id}</code>
      <p className="definition">{object.description}</p>

      {Boolean(c.grain) && <section><h3><Database size={14} /> Grain</h3><p>{String(c.grain)}</p></section>}
      {columns.length > 0 && (
        <section>
          <h3><Braces size={14} /> Fields <span>{columns.length}</span></h3>
          <div className="field-list">
            {columns.map((column) => <div className="field" key={String(column.name)}><code>{String(column.name)}</code><span>{String(column.data_type)}</span><em>{String(column.classification)}</em></div>)}
          </div>
        </section>
      )}
      {values(c.formula).length > 0 && <section><h3>Formula</h3><pre>{String(c.formula)}</pre></section>}
      {values(c.maps_to).length > 0 && <section><h3>Semantic mappings</h3><ul>{values(c.maps_to).map((item) => <li key={item}>{item}</li>)}</ul></section>}
      {values(c.dependencies).length > 0 && <section><h3>Dependencies</h3><ul>{values(c.dependencies).map((item) => <li key={item}>{item}</li>)}</ul></section>}
      {values(c.warnings).length > 0 && <section className="warning"><h3><AlertTriangle size={14} /> Query guidance</h3><ul>{values(c.warnings).map((item) => <li key={item}>{item}</li>)}</ul></section>}
      <section><h3><ShieldCheck size={14} /> Governance</h3><dl><div><dt>Classification</dt><dd>{String(c.classification || 'internal')}</dd></div><div><dt>Status</dt><dd>{object.status}</dd></div><div><dt>Provenance</dt><dd>{String(object.provenance.origin || 'declared')}</dd></div></dl></section>
      <a className="source-link" href={`/knowledge/${object.path}`}><ExternalLink size={14} /> Open OKF source</a>
    </aside>
  )
}
