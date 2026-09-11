import { BarChart3 } from 'lucide-react'
import { type MouseEvent, type ReactNode, useMemo, useState } from 'react'
import {
  formatAxisNumber,
  formatCompactNumber,
  MAX_PLOTTED_ROWS,
  niceMax,
  PALETTE,
  selectChartSpec,
  truncateLabel,
  type Cell,
  type ChartSeries,
  type ChartSpec,
} from './resultVisualization'

const VIEW_W = 560
const VIEW_H = 220
const MARGIN = { top: 16, right: 14, bottom: 34, left: 46 }
const PLOT_W = VIEW_W - MARGIN.left - MARGIN.right
const PLOT_H = VIEW_H - MARGIN.top - MARGIN.bottom
const BASELINE_Y = MARGIN.top + PLOT_H

function tickValues(max: number): number[] {
  const rounded = [0, max * 0.25, max * 0.5, max * 0.75, max].map((value) => Math.round(value))
  return [...new Set(rounded)]
}

function bandPositions(count: number): number[] {
  const band = PLOT_W / count
  return Array.from({ length: count }, (_, i) => MARGIN.left + band * (i + 0.5))
}

function axisTickIndices(count: number, maxTicks = 7): number[] {
  const step = Math.max(1, Math.ceil(count / maxTicks))
  const indices: number[] = []
  for (let i = 0; i < count; i += step) indices.push(i)
  return indices
}

function ChartFrame({ maxValue, categories, children, onLeave }: {
  maxValue: number
  categories: string[]
  children: ReactNode
  onLeave: () => void
}) {
  const ticks = tickValues(maxValue)
  const positions = bandPositions(categories.length)
  const labelIdx = new Set(axisTickIndices(categories.length))
  return (
    <svg
      className="result-chart-svg"
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      role="img"
      onMouseLeave={onLeave}
    >
      {ticks.map((tick) => {
        const y = BASELINE_Y - (maxValue === 0 ? 0 : (tick / maxValue) * PLOT_H)
        return (
          <g key={tick}>
            <line x1={MARGIN.left} x2={VIEW_W - MARGIN.right} y1={y} y2={y} className="result-chart-grid" />
            <text x={MARGIN.left - 6} y={y} className="result-chart-axis-label" textAnchor="end" dominantBaseline="middle">
              {formatAxisNumber(tick)}
            </text>
          </g>
        )
      })}
      {categories.map((category, i) => (
        labelIdx.has(i) && (
          <text key={category + i} x={positions[i]} y={VIEW_H - 10} className="result-chart-axis-label" textAnchor="middle">
            {truncateLabel(category)}
          </text>
        )
      ))}
      {children}
    </svg>
  )
}

function useHover() {
  const [index, setIndex] = useState<number | null>(null)
  return { index, setIndex, clear: () => setIndex(null) }
}

function Tooltip({ index, categories, rows }: {
  index: number | null
  categories: string[]
  rows: Array<Array<{ label: string; value: number; color: string }>>
}) {
  if (index === null) return null
  const x = bandPositions(categories.length)[index]
  const left = (x / VIEW_W) * 100
  const top = 6
  return (
    <div className="result-chart-tooltip" style={{ left: `${left}%`, top: `${top}%` }}>
      <strong>{categories[index]}</strong>
      {rows[index].map((entry) => (
        <div key={entry.label} className="result-chart-tooltip-row">
          <span className="result-chart-tooltip-key" style={{ background: entry.color }} />
          <span>{entry.label}: <strong>{formatAxisNumber(entry.value)}</strong></span>
        </div>
      ))}
    </div>
  )
}

