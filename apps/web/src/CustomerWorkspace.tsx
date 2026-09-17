import { type FormEvent, useEffect, useRef, useState } from 'react'
import { AlertTriangle, ChevronRight, Clock, ListChecks, Network, Send, Square, User } from 'lucide-react'
import { cancelChat, postChat } from './api'
import { ResultPanel } from './ResultPanel'
import type { ChatResponse } from './types'

interface CustomerUser {
  id: string
  label: string
  customerId: string
  questions: string[]
}

// Five simulated logins tied to real customer_ids from the bank-workshop database (not
// fictional personas) — see specs/024-customer-self-service-workspace.md for how these
// were picked. Selecting one in the dropdown below sets the active customer_id for every
// request; SQLGuardrail then scopes every query to that id's own rows.
const CUSTOMER_USERS: CustomerUser[] = [
  {
    id: 'user-1',
    label: 'Manoj Garcia',
    customerId: '46980',
    questions: [
      'Số dư tài khoản hiện tại của tôi là bao nhiêu?',
      'Tôi đã chi tiêu bao nhiêu trong 30 ngày qua?',
      'Giao dịch gần đây nhất của tôi là gì?',
      'Tôi đã nhận được bao nhiêu tiền chuyển vào trong tháng này?',
      'Tài khoản của tôi được mở từ khi nào?',
      'Giao dịch nào của tôi có số tiền lớn nhất trong 3 tháng qua?',
    ],
  },
  {
    id: 'user-2',
    label: 'James Bose',
    customerId: '3881',
    questions: [
      'Khoản vay của tôi còn nợ bao nhiêu?',
      'Tôi có từng thanh toán trễ hạn khoản vay không?',
      'Lãi suất khoản vay của tôi là bao nhiêu?',
      'Khoản vay của tôi sẽ đáo hạn khi nào?',
      'Tôi đã thanh toán được bao nhiêu kỳ cho khoản vay?',
      'Lần thanh toán gần nhất của tôi là khi nào và tôi đã trả bao nhiêu?',
    ],
  },
  {
    id: 'user-3',
    label: 'Neha Reddy',
    customerId: '18465',
    questions: [
      'Các giao dịch thẻ gần đây của tôi là gì?',
      'Tôi đã chi bao nhiêu qua thẻ theo từng danh mục trong tháng này?',
      'Thẻ của tôi có giao dịch nào bị nghi ngờ gian lận không?',
      'Thẻ của tôi có đang hoạt động không?',
      'Tổng số tiền giao dịch thẻ của tôi theo từng danh mục trong 7 ngày qua là bao nhiêu?',
      'Giao dịch thẻ nào của tôi có số tiền lớn nhất?',
    ],
  },
  {
    id: 'user-4',
    label: 'Linda Patel',
    customerId: '52111',
    questions: [
      'Lần thanh toán gần nhất của khoản vay của tôi là khi nào?',
      'Tôi có từng thanh toán trễ hạn khoản vay nào không?',
      'Tổng số tiền tôi đã trả cho khoản vay đến nay là bao nhiêu?',
      'Số tiền vay ban đầu của khoản vay của tôi là bao nhiêu?',
      'Tôi đã thanh toán trễ hạn bao nhiêu kỳ trong tổng số các kỳ đã thanh toán?',
      'Khoản vay của tôi được giải ngân từ khi nào?',
    ],
  },
  {
    id: 'user-5',
    label: 'Priya Menon',
    customerId: '35825',
    questions: [
      'Tổng số dư của tất cả tài khoản của tôi là bao nhiêu?',
      'Tôi có bao nhiêu tài khoản đang hoạt động?',
      'Giao dịch nào của tôi có số tiền lớn nhất trong 90 ngày qua?',
      'Số dư của từng tài khoản của tôi hiện tại là bao nhiêu?',
      'Những giao dịch gần đây nhất của tôi trên mỗi tài khoản, xét trong 30 ngày qua, là gì?',
      'Tôi có bao nhiêu giao dịch trong 30 ngày qua theo từng tài khoản?',
    ],
  },
]

// Demonstration prompts that fall outside the customer-centric graph (§2.1 of
// specs/024-customer-self-service-workspace.md): another customer's data, internal-only
// branch/employee records, and bank-wide aggregate metrics. These still go through the real
// agent (sendMessage below) — the decline is produced server-side because the customer-scope
// grounding and the SQL guardrail's blocked-table check make these genuinely unanswerable, not
// by short-circuiting the request on the client.
const OUT_OF_SCOPE_QUESTIONS = [
  'Số dư tài khoản của một khách hàng khác là bao nhiêu?',
  'Thông tin nhân viên tại chi nhánh của tôi là gì?',
  'Tỷ lệ gian lận thẻ trung bình của toàn ngân hàng là bao nhiêu?',
]

