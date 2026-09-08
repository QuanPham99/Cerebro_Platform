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

// --- Text-to-SQL agent -------------------------------------------------------
// These mirror the value-free response contract. Resolved literals and bound
// parameter values are excluded server side, so no field here can carry one.

export interface GroundingUsage {
  object_ids: string[]
  relationship_ids: string[]
  governed_literal_ids: string[]
}

export interface BudgetUsage {
  semantic_calls: number
  semantic_call_capacity: number
  planned_ir_authorized: boolean
  transport_attempts: number
  elapsed_ms: number
}

export interface SqlArtifact {
  sql: string
  sql_sha256: string
  parameter_count: number
  parameter_types: string[]
  compiler_version: string
  dialect: string
}

export interface QueryResult {
  columns: string[]
  column_types: string[]
  rows: Array<Array<string | number | boolean | null>>
  row_count: number
  truncated: boolean
  elapsed_ms: number
}

export interface ColumnRef {
  table_id: string
  column: string
}

export interface OutputLineage {
  output_name: string
  source_columns: ColumnRef[]
  metric_ids: string[]
  classification: string
}

export interface DisclosureRecord {
  output_name: string
  source_columns: ColumnRef[]
  classification: string
  row_limit: number
}

export interface CheckViolation {
  code: string
  stage: string
  subject_ids: string[]
}

interface AgentResponseBase {
  generation_route: 'default_ir' | 'planned_ir' | 'none'
  cache_status: 'disabled' | 'miss' | 'hit'
  grounding_usage: GroundingUsage
  budget_usage: BudgetUsage
  violations: CheckViolation[]
  semantic_version: string
  policy_version: string
}

export interface AgentOk extends AgentResponseBase {
  status: 'ok'
  sql_artifact: SqlArtifact
  result: QueryResult
  output_lineage: OutputLineage[]
  disclosures: DisclosureRecord[]
}

export interface AgentRefused extends AgentResponseBase {
  status: 'refused'
  reason: string
  policy_ids: string[]
  unsupported_operator_ids: string[]
  ambiguities: Array<{ ambiguity_id: string; start: number; end: number }>
  literal_needs: Array<{ issue: string; expected_type: string }>
  unmet_needs: Array<Record<string, unknown>>
}

export interface AgentCheckFailed extends AgentResponseBase {
  status: 'check_failed'
  sql_artifact: SqlArtifact | null
}

export interface AgentError {
  status: 'error'
  code: string
}

export type AgentResponse = AgentOk | AgentRefused | AgentCheckFailed | AgentError

export interface AgentScope {
  policy_version: string
  authorization_scope_hash: string
  allowed_object_ids: string[]
  allowed_classifications: string[]
  provider: string
  model: string
  dialect: string
  max_rows_default: number
}
