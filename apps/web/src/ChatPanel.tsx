import { useCallback, useEffect, useRef, useState } from 'react'
import { CornerDownLeft, Database, Loader2, ShieldAlert, ShieldCheck, Table2, X } from 'lucide-react'
import { askAgent } from './chatApi'
import type { AgentResponse, ColumnRef, OutputLineage } from './types'

/**
 * Each turn is independent. The agent answers one question at a time and keeps
 * no conversation state, so this is a question log rather than a dialogue.
 */
export interface Turn {
  id: number
  question: string
  response: AgentResponse | null
  error: string | null
}

const SENSITIVE = new Set(['confidential', 'restricted'])

/**
 * Faults that happen before or outside query checking. Order matters: the first
 * matching kind wins, so the most specific cause is the one reported.
 */
const FAULT_KINDS: Array<{ codes: string[]; text: string }> = [
  {
    codes: ['provider_configuration_error', 'egress_blocked'],
    text: 'The model gateway is not configured for this server.',
  },
  {
    codes: ['provider_unavailable', 'provider_rejected'],
    text: 'The model did not answer. Nothing was generated, so nothing was checked; ask again.',
  },
  {
    codes: ['unparsable_generation_outcome', 'unparsable_fallback_ir'],
    text: 'The model replied in a shape this contract cannot read, twice. Nothing was generated; ask again.',
  },
  {
    codes: [
      'deadline_exceeded',
      'budget_exceeded',
      'semantic_call_budget_exceeded',
      'token_budget_exceeded',
      'cost_budget_exceeded',
    ],
    text: 'The request ran out of its budget before finishing.',
  },
  {
    codes: ['execution_timeout', 'explain_timeout'],
    text: 'The query was valid but the engine exceeded its time limit.',
  },
]

const REFUSAL_TEXT: Record<string, string> = {
  missing_grounding: 'No authorized object in the bundle answers this.',
  policy_disallowed: 'A governance policy forbids returning this shape of answer.',
  clarification_required: 'The question is ambiguous; name the object or value explicitly.',
  unsupported_complexity: 'This needs an operator the router does not accept.',
}

const columnLabel = (ref: ColumnRef) => `${ref.table_id.replace(/^table\./, '')}.${ref.column}`

export function usedObjectIds(response: AgentResponse | null): Set<string> {
  if (!response || response.status === 'error') return new Set()
  const usage = response.grounding_usage
  return new Set([...usage.object_ids, ...usage.relationship_ids])
}

