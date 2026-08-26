export type NodeType = 'dataset' | 'table' | 'concept' | 'relationship' | 'metric' | 'policy'

export interface GraphNode {
  id: string
  type: NodeType
  label: string
  description: string
  classification: string
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  type: 'physical_fk' | 'relationship_endpoint' | 'semantic_mapping' | 'metric_dependency' | 'policy_coverage'
  label: string
}

export interface GraphResponse {
  version: string
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface SemanticObject extends GraphNode {
  name: string
  status: 'active' | 'draft' | 'deprecated'
  aliases: string[]
  tags: string[]
  links: string[]
  provenance: Record<string, unknown>
  cerebro: Record<string, unknown>
  body: string
  path: string
}

export interface BundleInfo {
  name: string
  version: string
  counts: Record<string, number>
  generation_mode: string
}
