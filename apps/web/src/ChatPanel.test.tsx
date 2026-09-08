import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatPanel, usedObjectIds } from './ChatPanel'
import type { AgentResponse } from './types'

const base = {
  generation_route: 'default_ir' as const,
  cache_status: 'miss' as const,
  grounding_usage: {
    object_ids: ['table.transactions', 'metric.transaction-volume'],
    relationship_ids: ['relationship.transaction_account'],
    governed_literal_ids: [],
  },
  budget_usage: {
    semantic_calls: 1,
    semantic_call_capacity: 1,
    planned_ir_authorized: false,
    transport_attempts: 1,
    elapsed_ms: 900,
  },
  violations: [],
  semantic_version: '0.1.0',
  policy_version: 'policy.v1',
}

const ok: AgentResponse = {
  ...base,
  status: 'ok',
  sql_artifact: {
    sql: 'SELECT "branch_name", SUM("amount") FROM transactions LIMIT ?',
    sql_sha256: 'a'.repeat(64),
    parameter_count: 1,
    parameter_types: ['integer'],
    compiler_version: '008.compiler.v1',
    dialect: 'duckdb',
  },
  result: {
    columns: ['branch_name', 'transaction_volume'],
    column_types: ['string', 'decimal'],
    rows: [['Pune Branch 9', 164334052.47]],
    row_count: 1,
    truncated: false,
    elapsed_ms: 120,
  },
  output_lineage: [
    {
      output_name: 'annual_income',
      source_columns: [{ table_id: 'table.customers', column: 'annual_income' }],
      metric_ids: [],
      classification: 'confidential',
    },
  ],
  disclosures: [
    {
      output_name: 'annual_income',
      source_columns: [{ table_id: 'table.customers', column: 'annual_income' }],
      classification: 'confidential',
      row_limit: 5,
    },
  ],
}

const refused: AgentResponse = {
  ...base,
  status: 'refused',
  reason: 'policy_disallowed',
  policy_ids: ['policy.sensitive-banking-data'],
  unsupported_operator_ids: [],
  ambiguities: [],
  literal_needs: [],
  unmet_needs: [],
}

const checkFailed: AgentResponse = {
  ...base,
  status: 'check_failed',
  sql_artifact: null,
  violations: [{ code: 'missing_minimum_group_size', stage: 'ast_check', subject_ids: [] }],
}

async function ask(response: AgentResponse) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, json: async () => response } as Response),
  )
  const onTurn = vi.fn()
  render(<ChatPanel onClose={() => {}} onTurn={onTurn} />)
  fireEvent.change(screen.getByLabelText('Question for the Text-to-SQL agent'), {
    target: { value: 'What is transaction volume by branch?' },
  })
  fireEvent.click(screen.getByLabelText('Send question'))
  await waitFor(() => expect(onTurn).toHaveBeenCalledWith(response))
  return onTurn
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ChatPanel', () => {
  it('shows rows, SQL, lineage, and disclosure for an answered question', async () => {
    await ask(ok)
    expect(screen.getByText('Pune Branch 9')).toBeInTheDocument()
    expect(screen.getByText(/1 bound parameter/)).toBeInTheDocument()
    expect(screen.getAllByText('customers.annual_income').length).toBeGreaterThan(0)
    expect(screen.getByText('max 5 rows')).toBeInTheDocument()
    // The cost evidence a technical reviewer will ask about.
    expect(screen.getByText('1 model call')).toBeInTheDocument()
    expect(screen.getByText('default_ir')).toBeInTheDocument()
  })

  it('explains a governed refusal instead of showing an error', async () => {
    await ask(refused)
    expect(screen.getByText(/governance policy forbids/i)).toBeInTheDocument()
    expect(screen.getByText('policy.sensitive-banking-data')).toBeInTheDocument()
  })

  it('names the failing gate when the generated query is rejected', async () => {
    await ask(checkFailed)
    expect(screen.getByText(/did not pass the gates/i)).toBeInTheDocument()
    expect(screen.getByText('missing_minimum_group_size')).toBeInTheDocument()
  })

  it('does not blame the gates when the model never answered', async () => {
    // A flaky transport read as a policy decision sends the reader looking for
    // a rule that does not exist.
    await ask({
      ...checkFailed,
      violations: [
        { code: 'provider_unavailable', stage: 'provider_transport', subject_ids: [] },
      ],
    })
    expect(screen.queryByText(/did not pass the gates/i)).not.toBeInTheDocument()
    expect(screen.getByText(/model did not answer/i)).toBeInTheDocument()
    expect(screen.getByText('provider_unavailable')).toBeInTheDocument()
  })

  it('does not blame the gates when the reply could not be read', async () => {
    await ask({
      ...checkFailed,
      violations: [
        { code: 'unparsable_generation_outcome', stage: 'default_ir', subject_ids: [] },
      ],
    })
    expect(screen.queryByText(/did not pass the gates/i)).not.toBeInTheDocument()
    expect(screen.getByText(/shape this contract cannot read/i)).toBeInTheDocument()
  })

  it('separates a budget exhaustion from a rejected query', async () => {
    await ask({
      ...checkFailed,
      violations: [{ code: 'deadline_exceeded', stage: 'execution', subject_ids: [] }],
    })
    expect(screen.queryByText(/did not pass the gates/i)).not.toBeInTheDocument()
    expect(screen.getByText(/ran out of its budget/i)).toBeInTheDocument()
  })

  it('reports grounded objects and relationships for the graph highlight', () => {
    expect(usedObjectIds(ok)).toEqual(
      new Set([
        'table.transactions',
        'metric.transaction-volume',
        'relationship.transaction_account',
      ]),
    )
    expect(usedObjectIds(null).size).toBe(0)
    expect(usedObjectIds({ status: 'error', code: 'agent_request_failed' }).size).toBe(0)
  })
})
