import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Inspector } from './Inspector'
import type { SemanticObject } from './types'

const table: SemanticObject = {
  id: 'table.transactions',
  type: 'table',
  label: 'Transactions',
  name: 'Transactions',
  status: 'active',
  description: 'Account ledger events.',
  classification: 'confidential',
  aliases: [],
  tags: [],
  links: [],
  provenance: { origin: 'human_reviewed' },
  cerebro: {
    grain: 'one account-level ledger event',
    classification: 'confidential',
    columns: [{ name: 'transaction_id', data_type: 'BIGINT', classification: 'internal' }],
    warnings: ['Amounts are positive.'],
  },
  body: '',
  path: 'tables/transactions.md',
}

describe('Inspector', () => {
  it('renders empty guidance', () => {
    render(<Inspector object={null} loading={false} />)
    expect(screen.getByText('Select a semantic object')).toBeInTheDocument()
  })

  it('renders grain, fields, warnings, and provenance', () => {
    render(<Inspector object={table} loading={false} />)
    expect(screen.getByText('one account-level ledger event')).toBeInTheDocument()
    expect(screen.getByText('transaction_id')).toBeInTheDocument()
    expect(screen.getByText('Amounts are positive.')).toBeInTheDocument()
    expect(screen.getByText('human_reviewed')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open OKF source/ })).toHaveAttribute('href', '/knowledge/tables/transactions.md')
  })
})
