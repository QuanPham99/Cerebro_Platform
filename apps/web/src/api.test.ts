import { afterEach, describe, expect, it, vi } from 'vitest'

import { postChat, SemanticApiError } from './api'
import type { ChatResponse } from './types'

const chatResponse: ChatResponse = {
  conversation_id: 'conversation-1',
  status: 'answered',
  answer: 'Có 2 khách hàng nữ và 1 khách hàng nam.',
  sql: 'SELECT gender, COUNT(*) FROM customers GROUP BY gender',
  columns: ['gender', 'count_star()'],
  rows: [['Female', 2], ['Male', 1]],
  row_count: 2,
  truncated: false,
  semantic_version: '0.2.0',
  evidence_ids: ['metric.customer-count'],
  warnings: [],
  trace: [],
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('chat API diagnostics', () => {
  it('logs the user message and complete Semantic API response with one request ID', async () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(chatResponse), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })))

    const response = await postChat({
      message: 'Có bao nhiêu khách hàng theo từng giới tính?',
      request_id: 'request-1',
      history: [],
    })

    expect(response).toEqual(chatResponse)
    expect(info).toHaveBeenNthCalledWith(1, '[Cerebro chat] User request', expect.objectContaining({
      requestId: 'request-1',
      message: 'Có bao nhiêu khách hàng theo từng giới tính?',
      historyCount: 0,
    }))
    expect(info).toHaveBeenNthCalledWith(2, '[Cerebro chat] Semantic API response', expect.objectContaining({
      requestId: 'request-1',
      elapsedMs: expect.any(Number),
      response: chatResponse,
    }))
  })

  it('surfaces and logs the Kong error body and request ID instead of a generic 502', async () => {
    vi.spyOn(console, 'info').mockImplementation(() => undefined)
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      message: 'An invalid response was received from the upstream server',
      request_id: 'kong-request-1',
    }), {
      status: 502,
      headers: { 'Content-Type': 'application/json' },
    })))

    const rejected = postChat({
      message: 'Có bao nhiêu khách hàng theo từng giới tính?',
      request_id: 'request-2',
      history: [],
    })

    await expect(rejected).rejects.toThrow(
      'Semantic API returned 502: An invalid response was received from the upstream server (request ID: kong-request-1)',
    )
    await expect(rejected).rejects.toBeInstanceOf(SemanticApiError)
    expect(error).toHaveBeenCalledWith('[Cerebro chat] Semantic API error', expect.objectContaining({
      requestId: 'request-2',
      httpStatus: 502,
      gatewayRequestId: 'kong-request-1',
      response: {
        message: 'An invalid response was received from the upstream server',
        request_id: 'kong-request-1',
      },
    }))
  })
})
