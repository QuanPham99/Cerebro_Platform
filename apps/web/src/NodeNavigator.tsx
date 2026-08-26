import type { GraphNode, NodeType } from './types'

export const nodeTypes: NodeType[] = ['dataset', 'table', 'concept', 'relationship', 'metric', 'policy']

const typeLabels: Record<NodeType, string> = {
  dataset: 'Datasets',
  table: 'Tables',
  concept: 'Concepts',
  relationship: 'Relationships',
  metric: 'Metrics',
  policy: 'Policies',
}

interface NodeNavigatorProps {
  nodes: GraphNode[]
  visibleIds: Set<string>
  selectedId: string | null
  onSelect: (id: string) => void
}

export function NodeNavigator({ nodes, visibleIds, selectedId, onSelect }: NodeNavigatorProps) {
  const groups = nodeTypes
    .map((type) => ({
      type,
      label: typeLabels[type],
      nodes: nodes.filter((node) => node.type === type && visibleIds.has(node.id)),
    }))
    .filter((group) => group.nodes.length > 0)

  return (
    <nav className="node-navigator" aria-label="Visible semantic objects">
      {groups.map((group) => (
        <details className="navigator-group" key={group.type} open>
          <summary>
            <span className={`type-symbol ${group.type}`} aria-hidden="true" />
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
                  <span className={`type-dot ${node.type}`} aria-hidden="true" />
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
