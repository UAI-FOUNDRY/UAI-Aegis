import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api/client'
import { errorText } from './apiErrors'
import ConfirmDialog from './components/ConfirmDialog'
import { useToast } from './components/ToastProvider'
import { fmtMarks, fmtTime, isGrading, type ExamInfo, type ResultRow } from './lib/grading'
import { STATUS_LABELS, finishReasonText } from './lib/proctorText'

type Props = {
  examId: string
  onBackToMonitor: () => void
  onOpenSession: (sessionId: string) => void
  onOpenAnswers: () => void
}

type SortKey = 'name' | 'batch' | 'total' | 'percent'

const pageStyle = {
  maxWidth: '1300px',
  margin: '24px auto',
  padding: '16px',
  fontFamily: 'Arial, sans-serif',
  textAlign: 'left' as const, // #root centres text (index.css)
}

export default function ResultsPage({ examId, onBackToMonitor, onOpenSession, onOpenAnswers }: Props) {
  const { notify } = useToast()
  const [exam, setExam] = useState<ExamInfo | null>(null)
  const [rows, setRows] = useState<ResultRow[] | null>(null)
  const [error, setError] = useState('')
  const [batchId, setBatchId] = useState('')
  const [language, setLanguage] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'batch', desc: false })
  // Filter options, remembered from the unfiltered list.
  const [batchOptions, setBatchOptions] = useState<{ id: string; code: string }[]>([])
  const [languageOptions, setLanguageOptions] = useState<string[]>([])

  const [regradeOpen, setRegradeOpen] = useState(false)
  const [regradeEveryone, setRegradeEveryone] = useState(false)
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const loadExam = useCallback(async () => {
    const result = await api.GET('/api/v1/exams/{exam_id}', { params: { path: { exam_id: examId } } })
    if (result.error) {
      setError(result.response.status === 404 ? 'Not found or you don’t have access.' : errorText(result.error, 'Could not load the exam.'))
      return
    }
    setExam(result.data as ExamInfo)
  }, [examId])

  const loadResults = useCallback(async () => {
    const result = await api.GET('/api/v1/exams/{exam_id}/results', {
      params: {
        path: { exam_id: examId },
        query: { ...(batchId ? { batch_id: batchId } : {}), ...(language ? { language } : {}) },
      },
    })
    if (result.error) {
      setError(errorText(result.error, 'Could not load results.'))
      return
    }
    const data = result.data as ResultRow[]
    setRows(data)
    if (!batchId && !language) {
      const batches = new Map<string, string>()
      const langs = new Set<string>()
      for (const r of data) {
        if (r.batch_id) batches.set(r.batch_id, r.batch_code ?? r.batch_id)
        r.languages.forEach((l) => langs.add(l))
      }
      setBatchOptions([...batches].map(([id, code]) => ({ id, code })).sort((a, b) => a.code.localeCompare(b.code)))
      setLanguageOptions([...langs].sort())
    }
  }, [examId, batchId, language])

  useEffect(() => {
    const timer = window.setTimeout(() => void loadExam(), 0)
    return () => window.clearTimeout(timer)
  }, [loadExam])

  useEffect(() => {
    const timer = window.setTimeout(() => void loadResults(), 0)
    return () => window.clearTimeout(timer)
  }, [loadResults])

  // Grading runs in the background after the exam closes: refresh until it finishes.
  const anyGrading = rows?.some((r) => isGrading(r.grading_status)) ?? false
  useEffect(() => {
    if (!anyGrading) return
    const timer = window.setInterval(() => void loadResults(), 3000)
    return () => window.clearInterval(timer)
  }, [anyGrading, loadResults])

  // Question columns: every question assigned to anyone (papers can differ per student).
  const questionColumns = useMemo(() => {
    const seen = new Map<string, string>()
    for (const r of rows ?? []) for (const q of r.questions) if (!seen.has(q.question_id)) seen.set(q.question_id, q.title)
    return [...seen].map(([id, title]) => ({ id, title }))
  }, [rows])

  const sorted = useMemo(() => {
    const list = [...(rows ?? [])]
    const pct = (r: ResultRow) => (r.max_total ? r.total / r.max_total : 0)
    list.sort((a, b) => {
      let diff = 0
      if (sort.key === 'name') diff = a.student.full_name.localeCompare(b.student.full_name)
      if (sort.key === 'batch') diff = (a.batch_code ?? '').localeCompare(b.batch_code ?? '') || a.student.full_name.localeCompare(b.student.full_name)
      if (sort.key === 'total') diff = a.total - b.total
      if (sort.key === 'percent') diff = pct(a) - pct(b)
      return sort.desc ? -diff : diff
    })
    return list
  }, [rows, sort])

  function sortBy(key: SortKey) {
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: key === 'total' || key === 'percent' }))
  }

  const arrow = (key: SortKey) => (sort.key === key ? (sort.desc ? ' ▼' : ' ▲') : '')

  async function regrade() {
    setBusy(true)
    const result = await api.POST('/api/v1/exams/{exam_id}/regrade', {
      params: { path: { exam_id: examId }, query: { everyone: regradeEveryone } },
    })
    setBusy(false)
    if (result.error) {
      notify(errorText(result.error, 'Could not start re-grading.'), 'error')
      return
    }
    const queued = (result.data as { queued?: number }).queued ?? 0
    setRegradeOpen(false)
    notify(queued ? `Re-grading ${queued} session(s)…` : 'Nothing needed re-grading.', queued ? 'success' : 'info')
    void loadResults()
  }

  async function release() {
    setBusy(true)
    const result = await api.POST('/api/v1/exams/{exam_id}/release-results', {
      params: { path: { exam_id: examId } },
    })
    setBusy(false)
    if (result.error) {
      notify(errorText(result.error, 'Could not release results.'), 'error')
      return
    }
    setExam(result.data as ExamInfo)
    setReleaseOpen(false)
    notify('Results released. Students can now see their marks.', 'success')
  }

  const canEdit = exam?.my_caps.includes('edit') ?? false
  const closed = exam?.status === 'closed'
  const th = { cursor: 'pointer', whiteSpace: 'nowrap' as const }

  return (
    <main style={pageStyle}>
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
        <button type="button" onClick={onBackToMonitor}>← Back to Live Monitor</button>
        <button type="button" onClick={onOpenAnswers}>Check answers one by one →</button>
      </div>

      <h1 style={{ margin: '12px 0 4px', fontSize: '28px', lineHeight: 1.25, letterSpacing: 'normal' }}>
        Results{exam ? `: ${exam.title}` : ''}
      </h1>
      {exam && (
        <p style={{ margin: '0 0 12px', color: '#4b5563' }}>
          Exam {exam.status}
          {exam.results_released_at
            ? ` · results released ${fmtTime(exam.results_released_at)}`
            : ' · results not released to students'}
        </p>
      )}

      {error && <p role="alert" style={{ color: '#b91c1c' }}>{error}</p>}

      {/* Actions */}
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '12px' }}>
        {canEdit && (
          <button type="button" onClick={() => setRegradeOpen(true)}>Re-grade…</button>
        )}
        {canEdit && closed && !exam?.results_released_at && (
          <button type="button" onClick={() => setReleaseOpen(true)} style={{ fontWeight: 'bold' }}>
            Release results
          </button>
        )}
        {canEdit && !closed && (
          <span style={{ color: '#6b7280', fontSize: '0.9em' }}>Results can be released after the exam is closed.</span>
        )}
        <button type="button" disabled title="Excel export is not in the API yet.">
          Export to Excel (coming soon)
        </button>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <label>
          Batch{' '}
          <select value={batchId} onChange={(e) => setBatchId(e.target.value)} style={{ padding: '5px' }}>
            <option value="">All batches</option>
            {batchOptions.map((b) => (
              <option key={b.id} value={b.id}>{b.code}</option>
            ))}
          </select>
        </label>
        <label>
          Language{' '}
          <select value={language} onChange={(e) => setLanguage(e.target.value)} style={{ padding: '5px' }}>
            <option value="">All languages</option>
            {languageOptions.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </label>
      </div>

      {anyGrading && (
        <p style={{ background: '#eff6ff', padding: '8px 12px', borderRadius: '6px' }}>
          Grading is still running for some students; their marks show “Grading…” and update automatically.
        </p>
      )}

      {rows === null && !error && <p>Loading results…</p>}
      {rows !== null && rows.length === 0 && (
        <p>No students have started this exam{batchId || language ? ' (with these filters)' : ''}.</p>
      )}

      {rows !== null && rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="monitor-table" style={{ width: '100%', borderCollapse: 'collapse', minWidth: '800px' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '2px solid #d1d5db' }}>
                <th style={th} onClick={() => sortBy('name')}>Student{arrow('name')}</th>
                <th style={th} onClick={() => sortBy('batch')}>Batch{arrow('batch')}</th>
                <th>Status</th>
                <th>Languages</th>
                {questionColumns.map((q) => (
                  <th key={q.id} style={{ maxWidth: '140px' }}>{q.title}</th>
                ))}
                <th style={th} onClick={() => sortBy('total')}>Total{arrow('total')}</th>
                <th style={th} onClick={() => sortBy('percent')}>%{arrow('percent')}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => {
                const grading = isGrading(r.grading_status)
                const pct = r.max_total ? Math.round((r.total / r.max_total) * 1000) / 10 : 0
                return (
                  <tr key={r.session_id} style={{ borderBottom: '1px solid #e5e7eb', verticalAlign: 'top' }}>
                    <td>
                      <button type="button" className="link-button" onClick={() => onOpenSession(r.session_id)}>
                        {r.student.full_name}
                      </button>
                      <div style={{ fontSize: '0.85em', color: '#6b7280' }}>{r.student.college_id ?? r.student.email}</div>
                    </td>
                    <td>{r.batch_code ?? '—'}</td>
                    <td>
                      {STATUS_LABELS[r.status] ?? r.status}
                      {r.finish_reason && (
                        <div style={{ fontSize: '0.85em', color: '#6b7280' }}>{finishReasonText(r.finish_reason)}</div>
                      )}
                      {r.flagged && <div style={{ color: '#991b1b', fontWeight: 'bold', fontSize: '0.85em' }}>⚑ Flagged</div>}
                      {r.grading_status === 'error' && (
                        <div style={{ color: '#b91c1c', fontSize: '0.85em' }}>Grading error: re-grade</div>
                      )}
                    </td>
                    <td>{r.languages.join(', ') || '—'}</td>
                    {questionColumns.map((q) => {
                      const cell = r.questions.find((x) => x.question_id === q.id)
                      return (
                        <td key={q.id} style={{ whiteSpace: 'nowrap' }}>
                          {!cell ? '—' : grading ? '…' : `${fmtMarks(cell.total)} / ${cell.max}`}
                        </td>
                      )
                    })}
                    <td style={{ whiteSpace: 'nowrap', fontWeight: 'bold' }}>
                      {grading ? 'Grading…' : `${fmtMarks(r.total)} / ${r.max_total}`}
                    </td>
                    <td>{grading ? '—' : `${pct}%`}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {regradeOpen && (
        <ConfirmDialog
          title="Re-grade submissions?"
          confirmLabel={regradeEveryone ? 'Re-grade everyone' : 'Re-grade failed ones'}
          busy={busy}
          onCancel={() => setRegradeOpen(false)}
          onConfirm={() => void regrade()}
        >
          <p style={{ marginTop: 0 }}>Grading runs again in the background with the questions’ <strong>current</strong> tests.</p>
          <ul>
            <li>
              <strong>Affected:</strong>{' '}
              {regradeEveryone
                ? 'every submitted or ended session.'
                : 'only sessions whose grading failed or never finished.'}{' '}
              Students still taking the exam are not affected.
            </li>
            <li><strong>All questions</strong> of each affected session are re-graded (you can’t pick single questions).</li>
            <li><strong>Manual marks and overrides are kept;</strong> only the automatic part is recalculated.</li>
          </ul>
          <label>
            <input type="checkbox" checked={regradeEveryone} onChange={(e) => setRegradeEveryone(e.target.checked)} />{' '}
            Re-grade everyone (e.g. after fixing a wrong test)
          </label>
        </ConfirmDialog>
      )}

      {releaseOpen && (
        <ConfirmDialog
          title="Release results?"
          confirmLabel="Release results"
          busy={busy}
          onCancel={() => setReleaseOpen(false)}
          onConfirm={() => void release()}
        >
          <p style={{ margin: 0 }}>
            <strong>Students will see their marks.</strong> Check for “Grading…” or grading errors first.
          </p>
        </ConfirmDialog>
      )}
    </main>
  )
}
