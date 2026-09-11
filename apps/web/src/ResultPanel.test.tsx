import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ResultPanel } from './ResultPanel'
import { inferColumnKinds, MAX_PLOTTED_ROWS, selectChartSpec } from './resultVisualization'

afterEach(() => {
  cleanup()
})

describe('inferColumnKinds', () => {
  it('classifies a column of plain numbers as numeric', () => {
    expect(inferColumnKinds(['total'], [[1], [2], [3]])).toEqual(['numeric'])
  })

  it('classifies a column of text as categorical', () => {
    expect(inferColumnKinds(['gender'], [['Female'], ['Male']])).toEqual(['categorical'])
  })

  it('classifies ISO date strings as temporal', () => {
    expect(inferColumnKinds(['month'], [['2024-01-01'], ['2024-02-01']])).toEqual(['temporal'])
  })

  it('classifies an all-null column as categorical', () => {
    expect(inferColumnKinds(['note'], [[null], [null]])).toEqual(['categorical'])
  })

  it('classifies a boolean column as categorical, never numeric', () => {
    expect(inferColumnKinds(['is_active'], [[true], [false]])).toEqual(['categorical'])
  })

  it('classifies a column mixing types as categorical', () => {
    expect(inferColumnKinds(['mixed'], [[1], ['two'], [3]])).toEqual(['categorical'])
  })
})

describe('selectChartSpec', () => {
  it('returns a bar spec for 1 categorical + 1 numeric column, 2+ rows', () => {
    const spec = selectChartSpec(['gender', 'total'], [['Female', 2], ['Male', 1]])
    expect(spec).toMatchObject({ kind: 'bar', categoryLabel: 'gender', measureLabel: 'total' })
  })

  it('returns a line spec for 1 temporal + 1 numeric column, sorted chronologically', () => {
    const spec = selectChartSpec(['month', 'total'], [['2024-02-01', 20], ['2024-01-01', 10]])
    expect(spec).toMatchObject({
      kind: 'line',
      points: [{ x: '2024-01-01', value: 10 }, { x: '2024-02-01', value: 20 }],
    })
  })

  it('returns a multi-series spec with a legend-ready series list for 1 axis + 2 numeric columns', () => {
    const spec = selectChartSpec(['branch', 'deposits', 'loans'], [['A', 100, 50], ['B', 200, 80]])
    expect(spec?.kind).toEqual('grouped-bar')
    if (spec?.kind === 'grouped-bar') {
      expect(spec.series.map((s) => s.label)).toEqual(['deposits', 'loans'])
      expect(spec.series[0].color).not.toEqual(spec.series[1].color)
    }
  })

  it('returns a kpi spec for a single row with numeric columns', () => {
    const spec = selectChartSpec(['branch_count'], [[12]])
    expect(spec).toEqual({ kind: 'kpi', tiles: [{ label: 'branch_count', value: 12 }] })
  })

  it('returns null for 2 categorical columns', () => {
    expect(selectChartSpec(['branch', 'status'], [['A', 'open'], ['B', 'closed']])).toBeNull()
  })

  it('charts the top MAX_PLOTTED_ROWS by value instead of dropping the chart when a categorical axis has many distinct values', () => {
    const rows = Array.from({ length: 73 }, (_, i) => [`branch-${i}`, i])
    const spec = selectChartSpec(['branch', 'total'], rows)
    expect(spec?.kind).toEqual('bar')
    if (spec?.kind === 'bar') {
      expect(spec.points).toHaveLength(MAX_PLOTTED_ROWS)
      expect(spec.omittedCount).toEqual(73 - MAX_PLOTTED_ROWS)
      // Highest values first: branch-72 (value 72) down to branch-43 (value 43).
      expect(spec.points[0]).toEqual({ category: 'branch-72', value: 72 })
      expect(spec.points.at(-1)).toEqual({ category: 'branch-43', value: 43 })
    }
  })

  it('caps plotted rows at MAX_PLOTTED_ROWS and reports the omitted count', () => {
    const rows = Array.from({ length: MAX_PLOTTED_ROWS + 10 }, (_, i) => [
      new Date(2024, 0, i + 1).toISOString().slice(0, 10), i,
    ])
    const spec = selectChartSpec(['day', 'value'], rows)
    expect(spec?.kind).toEqual('line')
    if (spec?.kind === 'line') {
      expect(spec.points).toHaveLength(MAX_PLOTTED_ROWS)
      expect(spec.omittedCount).toEqual(10)
    }
  })

  it('returns null for fewer than 2 rows with no single-row numeric shape', () => {
    expect(selectChartSpec(['gender', 'total'], [])).toBeNull()
  })
})

describe('ResultPanel', () => {
  it('renders a bar chart alongside the table for gender/total rows', () => {
    render(<ResultPanel columns={['gender', 'total']} rows={[['Female', 2], ['Male', 1]]} rowCount={2} truncated={false} />)
    expect(document.querySelector('.result-table-wrap table')).toBeInTheDocument()
    const chart = document.querySelector('.result-chart')
    expect(chart).toBeInTheDocument()
    expect(chart).toHaveAttribute('data-chart-kind', 'bar')
    expect(screen.getByText('total by gender')).toBeInTheDocument()
  })

  it('renders a KPI tile for a single-row numeric result', () => {
    render(<ResultPanel columns={['branch_count']} rows={[[12]]} rowCount={1} truncated={false} />)
    const kpis = document.querySelector('.result-chart-kpis')
    expect(kpis).toBeInTheDocument()
    expect(within(kpis as HTMLElement).getByText('branch_count')).toBeInTheDocument()
    expect(within(kpis as HTMLElement).getByText('12')).toBeInTheDocument()
  })

  it('renders only the table for an ineligible (2-categorical-column) result', () => {
    render(<ResultPanel columns={['branch', 'status']} rows={[['A', 'open'], ['B', 'closed']]} rowCount={2} truncated={false} />)
    expect(document.querySelector('.result-table-wrap table')).toBeInTheDocument()
    expect(document.querySelector('.result-chart')).toBeNull()
  })

  it('keeps the table row and column count exactly matching the response', () => {
    render(<ResultPanel columns={['gender', 'total']} rows={[['Female', 2], ['Male', 1]]} rowCount={2} truncated={false} />)
    const rows = document.querySelectorAll('.result-table-wrap tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0].querySelectorAll('td')).toHaveLength(2)
  })
})
