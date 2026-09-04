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
  review_state?: string
  provider?: string | null
  model?: string | null
  source_mode?: 'configured' | 'database_only'
  discovery_evidence?: Record<string, string | number | boolean>
}

export interface RuntimeStatus {
  llm_configured: boolean
  provider_id: string
  provider_name: string
  model: string | null
  base_url: string
  response_mode: string
  llm_timeout_seconds: number
  llm_max_output_tokens: number
  resolved_response_mode?: string | null
  api_key_configured: boolean
  embedding_model: string | null
  database_configured: boolean
  database_reachable: boolean
  database_schema: string
  query_row_limit: number
  query_timeout_seconds: number
  bundle: string
  semantic_version: string
  generation_mode: string
  review_state: string
  chat_ready: boolean
}

export interface AgentTrace {
  agent: string
  status: 'completed' | 'blocked' | 'skipped'
  summary: string
}

export interface ChatResponse {
  conversation_id: string
  status: 'answered' | 'clarification' | 'blocked'
  answer: string
  sql: string | null
  columns: string[]
  rows: Array<Array<string | number | boolean | null>>
  row_count: number
  truncated: boolean
  semantic_version: string
  evidence_ids: string[]
  warnings: string[]
  trace: AgentTrace[]
}

export type GenerationStage =
  | 'source_check'
  | 'catalog_scan'
  | 'business_semantics'
  | 'relationship_semantics'
  | 'query_semantics'
  | 'compile_okf'
  | 'validate_candidate'
  | 'candidate_ready'

export interface GenerationEvent {
  sequence: number
  stage: GenerationStage
  status: 'started' | 'completed' | 'skipped' | 'failed'
  summary: string
  command: string
  timestamp: string
  details: Record<string, string | number | boolean>
}

export interface GenerationCandidate {
  name: string
  version: string
  counts: Record<string, number>
  generation_mode: 'live' | 'fallback'
  provider: string | null
  model: string | null
  source_mode: 'configured' | 'database_only'
  discovery_evidence: Record<string, string | number | boolean>
  review_state: 'candidate' | 'approved' | 'rejected'
  review_record: ReviewRecord | null
}

export interface ReviewRecord {
  run_id: string
  decision: 'approve' | 'reject'
  reviewer: string
  comment: string
  acknowledge_ai_risk: boolean
  reviewed_at: string
  candidate_digest: string
  reviewed_bundle: string | null
  reviewed_digest: string | null
}

export interface GenerationRun {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  created_at: string
  updated_at: string
  events: GenerationEvent[]
  candidate: GenerationCandidate | null
  error: { code: string; message: string; type?: string } | null
  source_mode: 'configured' | 'database_only'
}