function BarChart({ points, color }: { points: Array<{ category: string; value: number }>; color: string }) {
  const hover = useHover()
  const max = niceMax(Math.max(...points.map((p) => p.value), 0))
  const positions = bandPositions(points.length)
  const barWidth = Math.min(24, (PLOT_W / points.length) * 0.6)
  const categories = points.map((p) => p.category)
  const tooltipRows = points.map((p) => [{ label: 'Value', value: p.value, color }])
  return (
    <div className="result-chart-frame">
      <ChartFrame maxValue={max} categories={categories} onLeave={hover.clear}>
        {points.map((p, i) => {
          const barHeight = max === 0 ? 0 : (p.value / max) * PLOT_H
          const x = positions[i] - barWidth / 2
          const y = BASELINE_Y - barHeight
          return (
            <g key={p.category + i}>
              <rect
                x={x} y={y} width={barWidth} height={Math.max(barHeight, 0)} rx={4} ry={4}
                className={`result-chart-bar${hover.index === i ? ' hovered' : ''}`}
                style={{ fill: color }}
              />
              <rect
                x={positions[i] - (PLOT_W / points.length) / 2} y={MARGIN.top} width={PLOT_W / points.length} height={PLOT_H}
                fill="transparent" onMouseEnter={() => hover.setIndex(i)} onFocus={() => hover.setIndex(i)} tabIndex={0}
              />
            </g>
          )
        })}
      </ChartFrame>
      <Tooltip index={hover.index} categories={categories} rows={tooltipRows} />
    </div>
  )
}

function GroupedBarChart({ points, series }: { points: Array<{ category: string; values: number[] }>; series: ChartSeries[] }) {
  const hover = useHover()
  const max = niceMax(Math.max(...points.flatMap((p) => p.values), 0))
  const positions = bandPositions(points.length)
  const bandWidth = PLOT_W / points.length
  const groupWidth = Math.min(bandWidth * 0.75, series.length * 16)
  const barWidth = Math.min(20, groupWidth / series.length)
  const categories = points.map((p) => p.category)
  const tooltipRows = points.map((p) => p.values.map((value, s) => ({ label: series[s].label, value, color: series[s].color })))
  return (
    <div className="result-chart-frame">
      <ChartFrame maxValue={max} categories={categories} onLeave={hover.clear}>
        {points.map((p, i) => {
          const groupStart = positions[i] - groupWidth / 2
          return (
            <g key={p.category + i}>
              {p.values.map((value, s) => {
                const barHeight = max === 0 ? 0 : (value / max) * PLOT_H
                const x = groupStart + s * barWidth
                const y = BASELINE_Y - barHeight
                return (
                  <rect
                    key={series[s].label}
                    x={x} y={y} width={Math.max(barWidth - 2, 1)} height={Math.max(barHeight, 0)} rx={3} ry={3}
                    className={`result-chart-bar${hover.index === i ? ' hovered' : ''}`}
                    style={{ fill: series[s].color }}
                  />
                )
              })}
              <rect
                x={positions[i] - bandWidth / 2} y={MARGIN.top} width={bandWidth} height={PLOT_H}
                fill="transparent" onMouseEnter={() => hover.setIndex(i)} onFocus={() => hover.setIndex(i)} tabIndex={0}
              />
            </g>
          )
        })}
      </ChartFrame>
      <Tooltip index={hover.index} categories={categories} rows={tooltipRows} />
      <Legend series={series} />
    </div>
  )
}

function pathFor(values: number[], max: number): string {
  const positions = bandPositions(values.length)
  return values
    .map((value, i) => {
      const y = BASELINE_Y - (max === 0 ? 0 : (value / max) * PLOT_H)
      return `${i === 0 ? 'M' : 'L'}${positions[i]},${y}`
    })
    .join(' ')
}

