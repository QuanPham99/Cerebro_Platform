import { PROFILE_KINDS, PROFILE_PRESENTATION } from './profilePresentation'
import type { GraphNode } from './types'

export const nodeTypes = PROFILE_KINDS

interface NodeNavigatorProps {
  nodes: GraphNode[]
  visibleIds: Set<string>
  selectedId: string | null
  onSelect: (id: string) => void
}

export function NodeNavigator({ nodes, visibleIds, selectedId, onSelect }: NodeNavigatorProps) {
  const groups = PROFILE_KINDS
    .map((kind) => ({
      kind,
      label: PROFILE_PRESENTATION[kind].plural,
      nodes: nodes.filter((node) => node.profile_kind === kind && visibleIds.has(node.id)),
    }))
    .filter((group) => group.nodes.length > 0)

  return (
    <nav className="node-navigator" aria-label="Visible semantic objects">
      {groups.map((group) => (
        <details className="navigator-group" key={group.kind} open>
          <summary>
            <span className={`type-symbol ${group.kind}`} aria-hidden="true" />
            <strong>{group.label}</strong>
            <em>{group.nodes.length}</em>
            <span className="navigator-chevron" aria-hidden="true" />
          </summary>
          <ul className="navigator-items" aria-label={`${group.label} objects`}>
            {group.nodes.map((node) => (
              <li key={node.id}>
                <button
                  type="button"
                  className={selectedId === node.id ? 'active' : ''}
                  onClick={() => onSelect(node.id)}
                >
                  <span className={`type-dot ${node.profile_kind}`} aria-hidden="true" />
                  <span>{node.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </details>
      ))}
      {groups.length === 0 && <p>No objects match this view.</p>}
    </nav>
  )
}
