export type ProfileKind =
  | 'dataset'
  | 'physical_table'
  | 'entity'
  | 'dimension'
  | 'metric'
  | 'business_rule'
  | 'relationship'
  | 'policy'
  | 'legacy_concept'
  | 'generic'

export type NodeType = ProfileKind

export type GraphEdgeType =
  | 'physical_fk'
  | 'relationship_endpoint'
  | 'semantic_mapping'
  | 'entity_mapping'
  | 'dimension_entity'
  | 'dimension_binding'
  | 'metric_entity'
  | 'metric_dimension'
  | 'metric_dependency'
  | 'rule_entity'
  | 'rule_dependency'
  | 'semantic_relationship'
  | 'policy_coverage'

export interface GraphNode {
  id: string
  type: string
  profile_kind: ProfileKind
  label: string
  description: string
  classification: string
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  type: GraphEdgeType
  label: string
}

export interface GraphResponse {
  version: string
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface SemanticObject extends GraphNode {
  name: string
  status: 'stable' | 'active' | 'draft' | 'deprecated'
  aliases: string[]
  tags: string[]
  links: string[]
  sources?: Array<Record<string, unknown>>
  generated?: Record<string, unknown> | null
  verified?: Array<Record<string, unknown>> | Record<string, unknown>
  stale_after?: string | null
  resource?: string | null
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

export interface BundleVersionSummary {
  id: string
  name: string
  version: string
  origin: 'golden' | 'generation' | 'definition'
  is_default: boolean
  review_state: 'approved'
  reviewer: string | null
  reviewed_at: string | null
  parent_version: string | null
  counts: Record<string, number>
  kind_counts: Record<string, number>
  generation_mode: string
  source_mode: 'configured' | 'database_only'
  provider: string | null
  model: string | null
}

export interface BundleVersionCatalog {
  default_id: string | null
  default_change_allowed: boolean
  versions: BundleVersionSummary[]
}

export interface BundleActivation {
  bundle_id: string
  path: string
  name: string
  version: string
  activated_at: string
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
  status: 'started' | 'completed' | 'skipped' | 'failed' | 'degraded'
  summary: string
  command: string
  timestamp: string
  details: Record<string, string | number | boolean>
}

export interface GenerationTraceStep {
  stage: GenerationStage
  actor: 'source' | 'agent' | 'compiler' | 'validator' | 'system'
  agent_id: string | null
  status: 'running' | 'completed' | 'skipped' | 'failed' | 'degraded'
  started_at: string | null
  completed_at: string | null
  summary: string
  command: string
  input: Record<string, unknown> | null
  output: Record<string, unknown> | null
  error: { message: string } | null
}

export interface GenerationTrace {
  run_id: string
  steps: GenerationTraceStep[]
}

export interface GenerationCandidate {
  name: string
  version: string
  counts: Record<string, number>
  generation_mode: 'live' | 'fallback' | 'partial' | 'authored'
  provider: string | null
  model: string | null
  source_mode: 'configured' | 'database_only'
  discovery_evidence: Record<string, string | number | boolean>
  review_state: 'candidate' | 'approved' | 'rejected'
  review_record: ReviewRecord | null
}

export type DefinitionKind = 'metric' | 'business_rule'

export interface DefinitionPayload {
  kind: DefinitionKind
  definition: Record<string, unknown>
}

export interface DefinitionTranslation {
  payload: DefinitionPayload
  warnings: string[]
  provider: string
  model: string
}

export interface DefinitionRevision {
  id: string
  base_bundle_id: string | null
  base_version: string
  version: string
  counts: Record<string, number>
  definitions: Array<{ id: string; name: string; kind: DefinitionKind }>
  generation_mode: 'authored'
  review_state: 'candidate' | 'approved' | 'rejected'
  review_record: ReviewRecord | null
}

export interface DefinitionContext {
  bundle_id: string | null
  version: string
  entities: Array<{ id: string; name: string }>
  dimensions: Array<{ id: string; name: string; entity?: string | null; semantic_type?: string | null }>
  tables: Array<{ id: string; name: string; columns: Array<{ name: string; data_type: string }> }>
  metrics: Array<{ id: string; name: string; entity?: string | null }>
  business_rules: Array<{ id: string; name: string; entity?: string | null }>
}

export interface DefinitionScope {
  base_bundle_id?: string
  revision_id?: string
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