function LineChart({ points, color }: { points: Array<{ x: string; value: number }>; color: string }) {
  const hover = useHover()
  const max = niceMax(Math.max(...points.map((p) => p.value), 0))
  const positions = bandPositions(points.length)
  const categories = points.map((p) => p.x)
  const tooltipRows = points.map((p) => [{ label: 'Value', value: p.value, color }])

  const handleMove = (event: MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const viewX = ((event.clientX - rect.left) / rect.width) * VIEW_W
    let nearest = 0
    let nearestDist = Infinity
    positions.forEach((x, i) => {
      const dist = Math.abs(x - viewX)
      if (dist < nearestDist) { nearestDist = dist; nearest = i }
    })
    hover.setIndex(nearest)
  }

  return (
    <div className="result-chart-frame">
      <svg
        className="result-chart-svg"
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        role="img"
        onMouseMove={handleMove}
        onMouseLeave={hover.clear}
      >
        {tickValues(max).map((tick) => {
          const y = BASELINE_Y - (max === 0 ? 0 : (tick / max) * PLOT_H)
          return (
            <g key={tick}>
              <line x1={MARGIN.left} x2={VIEW_W - MARGIN.right} y1={y} y2={y} className="result-chart-grid" />
              <text x={MARGIN.left - 6} y={y} className="result-chart-axis-label" textAnchor="end" dominantBaseline="middle">
                {formatAxisNumber(tick)}
              </text>
            </g>
          )
        })}
        {categories.map((category, i) => (
          axisTickIndices(categories.length).includes(i) && (
            <text key={category + i} x={positions[i]} y={VIEW_H - 10} className="result-chart-axis-label" textAnchor="middle">
              {truncateLabel(category)}
            </text>
          )
        ))}
        {hover.index !== null && (
          <line x1={positions[hover.index]} x2={positions[hover.index]} y1={MARGIN.top} y2={BASELINE_Y} className="result-chart-crosshair" />
        )}
        <path d={pathFor(points.map((p) => p.value), max)} className="result-chart-line" style={{ stroke: color }} vectorEffect="non-scaling-stroke" />
        {points.map((p, i) => (
          <circle
            key={p.x + i} cx={positions[i]} cy={BASELINE_Y - (max === 0 ? 0 : (p.value / max) * PLOT_H)} r={4}
            className="result-chart-dot" style={{ fill: color }}
          />
        ))}
      </svg>
      <Tooltip index={hover.index} categories={categories} rows={tooltipRows} />
    </div>
  )
}

function MultiLineChart({ points, series }: { points: Array<{ x: string; values: number[] }>; series: ChartSeries[] }) {
  const hover = useHover()
  const max = niceMax(Math.max(...points.flatMap((p) => p.values), 0))
  const positions = bandPositions(points.length)
  const categories = points.map((p) => p.x)
  const tooltipRows = points.map((p) => p.values.map((value, s) => ({ label: series[s].label, value, color: series[s].color })))

  const handleMove = (event: MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const viewX = ((event.clientX - rect.left) / rect.width) * VIEW_W
    let nearest = 0
    let nearestDist = Infinity
    positions.forEach((x, i) => {
      const dist = Math.abs(x - viewX)
      if (dist < nearestDist) { nearestDist = dist; nearest = i }
    })
    hover.setIndex(nearest)
  }

  return (
    <div className="result-chart-frame">
      <svg className="result-chart-svg" viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="img" onMouseMove={handleMove} onMouseLeave={hover.clear}>
        {tickValues(max).map((tick) => {
          const y = BASELINE_Y - (max === 0 ? 0 : (tick / max) * PLOT_H)
          return (
            <g key={tick}>
              <line x1={MARGIN.left} x2={VIEW_W - MARGIN.right} y1={y} y2={y} className="result-chart-grid" />
              <text x={MARGIN.left - 6} y={y} className="result-chart-axis-label" textAnchor="end" dominantBaseline="middle">
                {formatAxisNumber(tick)}
              </text>
            </g>
          )
        })}
        {categories.map((category, i) => (
          axisTickIndices(categories.length).includes(i) && (
            <text key={category + i} x={positions[i]} y={VIEW_H - 10} className="result-chart-axis-label" textAnchor="middle">
              {truncateLabel(category)}
            </text>
          )
        ))}
        {hover.index !== null && (
          <line x1={positions[hover.index]} x2={positions[hover.index]} y1={MARGIN.top} y2={BASELINE_Y} className="result-chart-crosshair" />
        )}
        {series.map((s, seriesIndex) => (
          <path key={s.label} d={pathFor(points.map((p) => p.values[seriesIndex]), max)} className="result-chart-line" style={{ stroke: s.color }} vectorEffect="non-scaling-stroke" />
        ))}
        {series.map((s, seriesIndex) => (
          points.map((p, i) => (
            <circle
              key={`${s.label}-${p.x}-${i}`} cx={positions[i]} cy={BASELINE_Y - (max === 0 ? 0 : (p.values[seriesIndex] / max) * PLOT_H)} r={4}
              className="result-chart-dot" style={{ fill: s.color }}
            />
          ))
        ))}
      </svg>
      <Tooltip index={hover.index} categories={categories} rows={tooltipRows} />
      <Legend series={series} />
    </div>
  )
}

