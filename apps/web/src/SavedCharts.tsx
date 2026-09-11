import { Bookmark, ChevronRight, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { deleteSavedChart, listSavedCharts } from './api'
import type { SavedChart } from './types'

function savedDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function SavedCharts({ onOpen, refreshKey }: {
  onOpen: (saved: SavedChart) => void
  refreshKey: number
}) {
  const [charts, setCharts] = useState<SavedChart[]>([])
  const [error, setError] = useState('')
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    listSavedCharts(controller.signal)
      .then(setCharts)
      .catch((reason: Error) => { if (reason.name !== 'AbortError') setError(reason.message || 'Could not load saved charts.') })
    return () => controller.abort()
  }, [refreshKey])

  const handleDelete = async (id: string) => {
    try {
      await deleteSavedChart(id)
      setCharts((current) => current.filter((chart) => chart.id !== id))
    } catch (reason) {
      console.error('[Cerebro chat] Failed to delete saved chart', reason)
    } finally {
      setPendingDeleteId(null)
    }
  }

  return (
    <details className="saved-charts" open>
      <summary className="saved-charts-heading rail-toggle">
        <ChevronRight size={12} className="chevron-icon" />
        <Bookmark size={13} />
        <span>Saved</span>
        <em>{charts.length}</em>
      </summary>
      {error && <p className="saved-charts-status error">{error}</p>}
      {!error && charts.length === 0 && <p className="saved-charts-status">No saved questions yet — use Save on a result.</p>}
      {charts.length > 0 && (
        <ul className="saved-charts-list">
          {charts.map((chart) => (
            <li className="saved-chart-item" key={chart.id}>
              <button type="button" className="saved-chart-open" onClick={() => onOpen(chart)}>
                <span className="saved-chart-question">{chart.question}</span>
                <span className="saved-chart-date">{savedDate(chart.created_at)}</span>
              </button>
              {pendingDeleteId === chart.id ? (
                <span className="saved-chart-confirm">
                  <button type="button" onClick={() => { void handleDelete(chart.id) }}>Delete</button>
                  <button type="button" onClick={() => setPendingDeleteId(null)}>Cancel</button>
                </span>
              ) : (
                <button type="button" className="saved-chart-delete" aria-label="Delete saved chart" onClick={() => setPendingDeleteId(chart.id)}>
                  <Trash2 size={12} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </details>
  )
}
