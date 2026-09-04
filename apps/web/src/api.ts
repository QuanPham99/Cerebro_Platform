import type { BundleInfo, ChatResponse, GenerationRun, GraphResponse, ReviewRecord, RuntimeStatus, SemanticObject } from './types'

async function request<T>(path: string, signal?: AbortSignal, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, signal })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: { message?: string; code?: string } } | null
    throw new Error(payload?.detail?.message || payload?.detail?.code || `Semantic API returned ${response.status}`)
  }
  return response.json() as Promise<T>
}

export const getBundle = (signal?: AbortSignal) => request<BundleInfo>('/api/bundles/active', signal)
export const getGraph = (signal?: AbortSignal) => request<GraphResponse>('/api/graph', signal)
export const getConcept = (id: string, signal?: AbortSignal) =>
  request<SemanticObject>(`/api/concepts/${encodeURIComponent(id)}`, signal)
export const getRuntimeStatus = (signal?: AbortSignal) => request<RuntimeStatus>('/api/runtime/status', signal)
export const startGeneration = (sourceMode: 'configured' | 'database_only', signal?: AbortSignal) => request<GenerationRun>('/api/generation/runs', signal, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ source_mode: sourceMode }),
})
export const getGeneration = (runId: string, signal?: AbortSignal) =>
  request<GenerationRun>(`/api/generation/runs/${encodeURIComponent(runId)}`, signal)
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
export const postChat = (
  payload: { message: string; conversation_id?: string; history: Array<{ role: 'user' | 'assistant'; content: string }> },
  signal?: AbortSignal,
) => request<ChatResponse>('/api/chat', signal, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
})
