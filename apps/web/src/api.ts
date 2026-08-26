import type { BundleInfo, GraphResponse, SemanticObject } from './types'

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal })
  if (!response.ok) throw new Error(`Semantic API returned ${response.status}`)
  return response.json() as Promise<T>
}

export const getBundle = (signal?: AbortSignal) => request<BundleInfo>('/api/bundles/active', signal)
export const getGraph = (signal?: AbortSignal) => request<GraphResponse>('/api/graph', signal)
export const getConcept = (id: string, signal?: AbortSignal) =>
  request<SemanticObject>(`/api/concepts/${encodeURIComponent(id)}`, signal)