type ConversationEntry =
  | { role: 'user'; content: string }
  | { role: 'assistant'; question: string; response: ChatResponse }

type ActiveChatRequest = { requestId: string; controller: AbortController }

const CUSTOMER_PENDING_EXPECTED_SECONDS = 20

function CustomerQueryProgress({ elapsed, onStop }: { elapsed: number; onStop: () => void }) {
  const percent = Math.min(94, (elapsed / CUSTOMER_PENDING_EXPECTED_SECONDS) * 100)
  return (
    <article className="chat-message assistant pending">
      <div className="chat-message-head">
        <span>Cerebro</span>
        <button type="button" className="stop-query" onClick={onStop}><Square size={10} />Stop query</button>
      </div>
      <div className="query-progress">
        <p><Clock size={13} /> Looking up your own records…</p>
        <div className="progress-bar" role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}>
          <div className="progress-bar-fill" style={{ width: `${percent}%` }} />
        </div>
        <small>{elapsed.toFixed(1)}s elapsed{elapsed > CUSTOMER_PENDING_EXPECTED_SECONDS ? ' · this one is taking longer than usual' : ''}</small>
      </div>
    </article>
  )
}

export function CustomerWorkspace({ active }: { active: boolean }) {
  const [loggedInUser, setLoggedInUser] = useState<CustomerUser>(CUSTOMER_USERS[0])
  const [entries, setEntries] = useState<ConversationEntry[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [pendingElapsed, setPendingElapsed] = useState(0)
  const [conversationId, setConversationId] = useState<string>()
  const transcriptRef = useRef<HTMLDivElement>(null)
  const activeRequestRef = useRef<ActiveChatRequest | null>(null)

  useEffect(() => {
    if (transcriptRef.current) transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
  }, [entries, pending])

  useEffect(() => {
    if (!pending) return
    setPendingElapsed(0)
    const start = Date.now()
    const timer = window.setInterval(() => setPendingElapsed((Date.now() - start) / 1000), 200)
    return () => window.clearInterval(timer)
  }, [pending])

  useEffect(() => () => {
    const requestInFlight = activeRequestRef.current
    if (!requestInFlight) return
    activeRequestRef.current = null
    void cancelChat(requestInFlight.requestId).catch(() => undefined)
    requestInFlight.controller.abort()
  }, [])

  const stopActiveRequest = () => {
    const requestInFlight = activeRequestRef.current
    if (!requestInFlight) return
    activeRequestRef.current = null
    void cancelChat(requestInFlight.requestId).catch(() => undefined)
    requestInFlight.controller.abort()
    setPending(false)
  }

  const sendMessage = async (message: string) => {
    if (!message || activeRequestRef.current) return
    const previous = entries
    const requestId = crypto.randomUUID()
    const controller = new AbortController()
    activeRequestRef.current = { requestId, controller }
    setEntries([...previous, { role: 'user', content: message }])
    setInput('')
    setPending(true)
    try {
      const history = previous.reduce<Array<{ role: 'user' | 'assistant'; content: string }>>((messages, entry) => {
        if (entry.role === 'user') messages.push({ role: 'user', content: entry.content })
        if (entry.role === 'assistant') messages.push({ role: 'assistant', content: entry.response.answer })
        return messages
      }, []).slice(-10)
      const response = await postChat(
        { message, request_id: requestId, conversation_id: conversationId, history, customer_id: loggedInUser.customerId },
        controller.signal,
      )
      if (activeRequestRef.current?.requestId !== requestId) return
      setConversationId(response.conversation_id)
      setEntries((current) => [...current, { role: 'assistant', question: message, response }])
    } catch (reason) {
      if (activeRequestRef.current?.requestId !== requestId) return
      if (reason instanceof Error && reason.name === 'AbortError') return
      const messageText = reason instanceof Error ? reason.message : 'Chat request failed'
      setEntries((current) => [...current, {
        role: 'assistant',
        question: message,
        response: {
          conversation_id: conversationId || 'error',
          status: 'blocked',
          answer: messageText,
          sql: null,
          columns: [],
          rows: [],
          row_count: 0,
          truncated: false,
          semantic_version: 'unknown',
          evidence_ids: [],
          warnings: [],
          trace: [],
        },
      }])
    } finally {
      if (activeRequestRef.current?.requestId === requestId) {
        activeRequestRef.current = null
        setPending(false)
      }
    }
  }

  const switchUser = (nextId: string) => {
    const next = CUSTOMER_USERS.find((user) => user.id === nextId)
    if (!next) return
    stopActiveRequest()
    setLoggedInUser(next)
    setEntries([])
    setConversationId(undefined)
    setInput('')
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void sendMessage(input.trim())
  }


  return (
    <section className="agent-workspace" hidden={!active}>
      <div className="chat-shell">
        <section className="chat-panel" aria-label="Customer self-service">
          <div className="chat-heading">
            <div className="chat-heading-title"><User size={16} /><span>Customer self-service</span></div>
            <label className="customer-login-select">
              <span className="sr-only">Log in as</span>
              <select value={loggedInUser.id} onChange={(event) => switchUser(event.target.value)}>
                {CUSTOMER_USERS.map((user) => (
                  <option key={user.id} value={user.id}>{user.label} · Customer #{user.customerId}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="chat-transcript" ref={transcriptRef} aria-live="polite">
            {entries.length === 0 && <div className="chat-empty">
              <span><User size={20} /></span>
              <h2>Logged in as {loggedInUser.label}</h2>
              <p>Ask about your own accounts, cards, loans, and transactions.</p>
              <p className="chat-empty-hint">Questions about other customers' data are never answered here — pick a suggestion on the right to get started.</p>
            </div>}
            {entries.map((entry, index) => entry.role === 'user' ? (
              <article className="chat-message user" key={index}><span>You</span><p>{entry.content}</p></article>
            ) : (
              <article className={`chat-message assistant ${entry.response.status}${entry.response.columns.length > 0 ? ' has-results' : ''}`} key={index}>
                <div className="chat-message-head"><span>Cerebro · {entry.response.status}</span></div>
                <p>{entry.response.answer}</p>
                {entry.response.columns.length > 0 && (
                  <ResultPanel columns={entry.response.columns} rows={entry.response.rows} rowCount={entry.response.row_count} truncated={entry.response.truncated} />
                )}
                {entry.response.evidence_ids.length > 0 && <details className="chat-detail"><summary><Network size={13} /> Semantic evidence · {entry.response.semantic_version}</summary><div className="evidence-chips">{entry.response.evidence_ids.map((id) => <code key={id}>{id}</code>)}</div></details>}
                {entry.response.warnings.length > 0 && <details className="chat-detail warning-detail"><summary><AlertTriangle size={13} /> Warnings</summary><ul>{entry.response.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></details>}
              </article>
            ))}
            {pending && <CustomerQueryProgress elapsed={pendingElapsed} onStop={stopActiveRequest} />}
          </div>
          <form className="chat-composer" onSubmit={submit}>
            <label><span className="sr-only">Ask about your own accounts and transactions</span><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit() } }} placeholder="Ask about your own accounts, cards, loans, or transactions…" rows={2} /></label>
            <button type="submit" disabled={!input.trim() || pending} aria-label="Send question"><Send size={17} /></button>
          </form>
        </section>

        <aside className="chat-tools" aria-label="Customer workspace tools">
          <details className="preset-panel" open>
            <summary className="preset-panel-heading rail-toggle">
              <ChevronRight size={12} className="chevron-icon" />
              <ListChecks size={14} />
              <span>Your questions</span>
              {pending && <em className="preset-panel-status">Sending…</em>}
            </summary>
            <div className="preset-levels">
              <section className="preset-level" aria-label={loggedInUser.label}>
                <header><strong>{loggedInUser.label}</strong><span>{loggedInUser.questions.length}</span></header>
                <div className="preset-level-questions">
                  {loggedInUser.questions.map((question) => (
                    <button type="button" key={question} disabled={pending} onClick={() => void sendMessage(question)}>{question}</button>
                  ))}
                </div>
              </section>
            </div>
          </details>
          <details className="preset-panel out-of-scope-panel">
            <summary className="preset-panel-heading rail-toggle">
              <ChevronRight size={12} className="chevron-icon" />
              <AlertTriangle size={14} />
              <span>Out-of-scope examples</span>
            </summary>
            <p className="out-of-scope-note">These are never answered here — try one to see the decline.</p>
            <div className="preset-levels">
              <section className="preset-level" aria-label="Out of scope">
                <div className="preset-level-questions">
                  {OUT_OF_SCOPE_QUESTIONS.map((question) => (
                    <button type="button" key={question} disabled={pending} onClick={() => void sendMessage(question)}>{question}</button>
                  ))}
                </div>
              </section>
            </div>
          </details>
        </aside>
      </div>
    </section>
  )
}
