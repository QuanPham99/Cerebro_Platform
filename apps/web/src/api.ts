import type { BundleActivation, BundleInfo, BundleVersionCatalog, ChatResponse, DefinitionContext, DefinitionPayload, DefinitionRevision, DefinitionScope, DefinitionTranslation, GenerationRun, GenerationTrace, GraphResponse, ReportDocument, ReportRun, ReviewRecord, RuntimeStatus, SavedChart, SemanticObject } from './types'

type SemanticApiErrorPayload = {
  detail?: string | { message?: string; code?: string }
  message?: string
  request_id?: string
}

export class SemanticApiError extends Error {
  readonly status: number
  readonly path: string
  readonly requestId?: string
  readonly payload: unknown

  constructor(message: string, status: number, path: string, requestId: string | undefined, payload: unknown) {
    super(message)
    this.name = 'SemanticApiError'
    this.status = status
    this.path = path
    this.requestId = requestId
    this.payload = payload
  }
}

function parseResponseBody(rawBody: string): unknown {
  if (!rawBody) return null
  try {
    return JSON.parse(rawBody)
  } catch {
    return rawBody
  }
}

async function request<T>(path: string, signal?: AbortSignal, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, signal })
  const rawBody = await response.text()
  const parsedBody = parseResponseBody(rawBody)
  if (!response.ok) {
    const payload = (
      parsedBody && typeof parsedBody === 'object' ? parsedBody : null
    ) as SemanticApiErrorPayload | null
    const detail = payload?.detail
    if (response.status === 404 && detail === 'Not Found') {
      throw new Error('This Semantic API route is unavailable. Restart the Cerebro API to load the current graph-saving endpoints.')
    }
    const upstreamMessage = (
      typeof detail === 'string' ? detail : detail?.message || detail?.code || payload?.message
    )
    const requestId = payload?.request_id
      || response.headers.get('x-kong-request-id')
      || response.headers.get('x-cerebro-request-id')
      || undefined
    const summary = upstreamMessage
      ? `Semantic API returned ${response.status}: ${upstreamMessage}`
      : `Semantic API returned ${response.status}`
    throw new SemanticApiError(
      requestId ? `${summary} (request ID: ${requestId})` : summary,
      response.status,
      path,
      requestId,
      parsedBody,
    )
  }
  return parsedBody as T
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
export const deleteBundleVersion = (bundleId: string, signal?: AbortSignal) =>
  request<{ deleted: string }>(`/api/bundles/${encodeURIComponent(bundleId)}`, signal, { method: 'DELETE' })
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

export const startReport = (reportRequest: string, signal?: AbortSignal) =>
  request<ReportRun>('/api/reports/runs', signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request: reportRequest }),
  })
export const getReportRun = (runId: string, signal?: AbortSignal) =>
  request<ReportRun>(`/api/reports/runs/${encodeURIComponent(runId)}`, signal)
export const getReportDocument = (runId: string, signal?: AbortSignal) =>
  request<ReportDocument>(`/api/reports/runs/${encodeURIComponent(runId)}/document`, signal)
export const cancelReport = (runId: string, signal?: AbortSignal) =>
  request<{ run_id: string; status: string }>(`/api/reports/runs/${encodeURIComponent(runId)}/cancel`, signal, { method: 'POST' })
export const reportEventsUrl = (runId: string) => `/api/reports/runs/${encodeURIComponent(runId)}/events`
export const reportPdfUrl = (runId: string) => `/api/reports/runs/${encodeURIComponent(runId)}/pdf`
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
export const postChat = async (
  payload: { message: string; request_id?: string; conversation_id?: string; history: Array<{ role: 'user' | 'assistant'; content: string }> },
  signal?: AbortSignal,
) => {
  const startedAt = performance.now()
  console.info('[Cerebro chat] User request', {
    requestId: payload.request_id,
    conversationId: payload.conversation_id,
    message: payload.message,
    historyCount: payload.history.length,
  })
  try {
    const response = await request<ChatResponse>('/api/chat', signal, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    console.info('[Cerebro chat] Semantic API response', {
      requestId: payload.request_id,
      elapsedMs: Math.round(performance.now() - startedAt),
      response,
    })
    return response
  } catch (reason) {
    console.error('[Cerebro chat] Semantic API error', {
      requestId: payload.request_id,
      elapsedMs: Math.round(performance.now() - startedAt),
      error: reason,
      ...(reason instanceof SemanticApiError ? {
        httpStatus: reason.status,
        gatewayRequestId: reason.requestId,
        response: reason.payload,
      } : {}),
    })
    throw reason
  }
}

export const cancelChat = (requestId: string) =>
  request<{ request_id: string; status: 'cancellation_requested' }>(
    `/api/chat/requests/${encodeURIComponent(requestId)}/cancel`,
    undefined,
    { method: 'POST' },
  )

export const listSavedCharts = (signal?: AbortSignal) => request<SavedChart[]>('/api/saved-charts', signal)
export const saveChart = (
  payload: { question: string; sql: string | null; columns: string[]; rows: Array<Array<string | number | boolean | null>>; row_count: number; truncated: boolean },
  signal?: AbortSignal,
) => request<SavedChart>('/api/saved-charts', signal, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
})
export const deleteSavedChart = (chartId: string, signal?: AbortSignal) =>
  request<{ deleted: string }>(`/api/saved-charts/${encodeURIComponent(chartId)}`, signal, { method: 'DELETE' })
