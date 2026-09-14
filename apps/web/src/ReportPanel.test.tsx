import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ReportPanel } from './ReportPanel'

const mocks = vi.hoisted(() => ({
  startReport: vi.fn(),
  getReportDocument: vi.fn(),
  cancelReport: vi.fn().mockResolvedValue({ run_id: 'run-1', status: 'cancellation_requested' }),
}))

vi.mock('./api', () => ({
  startReport: mocks.startReport,
  getReportDocument: mocks.getReportDocument,
  cancelReport: mocks.cancelReport,
  reportEventsUrl: (runId: string) => `/api/reports/runs/${runId}/events`,
  reportPdfUrl: (runId: string) => `/api/reports/runs/${runId}/pdf`,
}))

let eventSource: MockEventSource | null = null

class MockEventSource {
  listeners = new Map<string, Array<(event: Event) => void>>()
  constructor(public url: string) { eventSource = this }
  addEventListener(name: string, listener: (event: Event) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) || []), listener])
  }
  close() {}
  emit(name: string, data: unknown) {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>
    this.listeners.get(name)?.forEach((listener) => listener(event))
  }
}

beforeEach(() => {
  eventSource = null
  vi.stubGlobal('EventSource', MockEventSource)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('ReportPanel', () => {
  it('shows an empty state with an expanded list of preset reports and sample questions', () => {
    render(<ReportPanel active />)
    expect(screen.getByRole('heading', { name: 'Tạo báo cáo' })).toBeInTheDocument()
    const presetPanel = screen.getByText('Preset reports & questions').closest('details')
    expect(presetPanel).toHaveAttribute('open')
    expect(screen.getByRole('button', { name: 'Tổng quan điều hành' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rủi ro tín dụng & nợ xấu' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Phân tích tình hình gian lận thẻ/ })).toBeInTheDocument()
  })

  it('streams plan -> section -> synthesize progress, then renders the finished report with a PDF link', async () => {
    mocks.startReport.mockResolvedValue({
      run_id: 'run-1', request: 'Tạo báo cáo tổng quan điều hành ...', status: 'running',
      started_at: '2026-09-11T00:00:00Z', completed_at: null, current_stage: null, error: null, events: [],
    })
    mocks.getReportDocument.mockResolvedValue({
      run_id: 'run-1', request: 'Tạo báo cáo tổng quan điều hành ...', title: 'Báo cáo tổng quan điều hành',
      overview: 'Đây là đoạn tổng quan.', generated_at: '2026-09-11T00:05:00Z', semantic_version: '0.2.0',
      status: 'completed',
      sections: [{
        id: 's1', title: 'Khách hàng', question: 'Đếm khách hàng theo giới tính', status: 'answered',
        answer: 'Có 2 khách hàng nữ và 1 khách hàng nam.',
        sql: 'SELECT gender, COUNT(*) AS customer_count FROM customers GROUP BY gender',
        columns: ['gender', 'customer_count'], rows: [['Female', 2], ['Male', 1]], row_count: 2,
        truncated: false, evidence_ids: [], warnings: [],
      }],
    })

    render(<ReportPanel active />)
    fireEvent.click(screen.getByRole('button', { name: 'Tổng quan điều hành' }))

    await waitFor(() => expect(mocks.startReport).toHaveBeenCalledWith(expect.stringContaining('báo cáo tổng quan điều hành')))
    expect(eventSource?.url).toBe('/api/reports/runs/run-1/events')

    // Planning stage.
    act(() => eventSource?.emit('progress', { sequence: 1, stage: 'planning', status: 'started', summary: 'Đang lên kế hoạch báo cáo...', details: {} }))
    expect(screen.getByText('Đang lên kế hoạch báo cáo…')).toBeInTheDocument()

    act(() => eventSource?.emit('progress', { sequence: 2, stage: 'planning', status: 'completed', summary: 'Đã lên kế hoạch 1 phần: Khách hàng', details: {} }))
    expect(screen.getByText('Đã lên kế hoạch 1 phần: Khách hàng')).toBeInTheDocument()

    // Section stage: the question appears while running, the answer once it completes.
    act(() => eventSource?.emit('progress', { sequence: 3, stage: 's1', status: 'started', summary: 'Đếm khách hàng theo giới tính', details: {} }))
    expect(screen.getByText('Đếm khách hàng theo giới tính')).toBeInTheDocument()

    act(() => eventSource?.emit('progress', {
      sequence: 4, stage: 's1', status: 'completed', summary: 'Có 2 khách hàng nữ và 1 khách hàng nam.',
      details: { status: 'answered', row_count: 2 },
    }))
    expect(screen.getByText('Có 2 khách hàng nữ và 1 khách hàng nam.')).toBeInTheDocument()

    // No PDF link before the run is terminal.
    expect(screen.queryByRole('link', { name: /Xuất PDF/i })).not.toBeInTheDocument()

    // Synthesize stage, then the terminal SSE event.
    act(() => eventSource?.emit('progress', { sequence: 5, stage: 'synthesizing', status: 'started', summary: 'Đang tổng hợp báo cáo...', details: {} }))
    expect(screen.getByText('Đang tổng hợp báo cáo…')).toBeInTheDocument()

    act(() => eventSource?.emit('complete', {
      run_id: 'run-1', request: 'Tạo báo cáo tổng quan điều hành ...', status: 'completed',
      started_at: '2026-09-11T00:00:00Z', completed_at: '2026-09-11T00:05:00Z', current_stage: null, error: null,
      events: [
        { sequence: 1, stage: 'planning', status: 'started', summary: 'Đang lên kế hoạch báo cáo...', details: {} },
        { sequence: 2, stage: 'planning', status: 'completed', summary: 'Đã lên kế hoạch 1 phần: Khách hàng', details: {} },
        { sequence: 3, stage: 's1', status: 'started', summary: 'Đếm khách hàng theo giới tính', details: {} },
        { sequence: 4, stage: 's1', status: 'completed', summary: 'Có 2 khách hàng nữ và 1 khách hàng nam.', details: { status: 'answered', row_count: 2 } },
        { sequence: 5, stage: 'synthesizing', status: 'started', summary: 'Đang tổng hợp báo cáo...', details: {} },
        { sequence: 6, stage: 'synthesizing', status: 'completed', summary: 'Đã tổng hợp xong.', details: {} },
      ],
    }))

    await waitFor(() => expect(mocks.getReportDocument).toHaveBeenCalledWith('run-1'))
    expect(await screen.findByText('Báo cáo tổng quan điều hành')).toBeInTheDocument()
    expect(screen.getByText('Đây là đoạn tổng quan.')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'gender' })).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /Xuất PDF/i })
    expect(link).toHaveAttribute('href', '/api/reports/runs/run-1/pdf')

    // The sequential-execution trace survives into the final result, not just the live progress view.
    fireEvent.click(screen.getByText(/Quá trình thực hiện/))
    expect(screen.getByText('Lên kế hoạch')).toBeInTheDocument()
    expect(screen.getByText('Câu hỏi con 1')).toBeInTheDocument()
    expect(screen.getByText('Tổng hợp báo cáo')).toBeInTheDocument()
  })

  it('submits free-text input from the composer', async () => {
    mocks.startReport.mockResolvedValue({
      run_id: 'run-2', request: 'Phân tích tình hình gian lận thẻ', status: 'running',
      started_at: '2026-09-11T00:00:00Z', completed_at: null, current_stage: null, error: null, events: [],
    })

    render(<ReportPanel active />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Yêu cầu báo cáo' }), { target: { value: 'Phân tích tình hình gian lận thẻ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Tạo báo cáo' }))

    await waitFor(() => expect(mocks.startReport).toHaveBeenCalledWith('Phân tích tình hình gian lận thẻ'))
    expect(screen.getByText('Phân tích tình hình gian lận thẻ')).toBeInTheDocument()
  })

  it('shows a Stop button while running that cancels the run and never fetches a document once cancelled', async () => {
    mocks.startReport.mockResolvedValue({
      run_id: 'run-4', request: 'Tạo báo cáo tổng quan điều hành ...', status: 'running',
      started_at: '2026-09-11T00:00:00Z', completed_at: null, current_stage: null, error: null, events: [],
    })

    render(<ReportPanel active />)
    fireEvent.click(screen.getByRole('button', { name: 'Tổng quan điều hành' }))
    await waitFor(() => expect(mocks.startReport).toHaveBeenCalled())

    const stopButton = await screen.findByRole('button', { name: 'Dừng' })
    fireEvent.click(stopButton)
    expect(mocks.cancelReport).toHaveBeenCalledWith('run-4')
    expect(screen.getByRole('button', { name: 'Đang dừng…' })).toBeDisabled()

    act(() => eventSource?.emit('complete', {
      run_id: 'run-4', request: 'Tạo báo cáo tổng quan điều hành ...', status: 'cancelled',
      started_at: '2026-09-11T00:00:00Z', completed_at: '2026-09-11T00:00:02Z', current_stage: null, error: null,
      events: [],
    }))

    expect(await screen.findByText('Report đã được dừng theo yêu cầu.')).toBeInTheDocument()
    expect(mocks.getReportDocument).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Dừng' })).not.toBeInTheDocument()
  })

  it('shows a status-specific badge in the header and on a clarification section', async () => {
    mocks.startReport.mockResolvedValue({
      run_id: 'run-5', request: 'x', status: 'running', started_at: 't0', completed_at: null, current_stage: null, error: null, events: [],
    })
    mocks.getReportDocument.mockResolvedValue({
      run_id: 'run-5', request: 'x', title: 'Báo cáo một phần', overview: '',
      generated_at: '2026-09-11T00:05:00Z', semantic_version: '0.2.0', status: 'partial',
      sections: [{
        id: 's1', title: 'Phần khó', question: 'Câu hỏi khó', status: 'clarification',
        answer: 'Cần làm rõ thêm.', sql: null, columns: [], rows: [], row_count: 0,
        truncated: false, evidence_ids: [], warnings: [],
      }],
    })

    render(<ReportPanel active />)
    fireEvent.click(screen.getByRole('button', { name: 'Tổng quan điều hành' }))
    await waitFor(() => expect(mocks.startReport).toHaveBeenCalled())

    act(() => eventSource?.emit('complete', {
      run_id: 'run-5', request: 'x', status: 'partial', started_at: 't0', completed_at: 't1', current_stage: null, error: null, events: [],
    }))

    expect(await screen.findByText('Báo cáo một phần')).toBeInTheDocument()
    expect(screen.getByText('Một phần')).toBeInTheDocument()
    expect(screen.getByText('Cần làm rõ')).toBeInTheDocument()
  })

  it('shows an explicit empty-result line for a zero-row section and a truncation note for a truncated one', async () => {
    mocks.startReport.mockResolvedValue({
      run_id: 'run-6', request: 'x', status: 'running', started_at: 't0', completed_at: null, current_stage: null, error: null, events: [],
    })
    mocks.getReportDocument.mockResolvedValue({
      run_id: 'run-6', request: 'x', title: 'Báo cáo test', overview: '',
      generated_at: '2026-09-11T00:05:00Z', semantic_version: '0.2.0', status: 'completed',
      sections: [
        { id: 's1', title: 'Không có dữ liệu', question: 'q1', status: 'answered', answer: 'a1', sql: null, columns: ['a'], rows: [], row_count: 0, truncated: false, evidence_ids: [], warnings: [] },
        { id: 's2', title: 'Có dữ liệu bị cắt', question: 'q2', status: 'answered', answer: 'a2', sql: null, columns: ['a'], rows: [['x']], row_count: 500, truncated: true, evidence_ids: [], warnings: [] },
      ],
    })

    render(<ReportPanel active />)
    fireEvent.click(screen.getByRole('button', { name: 'Tổng quan điều hành' }))
    await waitFor(() => expect(mocks.startReport).toHaveBeenCalled())
    act(() => eventSource?.emit('complete', {
      run_id: 'run-6', request: 'x', status: 'completed', started_at: 't0', completed_at: 't1', current_stage: null, error: null, events: [],
    }))

    expect(await screen.findByText('Không có dữ liệu phù hợp.')).toBeInTheDocument()
    expect(screen.getByText(/500 dòng kết quả/)).toBeInTheDocument()
    expect(screen.getByText(/đã cắt bớt theo giới hạn hiển thị/)).toBeInTheDocument()
  })

  it('renders a table of contents only once a report has three or more sections', async () => {
    const sectionOf = (id: string, title: string) => (
      { id, title, question: `q-${id}`, status: 'answered' as const, answer: `a-${id}`, sql: null, columns: [], rows: [], row_count: 0, truncated: false, evidence_ids: [], warnings: [] }
    )

    mocks.startReport.mockResolvedValueOnce({
      run_id: 'run-7', request: 'x', status: 'running', started_at: 't0', completed_at: null, current_stage: null, error: null, events: [],
    })
    mocks.getReportDocument.mockResolvedValueOnce({
      run_id: 'run-7', request: 'x', title: 'Báo cáo 2 phần', overview: '',
      generated_at: 't0', semantic_version: '0.2.0', status: 'completed',
      sections: [sectionOf('s1', 'A'), sectionOf('s2', 'B')],
    })

    render(<ReportPanel active />)
    fireEvent.click(screen.getByRole('button', { name: 'Tổng quan điều hành' }))
    await waitFor(() => expect(mocks.startReport).toHaveBeenCalled())
    act(() => eventSource?.emit('complete', {
      run_id: 'run-7', request: 'x', status: 'completed', started_at: 't0', completed_at: 't1', current_stage: null, error: null, events: [],
    }))
    expect(await screen.findByText('Báo cáo 2 phần')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Mục lục báo cáo' })).not.toBeInTheDocument()

    mocks.startReport.mockResolvedValueOnce({
      run_id: 'run-8', request: 'y', status: 'running', started_at: 't0', completed_at: null, current_stage: null, error: null, events: [],
    })
    mocks.getReportDocument.mockResolvedValueOnce({
      run_id: 'run-8', request: 'y', title: 'Báo cáo 3 phần', overview: '',
      generated_at: 't0', semantic_version: '0.2.0', status: 'completed',
      sections: [sectionOf('s1', 'A'), sectionOf('s2', 'B'), sectionOf('s3', 'C')],
    })
    fireEvent.click(screen.getByRole('button', { name: 'Tổng quan điều hành' }))
    await waitFor(() => expect(mocks.startReport).toHaveBeenCalledTimes(2))
    act(() => eventSource?.emit('complete', {
      run_id: 'run-8', request: 'y', status: 'completed', started_at: 't0', completed_at: 't1', current_stage: null, error: null, events: [],
    }))
    const toc = await screen.findByRole('navigation', { name: 'Mục lục báo cáo' })
    expect(toc.querySelectorAll('a')).toHaveLength(3)
  })

  it('shows the failure reason directly and never opens an event stream when the run fails immediately', async () => {
    mocks.startReport.mockResolvedValue({
      run_id: 'run-3', request: 'Tạo báo cáo tổng quan điều hành ...', status: 'failed',
      started_at: '2026-09-11T00:00:00Z', completed_at: '2026-09-11T00:00:01Z', current_stage: null,
      error: 'Configure CEREBRO_DATABASE_PATH with a readable DuckDB database, then restart the server.',
      events: [],
    })

    render(<ReportPanel active />)
    fireEvent.click(screen.getByRole('button', { name: 'Tổng quan điều hành' }))

    expect(await screen.findByText(/Configure CEREBRO_DATABASE_PATH/)).toBeInTheDocument()
    expect(eventSource).toBeNull()
    expect(mocks.getReportDocument).not.toHaveBeenCalled()
    expect(screen.queryByRole('link', { name: /Xuất PDF/i })).not.toBeInTheDocument()
  })
})
