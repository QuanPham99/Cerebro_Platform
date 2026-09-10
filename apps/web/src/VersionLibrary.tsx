import { AlertTriangle, Check, Database, Eye, GitBranch, Network, RefreshCw, ShieldCheck, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { BundleVersionCatalog, BundleVersionSummary } from './types'

function displayOrigin(origin: BundleVersionSummary['origin']) {
  if (origin === 'golden') return 'Golden baseline'
  if (origin === 'definition') return 'Definition revision'
  return 'Generated graph'
}

function reviewedDate(value: string | null) {
  if (!value) return 'Checked-in baseline'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function VersionLibrary({
  catalog,
  loading,
  error,
  focusId,
  actioning,
  onPreview,
  onSetDefault,
  onRetry,
  onGenerate,
}: {
  catalog: BundleVersionCatalog | null
  loading: boolean
  error: string
  focusId: string | null
  actioning: boolean
  onPreview: (version: BundleVersionSummary) => void
  onSetDefault: (version: BundleVersionSummary) => void
  onRetry: () => void
  onGenerate: () => void
}) {
  const [pending, setPending] = useState<BundleVersionSummary | null>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const defaultVersion = catalog?.versions.find((version) => version.is_default) || null

  useEffect(() => {
    if (!pending) return
    confirmRef.current?.focus()
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !actioning) setPending(null)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [actioning, pending])

  return (
    <section className="version-library" aria-label="Saved graph versions">
      <header className="version-library-heading">
        <div>
          <span className="version-kicker"><GitBranch size={13} /> Semantic lineage</span>
          <h1>Saved graph versions</h1>
          <p>Every approved graph is preserved as an immutable checkpoint. Choose one checkpoint to govern the workspace.</p>
        </div>
        <button className="version-generate" onClick={onGenerate}><Sparkles size={14} /> Generate new graph</button>
      </header>

      <div className="version-library-rule">
        <span>{catalog?.versions.length || 0} approved versions</span>
        <span><ShieldCheck size={12} /> Approval saves. Default selection activates.</span>
      </div>

      {error && <div className="version-library-error" role="alert"><AlertTriangle size={15} /><span>{error}</span><button onClick={onRetry}><RefreshCw size={13} /> Retry</button></div>}
      {loading && !catalog && <div className="version-library-loading"><RefreshCw size={18} /><span>Reading approved graph history…</span></div>}

      {catalog && <ol className="version-ledger">
        {catalog.versions.map((version, index) => {
          const objectCount = Object.values(version.kind_counts).reduce((sum, count) => sum + count, 0)
          return <li className={`${version.is_default ? 'default' : ''} ${focusId === version.id ? 'focused' : ''}`} key={version.id}>
            <div className="lineage-node" aria-hidden="true"><span>{String(index + 1).padStart(2, '0')}</span><i /></div>
            <article className="version-entry">
              <header>
                <div>
                  <span className={`version-origin ${version.origin}`}>{version.origin === 'golden' ? <Database size={12} /> : <Network size={12} />}{displayOrigin(version.origin)}</span>
                  <h2>{version.name}</h2>
                  <code>v{version.version}</code>
                </div>
                <div className="version-badges">
                  {version.is_default && <span className="default-badge"><Check size={11} /> Default</span>}
                  {version.origin === 'golden' && <span className="golden-badge">Golden</span>}
                </div>
              </header>
              <dl>
                <div><dt>Approved</dt><dd>{reviewedDate(version.reviewed_at)}</dd></div>
                <div><dt>Reviewer</dt><dd>{version.reviewer || 'Cerebro maintainers'}</dd></div>
                <div><dt>Objects</dt><dd>{objectCount}</dd></div>
                <div><dt>Source</dt><dd>{version.source_mode.replace('_', ' ')}</dd></div>
                <div><dt>Mode</dt><dd>{version.generation_mode}</dd></div>
                <div><dt>Model</dt><dd>{version.model || 'None'}</dd></div>
              </dl>
              {version.parent_version && <p className="version-parent"><GitBranch size={11} /> Based on v{version.parent_version}</p>}
              <footer>
                <button onClick={() => onPreview(version)}><Eye size={13} /> View graph</button>
                <button
                  className="set-default"
                  disabled={version.is_default || actioning || !catalog.default_change_allowed}
                  title={!catalog.default_change_allowed ? 'The default is locked by server configuration' : undefined}
                  onClick={() => setPending(version)}
                >{version.is_default ? <><Check size={13} /> Current default</> : <><ShieldCheck size={13} /> Set as default</>}</button>
              </footer>
            </article>
          </li>
        })}
      </ol>}

      {pending && <div className="version-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !actioning) setPending(null) }}>
        <div className="version-dialog" role="dialog" aria-modal="true" aria-labelledby="default-dialog-title" aria-describedby="default-dialog-description">
          <button className="dialog-close" aria-label="Cancel default change" disabled={actioning} onClick={() => setPending(null)}><X size={15} /></button>
          <span className="version-kicker"><ShieldCheck size={13} /> Workspace default</span>
          <h2 id="default-dialog-title">Set v{pending.version} as default?</h2>
          <p id="default-dialog-description">Semantic Constellation, Define, Text to SQL, API, and MCP will switch to this approved graph.</p>
          <div className="default-transition"><span><small>Current</small><code>v{defaultVersion?.version || '—'}</code></span><i>→</i><span><small>Next</small><code>v{pending.version}</code></span></div>
          <footer><button disabled={actioning} onClick={() => setPending(null)}>Keep current</button><button ref={confirmRef} className="confirm-default" disabled={actioning} onClick={() => onSetDefault(pending)}>{actioning ? 'Switching…' : 'Set as default'}</button></footer>
        </div>
      </div>}
    </section>
  )
}
