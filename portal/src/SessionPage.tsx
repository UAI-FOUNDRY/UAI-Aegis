import { useCallback, useEffect, useState } from 'react'
import { api } from './api/client'
import { errorText } from './apiErrors'
import CodeBlock from './components/CodeBlock'
import MarksForm from './components/MarksForm'
import RunResults from './components/RunResults'
import {
  fmtDuration,
  fmtMarks,
  fmtTime,
  isGrading,
  runKindText,
  verdictSummary,
  type AnswerOut,
  type ExamInfo,
  type SessionDetail,
} from './lib/grading'
import {
  STATUS_COLORS,
  STATUS_LABELS,
  eventText,
  finishReasonText,
  lockReasonText,
} from './lib/proctorText'
import type { MonitorRow } from './lib/liveMonitor'

type Props = {
  examId: string
  sessionId: string
  onBackToMonitor: () => void
  onOpenResults: () => void
}

const pageStyle = {
  maxWidth: '1300px',
  margin: '24px auto',
  padding: '16px',
  fontFamily: 'Arial, sans-serif',
  textAlign: 'left' as const, // #root centres text (index.css)
}

const panelStyle = {
  border: '1px solid #e5e7eb',
  borderRadius: '10px',
  padding: '12px 14px',
  background: '#ffffff',
}

const SEVERITY_COLOR: Record<string, string> = {
  info: '#6b7280',
  minor: '#b45309',
  severe: '#b91c1c',
}

type TimelineItem = {
  at: string
  key: string
  title: string
  detail?: string
  color: string
}

/** Event details worth showing, in words (only keys the backend actually sends). */
function eventDetail(details: Record<string, unknown>): string {
  const parts: string[] = []
  const d = details as Record<string, string | number | boolean | undefined>
  if (d.reason) parts.push(`reason: ${String(d.reason)}`)
  if (d.by) parts.push(`by ${String(d.by)}`)
  if (typeof d.extra_minutes === 'number' && d.extra_minutes > 0) parts.push(`+${d.extra_minutes} min`)
  if (d.reset_strikes === true) parts.push('strikes reset')
  if (d.device_change) parts.push(`new laptop ${String(d.device_change)}`)
  if (d.lock_reason) parts.push(`was: ${lockReasonText(String(d.lock_reason))}`)
  if (d.process) parts.push(`process: ${String(d.process)}`)
  return parts.join(' · ')
}

