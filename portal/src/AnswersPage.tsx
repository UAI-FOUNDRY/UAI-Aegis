import { useCallback, useEffect, useState } from 'react'
import { api } from './api/client'
import { errorText } from './apiErrors'
import CodeBlock from './components/CodeBlock'
import MarksForm from './components/MarksForm'
import {
  fmtMarks,
  fmtTime,
  isGrading,
  verdictSummary,
  type AnswerOut,
  type AnswerRow,
  type ExamInfo,
  type ResultRow,
  type SessionDetail,
} from './lib/grading'

type Props = {
  examId: string
  onBackToResults: () => void
  onOpenSession: (sessionId: string) => void
}

const PAGE = 20

const pageStyle = {
  maxWidth: '1100px',
  margin: '24px auto',
  padding: '16px',
  fontFamily: 'Arial, sans-serif',
  textAlign: 'left' as const, // #root centres text (index.css)
}

/** Check every saved answer one after another (GET /exams/{id}/answers, paginated). */
export default function AnswersPage({ examId, onBackToResults, onOpenSession }: Props) {
  const [exam, setExam] = useState<ExamInfo | null>(null)
  const [filters, setFilters] = useState({ language: '', batch_id: '', question_id: '' })
  const [options, setOptions] = useState<{
    languages: string[]
    batches: { id: string; code: string }[]
    questions: { id: string; title: string }[]
  }>({ languages: [], batches: [], questions: [] })

  const [index, setIndex] = useState(0)
  const [total, setTotal] = useState<number | null>(null)
  const [page, setPage] = useState<{ offset: number; items: AnswerRow[] } | null>(null)
  const [error, setError] = useState('')
  // Full session detail per session, for manual_max, notes and grading details.
  const [sessions, setSessions] = useState<Record<string, SessionDetail>>({})

  // Exam + filter options (from the unfiltered results list).
  useEffect(() => {
    let cancelled = false
    void Promise.all([
      api.GET('/api/v1/exams/{exam_id}', { params: { path: { exam_id: examId } } }),
      api.GET('/api/v1/exams/{exam_id}/results', { params: { path: { exam_id: examId } } }),
    ]).then(([examResult, resultsResult]) => {
      if (cancelled) return
      if (examResult.error) {
        setError(examResult.response.status === 404 ? 'Not found or you don’t have access.' : errorText(examResult.error, 'Could not load the exam.'))
        return
      }
      setExam(examResult.data as ExamInfo)
      const rows = (resultsResult.data ?? []) as ResultRow[]
      const batches = new Map<string, string>()
      const langs = new Set<string>()
      const questions = new Map<string, string>()
      for (const r of rows) {
        if (r.batch_id) batches.set(r.batch_id, r.batch_code ?? r.batch_id)
        r.languages.forEach((l) => langs.add(l))
        r.questions.forEach((q) => questions.set(q.question_id, q.title))
      }
      setOptions({
        languages: [...langs].sort(),
        batches: [...batches].map(([id, code]) => ({ id, code })).sort((a, b) => a.code.localeCompare(b.code)),
        questions: [...questions].map(([id, title]) => ({ id, title })),
      })
    })
    return () => {
      cancelled = true
    }
  }, [examId])

  const offset = Math.floor(index / PAGE) * PAGE

  // The page of answers that contains the current index.
  useEffect(() => {
    let cancelled = false
    const query: Record<string, string | number> = { limit: PAGE, offset }
    if (filters.language) query.language = filters.language
    if (filters.batch_id) query.batch_id = filters.batch_id
    if (filters.question_id) query.question_id = filters.question_id
    void api
      .GET('/api/v1/exams/{exam_id}/answers', { params: { path: { exam_id: examId }, query } })
      .then((result) => {
        if (cancelled) return
        if (result.error) {
          setError(errorText(result.error, 'Could not load answers.'))
          return
        }
        const data = result.data as { total: number; items: AnswerRow[] }
        setTotal(data.total)
        setPage({ offset, items: data.items })
      })
    return () => {
      cancelled = true
    }
  }, [examId, filters, offset])

  const current = page && page.offset === offset ? page.items[index - offset] : undefined
  const detail = current ? sessions[current.session_id] : undefined
  const fullAnswer = detail?.answers.find((a) => a.question_id === current?.question_id)

  const loadSession = useCallback(
    async (sessionId: string) => {
      const result = await api.GET('/api/v1/exams/{exam_id}/sessions/{session_id}', {
        params: { path: { exam_id: examId, session_id: sessionId } },
      })
      if (result.data) {
        setSessions((s) => ({ ...s, [sessionId]: result.data as SessionDetail }))
      }
    },
    [examId],
  )

  // Load the current answer's session detail once.
  const currentSessionId = current?.session_id
  const haveDetail = currentSessionId ? currentSessionId in sessions : true
  useEffect(() => {
    if (!currentSessionId || haveDetail) return
    const timer = window.setTimeout(() => void loadSession(currentSessionId), 0)
    return () => window.clearTimeout(timer)
  }, [currentSessionId, haveDetail, loadSession])

  const go = useCallback(
    (delta: number) => {
      if (total === null) return
      setIndex((i) => Math.min(Math.max(i + delta, 0), Math.max(total - 1, 0)))
    },
    [total],
  )

  // Keyboard: → / j = next, ← / k = previous (not while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
      if (e.key === 'ArrowRight' || e.key === 'j') go(1)
      if (e.key === 'ArrowLeft' || e.key === 'k') go(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go])

  function setFilter(name: keyof typeof filters, value: string) {
    setFilters((f) => ({ ...f, [name]: value }))
    setIndex(0)
    setTotal(null)
  }

  function onSaved(updated: AnswerOut) {
    if (!current) return
    setSessions((s) => {
      const d = s[current.session_id]
      if (!d) return s
      return {
        ...s,
        [current.session_id]: { ...d, answers: d.answers.map((a) => (a.question_id === updated.question_id ? updated : a)) },
      }
    })
    setPage((p) =>
      p
        ? {
            ...p,
            items: p.items.map((it) =>
              it.session_id === current.session_id && it.question_id === updated.question_id
                ? { ...it, total: updated.total, manual_marks: updated.manual_marks, override_marks: updated.override_marks }
                : it,
            ),
          }
        : p,
    )
  }

  const canGrade = exam?.my_caps.includes('grade') ?? false
  const finished = detail ? detail.row.status === 'submitted' || detail.row.status === 'terminated' : false

  return (
    <main style={pageStyle}>
      <button type="button" onClick={onBackToResults}>← Back to results</button>
      <h1 style={{ margin: '12px 0 4px', fontSize: '26px', lineHeight: 1.25, letterSpacing: 'normal' }}>
        Check answers{exam ? `: ${exam.title}` : ''}
      </h1>

      <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', margin: '10px 0' }}>
        <label>
          Question{' '}
          <select value={filters.question_id} onChange={(e) => setFilter('question_id', e.target.value)} style={{ padding: '5px' }}>
            <option value="">All questions</option>
            {options.questions.map((q) => <option key={q.id} value={q.id}>{q.title}</option>)}
          </select>
        </label>
        <label>
          Batch{' '}
          <select value={filters.batch_id} onChange={(e) => setFilter('batch_id', e.target.value)} style={{ padding: '5px' }}>
            <option value="">All batches</option>
            {options.batches.map((b) => <option key={b.id} value={b.id}>{b.code}</option>)}
          </select>
        </label>
        <label>
          Language{' '}
          <select value={filters.language} onChange={(e) => setFilter('language', e.target.value)} style={{ padding: '5px' }}>
            <option value="">All languages</option>
            {options.languages.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
      </div>

      {error && <p role="alert" style={{ color: '#b91c1c' }}>{error}</p>}
      {total === null && !error && <p>Loading answers…</p>}
      {total === 0 && <p>No saved answers match these filters.</p>}

      {total !== null && total > 0 && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', margin: '6px 0 12px' }}>
            <button type="button" onClick={() => go(-1)} disabled={index === 0}>← Previous</button>
            <strong>
              Answer {index + 1} of {total}
            </strong>
            <button type="button" onClick={() => go(1)} disabled={index >= total - 1} style={{ fontWeight: 'bold' }}>
              Next →
            </button>
            <span style={{ color: '#6b7280', fontSize: '0.85em' }}>Keys: ← / → (or k / j)</span>
          </div>

          {!current && <p>Loading…</p>}
          {current && (
            <article style={{ border: '1px solid #e5e7eb', borderRadius: '10px', padding: '14px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', flexWrap: 'wrap' }}>
                <div>
                  <h2 style={{ margin: 0, fontSize: '20px' }}>{current.question_title}</h2>
                  <div style={{ color: '#4b5563' }}>
                    <button type="button" className="link-button" onClick={() => onOpenSession(current.session_id)}>
                      {current.student.full_name}
                    </button>
                    {current.student.college_id ? ` · ${current.student.college_id}` : ''}
                    {current.batch_code ? ` · ${current.batch_code}` : ''}
                  </div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: '1.3em', fontWeight: 'bold' }}>
                    {detail && isGrading(detail.row.grading_status) ? 'Grading…' : `${fmtMarks(current.total)} / ${current.marks}`}
                  </div>
                  <div style={{ fontSize: '0.85em', color: '#6b7280' }}>
                    {current.is_final ? 'Final answer' : 'Draft (not handed in)'} · {fmtTime(current.updated_at)}
                  </div>
                </div>
              </div>

              <div style={{ marginTop: '10px' }}>
                <CodeBlock code={current.code} language={current.language} caption={current.language} />
              </div>

              {fullAnswer?.auto_details?.verdicts && (
                <p style={{ margin: '8px 0 0' }}>
                  <strong>Automatic grading:</strong> {verdictSummary(fullAnswer.auto_details.verdicts)}
                </p>
              )}

              {!detail && <p style={{ color: '#6b7280' }}>Loading marks…</p>}
              {detail && fullAnswer && (
                <MarksForm
                  key={`${current.session_id}-${current.question_id}-${fullAnswer.total}-${fullAnswer.override_marks}-${fullAnswer.manual_marks}`}
                  examId={examId}
                  sessionId={current.session_id}
                  answer={fullAnswer}
                  canGrade={canGrade}
                  sessionFinished={finished}
                  onSaved={onSaved}
                />
              )}
            </article>
          )}
        </>
      )}
    </main>
  )
}