function LineageChips({ lineage }: { lineage: OutputLineage[] }) {
  if (lineage.length === 0) return null
  return (
    <div className="chat-lineage">
      <span className="chat-section-label"><Database size={11} /> Lineage</span>
      <ul>
        {lineage.map((item) => (
          <li key={item.output_name} className={SENSITIVE.has(item.classification) ? 'sensitive' : ''}>
            <strong>{item.output_name}</strong>
            <em>{item.classification}</em>
            {item.metric_ids.map((id) => <code key={id}>{id}</code>)}
            {item.source_columns.map((ref) => <code key={columnLabel(ref)}>{columnLabel(ref)}</code>)}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Answer({ response }: { response: AgentResponse }) {
  if (response.status === 'error') {
    return <p className="chat-failed"><ShieldAlert size={13} /> The agent could not complete this request ({response.code}).</p>
  }
  const footer = (
    <div className="chat-meta">
      <span>{response.generation_route}</span>
      <span>{response.budget_usage.semantic_calls} model call{response.budget_usage.semantic_calls === 1 ? '' : 's'}</span>
      <span>cache {response.cache_status}</span>
    </div>
  )

  if (response.status === 'refused') {
    return (
      <div className="chat-answer">
        <p className="chat-refused">
          <ShieldAlert size={13} /> {REFUSAL_TEXT[response.reason] ?? response.reason}
        </p>
        {response.policy_ids.length > 0 && (
          <p className="chat-detail">Policy: {response.policy_ids.map((id) => <code key={id}>{id}</code>)}</p>
        )}
        {response.literal_needs.length > 0 && (
          <p className="chat-detail">Needs: {response.literal_needs.map((need) => <code key={need.issue}>{need.issue}</code>)}</p>
        )}
        {footer}
      </div>
    )
  }

  if (response.status === 'check_failed') {
    // A transport or budget fault is not a rejected query: nothing was
    // generated, so blaming the gates would misread a flaky model as a policy
    // decision and send the reader looking for a rule that does not exist.
    const codes = new Set(response.violations.map((item) => item.code))
    const fault = FAULT_KINDS.find((kind) => kind.codes.some((code) => codes.has(code)))
    return (
      <div className="chat-answer">
        <p className="chat-failed">
          <ShieldAlert size={13} /> {fault ? fault.text : 'The generated query did not pass the gates.'}
        </p>
        <p className="chat-detail">
          {response.violations.map((v) => <code key={v.code + v.stage}>{v.code}</code>)}
        </p>
        {footer}
      </div>
    )
  }

  const { result, sql_artifact: artifact, disclosures } = response
  return (
    <div className="chat-answer">
      <div className="chat-result">
        <span className="chat-section-label"><Table2 size={11} /> {result.row_count} row{result.row_count === 1 ? '' : 's'}{result.truncated ? ' (truncated)' : ''}</span>
        {result.row_count === 0 ? (
          <p className="chat-detail">The query ran and matched nothing.</p>
        ) : (
          <div className="chat-table-scroll">
            <table>
              <thead><tr>{result.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
              <tbody>
                {result.rows.map((row, index) => (
                  <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell === null ? '—' : String(cell)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <details className="chat-sql">
        <summary>SQL · {artifact.parameter_count} bound parameter{artifact.parameter_count === 1 ? '' : 's'}</summary>
        <pre>{artifact.sql}</pre>
      </details>
      <LineageChips lineage={response.output_lineage} />
      {disclosures.length > 0 && (
        <div className="chat-disclosure">
          <span className="chat-section-label"><ShieldCheck size={11} /> Disclosed</span>
          <ul>
            {disclosures.map((record) => (
              <li key={record.output_name}>
                <strong>{record.output_name}</strong>
                <em>{record.classification}</em>
                <span>max {record.row_limit} rows</span>
                {record.source_columns.map((ref) => <code key={columnLabel(ref)}>{columnLabel(ref)}</code>)}
              </li>
            ))}
          </ul>
        </div>
      )}
      {footer}
    </div>
  )
}

export function ChatPanel({
  onClose,
  onTurn,
  maxRows = 100,
}: {
  onClose: () => void
  onTurn: (response: AgentResponse | null) => void
  maxRows?: number
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const log = useRef<HTMLDivElement>(null)
  const nextId = useRef(0)

  useEffect(() => {
    // Optional call: `scrollTo` exists on elements in browsers but not in jsdom,
    // and a missing convenience must not break the panel or its tests.
    log.current?.scrollTo?.({ top: log.current.scrollHeight, behavior: 'smooth' })
  }, [turns, pending])

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault()
      const question = draft.trim()
      if (!question || pending) return
      const id = nextId.current++
      setTurns((current) => [...current, { id, question, response: null, error: null }])
      setDraft('')
      setPending(true)
      try {
        const response = await askAgent(question, maxRows)
        setTurns((current) => current.map((turn) => (turn.id === id ? { ...turn, response } : turn)))
        onTurn(response)
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : 'Request failed'
        setTurns((current) => current.map((turn) => (turn.id === id ? { ...turn, error: message } : turn)))
        onTurn(null)
      } finally {
        setPending(false)
      }
    },
    [draft, maxRows, onTurn, pending],
  )

  return (
    <section className="chat-dock" aria-label="Text-to-SQL agent">
      <header>
        <span className="chat-title"><Database size={13} /> Ask the data</span>
        <span className="chat-hint">Each question is answered independently</span>
        <button onClick={onClose} aria-label="Close chat"><X size={14} /></button>
      </header>
      <div className="chat-log" ref={log}>
        {turns.length === 0 && (
          <p className="chat-empty">
            Grounded in the reviewed bundle. Try “What is transaction volume by branch?”
          </p>
        )}
        {turns.map((turn) => (
          <article key={turn.id} className="chat-turn">
            <p className="chat-question">{turn.question}</p>
            {turn.error && <p className="chat-failed"><ShieldAlert size={13} /> {turn.error}</p>}
            {turn.response && <Answer response={turn.response} />}
            {!turn.response && !turn.error && (
              <p className="chat-pending"><Loader2 size={13} className="spin" /> Grounding, compiling, and validating…</p>
            )}
          </article>
        ))}
      </div>
      <form className="chat-composer" onSubmit={submit}>
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask a question about the bank data…"
          aria-label="Question for the Text-to-SQL agent"
          disabled={pending}
        />
        <button type="submit" disabled={pending || draft.trim().length === 0} aria-label="Send question">
          {pending ? <Loader2 size={15} className="spin" /> : <CornerDownLeft size={15} />}
        </button>
      </form>
    </section>
  )
}