export default function SessionPage({ examId, sessionId, onBackToMonitor, onOpenResults }: Props) {
  const [exam, setExam] = useState<ExamInfo | null>(null)
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [error, setError] = useState('')
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [showTestsFor, setShowTestsFor] = useState<Record<string, boolean>>({})
  const [batchCode, setBatchCode] = useState<string | null>(null)

  const load = useCallback(async () => {
    const [examResult, sessionResult] = await Promise.all([
      api.GET('/api/v1/exams/{exam_id}', { params: { path: { exam_id: examId } } }),
      api.GET('/api/v1/exams/{exam_id}/sessions/{session_id}', {
        params: { path: { exam_id: examId, session_id: sessionId } },
      }),
    ])
    if (sessionResult.error) {
      setError(
        sessionResult.response.status === 404
          ? 'Not found or you don’t have access.'
          : errorText(sessionResult.error, 'Could not load the session.'),
      )
      return
    }
    if (examResult.data) setExam(examResult.data as ExamInfo)
    const data = sessionResult.data as SessionDetail
    setDetail(data)
    // The session-detail endpoint leaves batch_code empty; the monitor list has it.
    if (!data.row.batch_code) {
      const monitor = await api.GET('/api/v1/exams/{exam_id}/monitor', {
        params: { path: { exam_id: examId } },
      })
      const rows = (monitor.data as { rows?: MonitorRow[] } | undefined)?.rows ?? []
      const match = rows.find((r) => r.student.id === data.row.student.id)
      if (match?.batch_code) setBatchCode(match.batch_code)
    }
  }, [examId, sessionId])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  // While grading runs in the background, refresh until it finishes.
  const grading = isGrading(detail?.row.grading_status)
  useEffect(() => {
    if (!grading) return
    const timer = window.setInterval(() => void load(), 3000)
    return () => window.clearInterval(timer)
  }, [grading, load])

  function replaceAnswer(updated: AnswerOut) {
    setDetail((current) =>
      current
        ? { ...current, answers: current.answers.map((a) => (a.question_id === updated.question_id ? updated : a)) }
        : current,
    )
  }

  if (error) {
    return (
      <main style={pageStyle}>
        <button type="button" onClick={onBackToMonitor}>← Back to Live Monitor</button>
        <p role="alert" style={{ color: '#b91c1c' }}>{error}</p>
      </main>
    )
  }
  if (!detail) {
    return (
      <main style={pageStyle}>
        <button type="button" onClick={onBackToMonitor}>← Back to Live Monitor</button>
        <p>Loading session…</p>
      </main>
    )
  }

  const { row, events, answers, runs } = detail
  const canGrade = exam?.my_caps.includes('grade') ?? false
  const finished = row.status === 'submitted' || row.status === 'terminated'
  const colors = STATUS_COLORS[row.status] ?? STATUS_COLORS.not_started
  const titleFor = (questionId: string) => answers.find((a) => a.question_id === questionId)?.title ?? 'Question'
  const total = answers.reduce((n, a) => n + a.total, 0)
  const maxTotal = answers.reduce((n, a) => n + a.marks, 0)
  const deviceEvents = events.filter((e) => e.kind === 'device_changed' || e.details?.device_change)

  // Timeline: real session times, proctoring events and runs, in order.
  const timeline: TimelineItem[] = []
  if (row.started_at) timeline.push({ at: row.started_at, key: 'start', title: 'Joined the exam', color: '#166534' })
  for (const e of events) {
    timeline.push({
      at: e.created_at,
      key: `e-${e.id}`,
      title: `${eventText(e.kind)}${e.caused_lock ? ' → locked' : ''}${e.severity === 'minor' && !e.caused_lock && e.source !== 'staff' ? ' (strike)' : ''}`,
      detail: eventDetail(e.details ?? {}),
      color: e.source === 'staff' ? '#1d4ed8' : SEVERITY_COLOR[e.severity] ?? '#374151',
    })
  }
  for (const r of runs) {
    timeline.push({
      at: r.created_at,
      key: `r-${r.id}`,
      title: `${runKindText(r.kind)}: ${titleFor(r.question_id)}`,
      detail: verdictSummary(r.verdicts),
      color: '#4b5563',
    })
  }
  if (row.finished_at) {
    timeline.push({ at: row.finished_at, key: 'end', title: finishReasonText(row.finish_reason) || 'Finished', color: '#1e40af' })
  }
  timeline.sort((a, b) => Date.parse(a.at) - Date.parse(b.at))

  const runsByQuestion = (questionId: string) => runs.filter((r) => r.question_id === questionId)
  const latestFinal = (questionId: string) =>
    [...runsByQuestion(questionId)].reverse().find((r) => r.kind === 'final')

  return (
    <main style={pageStyle}>
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
        <button type="button" onClick={onBackToMonitor}>← Back to Live Monitor</button>
        <button type="button" onClick={onOpenResults}>Results →</button>
      </div>

      {/* Header */}
      <section style={{ ...panelStyle, marginTop: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', alignItems: 'baseline' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: '26px', lineHeight: 1.25, letterSpacing: 'normal' }}>
              {row.student.full_name}
            </h1>
            <div style={{ color: '#4b5563' }}>
              {row.student.college_id ? `${row.student.college_id} · ` : ''}
              {row.student.email}
              {(row.batch_code ?? batchCode) ? ` · ${row.batch_code ?? batchCode}` : ''}
            </div>
            {exam && <div style={{ color: '#4b5563', marginTop: '2px' }}>{exam.title} ({exam.status})</div>}
          </div>
          <div style={{ textAlign: 'right' }}>
            <span style={{ padding: '3px 10px', borderRadius: '999px', background: colors.bg, color: colors.fg, fontWeight: 'bold' }}>
              {STATUS_LABELS[row.status] ?? row.status}
            </span>
            {row.flagged && <span style={{ marginLeft: '8px', color: '#991b1b', fontWeight: 'bold' }}>⚑ Flagged</span>}
            <div style={{ fontSize: '1.4em', fontWeight: 'bold', marginTop: '6px' }}>
              {grading ? 'Grading…' : `${fmtMarks(total)} / ${maxTotal}`}
            </div>
          </div>
        </div>
        <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '6px 16px', margin: '12px 0 0' }}>
          <div><dt style={{ color: '#6b7280' }}>Started</dt><dd style={{ margin: 0 }}>{fmtTime(row.started_at)}</dd></div>
          <div><dt style={{ color: '#6b7280' }}>Finished</dt><dd style={{ margin: 0 }}>{fmtTime(row.finished_at)}</dd></div>
          <div><dt style={{ color: '#6b7280' }}>Time spent</dt><dd style={{ margin: 0 }}>{fmtDuration(row.started_at, row.finished_at ?? (finished ? null : new Date().toISOString()))}</dd></div>
          <div><dt style={{ color: '#6b7280' }}>Deadline</dt><dd style={{ margin: 0 }}>{fmtTime(row.deadline_at)}</dd></div>
          <div><dt style={{ color: '#6b7280' }}>How it ended</dt><dd style={{ margin: 0 }}>{finishReasonText(row.finish_reason) || '—'}</dd></div>
          <div><dt style={{ color: '#6b7280' }}>Strikes</dt><dd style={{ margin: 0 }}>{row.strikes}</dd></div>
          <div><dt style={{ color: '#6b7280' }}>Lock reason</dt><dd style={{ margin: 0 }}>{lockReasonText(row.lock_reason) || '—'}</dd></div>
          <div><dt style={{ color: '#6b7280' }}>Laptop</dt><dd style={{ margin: 0 }}>
            {row.device_change_pending ? 'Switch pending approval' : deviceEvents.length ? `${deviceEvents.length} change event(s)` : 'No changes'}
          </dd></div>
          <div><dt style={{ color: '#6b7280' }}>Grading</dt><dd style={{ margin: 0 }}>{row.grading_status ?? 'Not started'}</dd></div>
        </dl>
      </section>

      {grading && (
        <p style={{ background: '#eff6ff', padding: '8px 12px', borderRadius: '6px' }}>
          Grading is running in the background; this page refreshes until it finishes.
        </p>
      )}

      <div className="session-grid" style={{ marginTop: '16px' }}>
        {/* Left: timeline */}
        <section style={panelStyle} aria-labelledby="timeline-heading">
          <h2 id="timeline-heading" style={{ fontSize: '18px', marginTop: 0 }}>Timeline</h2>
          {timeline.length === 0 && <p style={{ color: '#6b7280' }}>Nothing recorded yet.</p>}
          <ol style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {timeline.map((item) => (
              <li key={item.key} style={{ borderLeft: `3px solid ${item.color}`, padding: '2px 0 8px 10px', marginBottom: '4px' }}>
                <div style={{ fontSize: '0.8em', color: '#6b7280' }}>{new Date(item.at).toLocaleTimeString()}</div>
                <div style={{ color: item.color, fontWeight: 'bold' }}>{item.title}</div>
                {item.detail && <div style={{ fontSize: '0.85em', color: '#4b5563' }}>{item.detail}</div>}
              </li>
            ))}
          </ol>
        </section>

        {/* Middle: answers */}
        <section aria-labelledby="answers-heading">
          <h2 id="answers-heading" style={{ fontSize: '18px', marginTop: 0 }}>Answers</h2>
          {answers.length === 0 && <p style={{ color: '#6b7280' }}>No questions were assigned.</p>}
          {answers.map((a) => {
            const final = latestFinal(a.question_id)
            return (
              <article key={a.question_id} style={{ ...panelStyle, marginBottom: '14px' }}>
                <h3 style={{ margin: '0 0 4px' }}>Q{a.ordinal}. {a.title}</h3>
                <p style={{ margin: '0 0 8px', color: '#4b5563', fontSize: '0.9em' }}>
                  {a.language ?? 'no language'} · saved {fmtTime(a.updated_at)}
                  {a.revision !== null ? ` · revision ${a.revision}` : ''}
                  {a.is_final ? ' · final' : a.code !== null ? ' · draft (not handed in)' : ''}
                </p>
                {a.code === null ? (
                  <p style={{ color: '#6b7280', fontStyle: 'italic' }}>The student saved no answer for this question.</p>
                ) : (
                  <CodeBlock code={a.code} language={a.language} caption="Final answer" />
                )}

                <div style={{ marginTop: '8px' }}>
                  <strong>Automatic grading: </strong>
                  {grading
                    ? 'Grading…'
                    : a.auto_details?.verdicts
                      ? verdictSummary(a.auto_details.verdicts)
                      : 'Not graded'}
                  {a.auto_details?.compile_output && (
                    <details style={{ marginTop: '4px' }}>
                      <summary>Compiler output</summary>
                      <pre style={{ whiteSpace: 'pre-wrap', color: '#b91c1c' }}>{a.auto_details.compile_output}</pre>
                    </details>
                  )}
                  {final && (
                    <div style={{ marginTop: '6px' }}>
                      <button
                        type="button"
                        onClick={() => setShowTestsFor((s) => ({ ...s, [a.question_id]: !s[a.question_id] }))}
                      >
                        {showTestsFor[a.question_id] ? 'Hide test results' : 'Show test results'}
                      </button>
                      {showTestsFor[a.question_id] && (
                        <div style={{ marginTop: '8px' }}>
                          <RunResults examId={examId} sessionId={sessionId} runId={final.id} />
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {a.code !== null && (
                  <MarksForm
                    key={`${a.question_id}-${a.total}-${a.override_marks}-${a.manual_marks}`}
                    examId={examId}
                    sessionId={sessionId}
                    answer={a}
                    canGrade={canGrade}
                    sessionFinished={finished}
                    onSaved={replaceAnswer}
                  />
                )}
              </article>
            )
          })}
        </section>

        {/* Right: run history */}
        <section style={panelStyle} aria-labelledby="runs-heading">
          <h2 id="runs-heading" style={{ fontSize: '18px', marginTop: 0 }}>Run history</h2>
          {runs.length === 0 && <p style={{ color: '#6b7280' }}>No runs.</p>}
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {[...runs].reverse().map((r) => (
              <li key={r.id} style={{ marginBottom: '6px' }}>
                <button
                  type="button"
                  className="link-button"
                  aria-pressed={selectedRun === r.id}
                  onClick={() => setSelectedRun(selectedRun === r.id ? null : r.id)}
                  style={{ fontWeight: selectedRun === r.id ? 'bold' : 'normal' }}
                >
                  {new Date(r.created_at).toLocaleTimeString()} · {runKindText(r.kind)}
                </button>
                <div style={{ fontSize: '0.85em', color: '#4b5563' }}>
                  {titleFor(r.question_id)} · {r.language} · {verdictSummary(r.verdicts)}
                </div>
              </li>
            ))}
          </ul>
          {selectedRun && (
            <div style={{ marginTop: '10px', borderTop: '1px solid #e5e7eb', paddingTop: '10px' }}>
              <RunResults key={selectedRun} examId={examId} sessionId={sessionId} runId={selectedRun} showCode />
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
