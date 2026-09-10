import type { BundleActivation, BundleInfo, BundleVersionCatalog, ChatResponse, DefinitionContext, DefinitionPayload, DefinitionRevision, DefinitionScope, DefinitionTranslation, GenerationRun, GenerationTrace, GraphResponse, ReviewRecord, RuntimeStatus, SemanticObject } from './types'

async function request<T>(path: string, signal?: AbortSignal, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, signal })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string | { message?: string; code?: string } } | null
    const detail = payload?.detail
    if (response.status === 404 && detail === 'Not Found') {
      throw new Error('This Semantic API route is unavailable. Restart the Cerebro API to load the current graph-saving endpoints.')
    }
    throw new Error(
      (typeof detail === 'string' ? detail : detail?.message || detail?.code)
      || `Semantic API returned ${response.status}`,
    )
  }
  return response.json() as Promise<T>
}

export const getBundle = (signal?: AbortSignal) => request<BundleInfo>('/api/bundles/active', signal)
export const getGraph = (signal?: AbortSignal) => request<GraphResponse>('/api/graph', signal)
export const getGoldenBundle = (signal?: AbortSignal) => request<BundleInfo>('/api/bundles/golden', signal)
export const getGoldenGraph = (signal?: AbortSignal) => request<GraphResponse>('/api/golden/graph', signal)
export const getGoldenObject = (id: string, signal?: AbortSignal) =>
  request<SemanticObject>(`/api/golden/objects/${encodeURIComponent(id)}`, signal)
export const getConcept = (id: string, signal?: AbortSignal) =>
  request<SemanticObject>(`/api/concepts/${encodeURIComponent(id)}`, signal)
export const getBundleVersions = (signal?: AbortSignal) => request<BundleVersionCatalog>('/api/bundles', signal)
export const getBundleVersionGraph = (bundleId: string, signal?: AbortSignal) =>
  request<GraphResponse>(`/api/bundles/${encodeURIComponent(bundleId)}/graph`, signal)
export const getBundleVersionObject = (bundleId: string, id: string, signal?: AbortSignal) =>
  request<SemanticObject>(`/api/bundles/${encodeURIComponent(bundleId)}/objects/${encodeURIComponent(id)}`, signal)
export const setDefaultBundle = (bundleId: string, signal?: AbortSignal) =>
  request<BundleActivation>('/api/bundles/default', signal, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bundle_id: bundleId }),
  })
export const getRuntimeStatus = (signal?: AbortSignal) => request<RuntimeStatus>('/api/runtime/status', signal)
export const startGeneration = (sourceMode: 'configured' | 'database_only', signal?: AbortSignal) => request<GenerationRun>('/api/generation/runs', signal, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ source_mode: sourceMode }),
})
export const getGeneration = (runId: string, signal?: AbortSignal) =>
  request<GenerationRun>(`/api/generation/runs/${encodeURIComponent(runId)}`, signal)
export const getGenerationTrace = (runId: string, signal?: AbortSignal) =>
  request<GenerationTrace>(`/api/generation/runs/${encodeURIComponent(runId)}/trace`, signal)
export const getGenerationGraph = (runId: string, signal?: AbortSignal) =>
  request<GraphResponse>(`/api/generation/runs/${encodeURIComponent(runId)}/graph`, signal)
export const getGenerationConcept = (runId: string, id: string, signal?: AbortSignal) =>
  request<SemanticObject>(`/api/generation/runs/${encodeURIComponent(runId)}/concepts/${encodeURIComponent(id)}`, signal)
export const generationEventsUrl = (runId: string) => `/api/generation/runs/${encodeURIComponent(runId)}/events`
export const reviewGeneration = (
  runId: string,
  payload: { decision: 'approve' | 'reject'; reviewer: string; comment: string; acknowledge_ai_risk: boolean },
  signal?: AbortSignal,
) => request<ReviewRecord>(`/api/generation/runs/${encodeURIComponent(runId)}/reviews`, signal, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
})
export const activateGeneration = (runId: string, signal?: AbortSignal) =>
  request<{ path: string; name: string; version: string; activated_at: string }>(
    `/api/generation/runs/${encodeURIComponent(runId)}/activate`, signal, { method: 'POST' },
  )
export const getDefinitionContext = (scope: DefinitionScope = {}, signal?: AbortSignal) => {
  const params = new URLSearchParams()
  if (scope.base_bundle_id) params.set('base_bundle_id', scope.base_bundle_id)
  if (scope.revision_id) params.set('revision_id', scope.revision_id)
  const query = params.size ? `?${params.toString()}` : ''
  return request<DefinitionContext>(`/api/definitions/context${query}`, signal)
}
export const translateDefinition = (
  payload: { kind: 'metric' | 'business_rule'; intent: string; entity_id?: string } & DefinitionScope,
  signal?: AbortSignal,
) => request<DefinitionTranslation>('/api/definitions/translate', signal, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
})
export const createDefinitionRevision = (payload: DefinitionPayload, origin: 'declared' | 'ai_proposed', baseBundleId?: string, signal?: AbortSignal) =>
  request<DefinitionRevision>('/api/definition-revisions', signal, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payload, origin, ...(baseBundleId ? { base_bundle_id: baseBundleId } : {}) }),
  })
export const addDefinition = (revisionId: string, payload: DefinitionPayload, origin: 'declared' | 'ai_proposed', signal?: AbortSignal) =>
  request<DefinitionRevision>(`/api/definition-revisions/${encodeURIComponent(revisionId)}/definitions`, signal, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payload, origin }),
  })
export const getDefinitionRevision = (revisionId: string, signal?: AbortSignal) =>
  request<DefinitionRevision>(`/api/definition-revisions/${encodeURIComponent(revisionId)}`, signal)
export const getDefinitionGraph = (revisionId: string, signal?: AbortSignal) =>
  request<GraphResponse>(`/api/definition-revisions/${encodeURIComponent(revisionId)}/graph`, signal)
export const getDefinitionObject = (revisionId: string, id: string, signal?: AbortSignal) =>
  request<SemanticObject>(`/api/definition-revisions/${encodeURIComponent(revisionId)}/objects/${encodeURIComponent(id)}`, signal)
export const reviewDefinitionRevision = (
  revisionId: string,
  payload: { decision: 'approve' | 'reject'; reviewer: string; comment: string; acknowledge_ai_risk: boolean },
  signal?: AbortSignal,
) => request<ReviewRecord>(`/api/definition-revisions/${encodeURIComponent(revisionId)}/reviews`, signal, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
})
export const activateDefinitionRevision = (revisionId: string, signal?: AbortSignal) =>
  request<{ path: string; name: string; version: string; activated_at: string }>(
    `/api/definition-revisions/${encodeURIComponent(revisionId)}/activate`, signal, { method: 'POST' },
  )
export const postChat = (
  payload: { message: string; conversation_id?: string; history: Array<{ role: 'user' | 'assistant'; content: string }> },
  signal?: AbortSignal,
) => request<ChatResponse>('/api/chat', signal, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
})