function Legend({ series }: { series: ChartSeries[] }) {
  return (
    <div className="result-chart-legend">
      {series.map((s) => (
        <span className="result-chart-legend-item" key={s.label}>
          <span className="result-chart-legend-swatch" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
    </div>
  )
}

function KpiTiles({ tiles }: { tiles: Array<{ label: string; value: number }> }) {
  return (
    <div className="result-chart-kpis">
      {tiles.map((tile) => (
        <div className="result-chart-kpi" key={tile.label}>
          <span className="result-chart-kpi-label">{tile.label}</span>
          <span className="result-chart-kpi-value">{formatCompactNumber(tile.value)}</span>
        </div>
      ))}
    </div>
  )
}

function titleFor(spec: ChartSpec): string {
  if (spec.kind === 'kpi') return 'Summary'
  if (spec.kind === 'bar') return `${spec.measureLabel} by ${spec.categoryLabel}`
  if (spec.kind === 'line') return `${spec.measureLabel} over ${spec.axisLabel}`
  if (spec.kind === 'grouped-bar') return `${spec.series.map((s) => s.label).join(', ')} by ${spec.categoryLabel}`
  return `${spec.series.map((s) => s.label).join(', ')} over ${spec.axisLabel}`
}

function ResultChart({ spec, rowCount }: { spec: ChartSpec; rowCount: number }) {
  const omittedCount = 'omittedCount' in spec ? spec.omittedCount : 0
  return (
    <div className="result-chart" data-chart-kind={spec.kind}>
      <div className="result-chart-header"><BarChart3 size={13} /><span>{titleFor(spec)}</span></div>
      {spec.kind === 'kpi' && <KpiTiles tiles={spec.tiles} />}
      {spec.kind === 'bar' && <BarChart points={spec.points} color={PALETTE[0]} />}
      {spec.kind === 'line' && <LineChart points={spec.points} color={PALETTE[0]} />}
      {spec.kind === 'grouped-bar' && <GroupedBarChart points={spec.points} series={spec.series} />}
      {spec.kind === 'multi-line' && <MultiLineChart points={spec.points} series={spec.series} />}
      {omittedCount > 0 && (
        <small className="result-chart-note">Chart shows the top {MAX_PLOTTED_ROWS} of {rowCount} rows.</small>
      )}
    </div>
  )
}

export function ResultPanel({ columns, rows, rowCount, truncated }: {
  columns: string[]
  rows: Cell[][]
  rowCount: number
  truncated: boolean
}) {
  const spec = useMemo(() => selectChartSpec(columns, rows), [columns, rows])
  return (
    <div className="result-panels">
      <div className="result-table-wrap">
        <table>
          <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
          <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, cellIndex) => <td key={cellIndex}>{String(value ?? 'NULL')}</td>)}</tr>)}</tbody>
        </table>
        {truncated && <small>Results truncated by the governed row cap.</small>}
      </div>
      {spec && <ResultChart spec={spec} rowCount={rowCount} />}
    </div>
  )
}
