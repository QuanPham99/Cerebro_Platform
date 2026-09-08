import type { AgentResponse, AgentScope } from './types'

/** Whether this server was started with a trusted scope, and therefore a chat. */
export async function getAgentStatus(signal?: AbortSignal): Promise<'ready' | 'disabled'> {
  const response = await fetch('/api/health', { signal })
  if (!response.ok) return 'disabled'
  const payload = (await response.json()) as { agent?: string }
  return payload.agent === 'ready' ? 'ready' : 'disabled'
}

export async function getAgentScope(signal?: AbortSignal): Promise<AgentScope> {
  const response = await fetch('/api/agent/scope', { signal })
  if (!response.ok) throw new Error(`Agent scope unavailable (${response.status})`)
  return (await response.json()) as AgentScope
}

/**
 * Ask one question. The body carries no scope on purpose: authority belongs to
 * the server's startup configuration, never to the caller.
 */
export async function askAgent(
  question: string,
  maxRows: number,
  signal?: AbortSignal,
): Promise<AgentResponse> {
  const response = await fetch('/api/agent/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, max_rows: maxRows }),
    signal,
  })
  const payload = (await response.json()) as AgentResponse
  if (!response.ok && !('status' in payload)) {
    throw new Error(`Agent request failed (${response.status})`)
  }
  return payload
}
