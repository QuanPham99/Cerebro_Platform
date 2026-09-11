export type Cell = string | number | boolean | null
export type ColumnKind = 'numeric' | 'temporal' | 'categorical'

export const PALETTE = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181']
export const MAX_PLOTTED_ROWS = 30

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$/

function inferColumnKind(values: Cell[]): ColumnKind {
  const nonNull = values.filter((value) => value !== null)
  if (nonNull.length === 0) return 'categorical'
  if (nonNull.some((value) => typeof value === 'boolean')) return 'categorical'
  if (nonNull.every((value) => typeof value === 'number' && Number.isFinite(value))) return 'numeric'
  if (nonNull.every((value) => typeof value === 'string' && ISO_DATE_RE.test(value) && !Number.isNaN(Date.parse(value)))) return 'temporal'
  return 'categorical'
}

export function inferColumnKinds(columns: string[], rows: Cell[][]): ColumnKind[] {
  return columns.map((_, columnIndex) => inferColumnKind(rows.map((row) => row[columnIndex])))
}

export type ChartSeries = { label: string; color: string }

export type ChartSpec =
  | { kind: 'kpi'; tiles: Array<{ label: string; value: number }> }
  | { kind: 'bar'; categoryLabel: string; measureLabel: string; points: Array<{ category: string; value: number }>; omittedCount: number }
  | { kind: 'line'; axisLabel: string; measureLabel: string; points: Array<{ x: string; value: number }>; omittedCount: number }
  | { kind: 'grouped-bar'; categoryLabel: string; series: ChartSeries[]; points: Array<{ category: string; values: number[] }>; omittedCount: number }
  | { kind: 'multi-line'; axisLabel: string; series: ChartSeries[]; points: Array<{ x: string; values: number[] }>; omittedCount: number }

function toNumber(value: Cell): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

export function selectChartSpec(columns: string[], rows: Cell[][]): ChartSpec | null {
  if (rows.length === 0) return null
  const kinds = inferColumnKinds(columns, rows)
  const numericIdx = kinds.reduce<number[]>((acc, kind, i) => (kind === 'numeric' ? [...acc, i] : acc), [])
  const nonNumericIdx = kinds.reduce<number[]>((acc, kind, i) => (kind !== 'numeric' ? [...acc, i] : acc), [])
  if (numericIdx.length === 0) return null

  if (rows.length === 1) {
    return { kind: 'kpi', tiles: numericIdx.map((i) => ({ label: columns[i], value: toNumber(rows[0][i]) })) }
  }

  if (nonNumericIdx.length !== 1) return null
  const axisIdx = nonNumericIdx[0]
  const axisKind = kinds[axisIdx]
  const primaryMeasureIdx = numericIdx[0]

  // A categorical axis is never rejected for having many distinct values: instead of
  // showing no chart at all, the plotted-row cap below keeps only the most significant
  // (highest-value) rows, so "too many branches/categories" degrades to a top-N chart
  // rather than table-only.
  const ordered = axisKind === 'temporal'
    ? [...rows].sort((a, b) => Date.parse(String(a[axisIdx])) - Date.parse(String(b[axisIdx])))
    : rows.length > MAX_PLOTTED_ROWS
      ? [...rows].sort((a, b) => toNumber(b[primaryMeasureIdx]) - toNumber(a[primaryMeasureIdx]))
      : rows
  const omittedCount = Math.max(0, ordered.length - MAX_PLOTTED_ROWS)
  const plotted = ordered.slice(0, MAX_PLOTTED_ROWS)

  if (numericIdx.length === 1) {
    const measureIdx = numericIdx[0]
    const points = plotted.map((row) => ({ category: String(row[axisIdx]), value: toNumber(row[measureIdx]) }))
    return axisKind === 'temporal'
      ? { kind: 'line', axisLabel: columns[axisIdx], measureLabel: columns[measureIdx], points: points.map((p) => ({ x: p.category, value: p.value })), omittedCount }
      : { kind: 'bar', categoryLabel: columns[axisIdx], measureLabel: columns[measureIdx], points, omittedCount }
  }

  const series: ChartSeries[] = numericIdx.map((i, s) => ({ label: columns[i], color: PALETTE[s % PALETTE.length] }))
  const points = plotted.map((row) => ({ category: String(row[axisIdx]), values: numericIdx.map((i) => toNumber(row[i])) }))
  return axisKind === 'temporal'
    ? { kind: 'multi-line', axisLabel: columns[axisIdx], series, points: points.map((p) => ({ x: p.category, values: p.values })), omittedCount }
    : { kind: 'grouped-bar', categoryLabel: columns[axisIdx], series, points, omittedCount }
}

export function niceMax(value: number): number {
  if (value <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  const normalized = value / magnitude
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10
  return step * magnitude
}

export function formatAxisNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)
}

export function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

export function truncateLabel(label: string, maxLength = 12): string {
  return label.length > maxLength ? `${label.slice(0, maxLength - 1)}…` : label
}
