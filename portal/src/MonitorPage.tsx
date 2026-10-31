import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from './api/client'
import { errorText } from './apiErrors'
import NumberInput from './components/NumberInput'
import { useToast } from './components/ToastProvider'
import { useLiveMonitor } from './hooks/useLiveMonitor'
import type { MonitorRow } from './lib/liveMonitor'
import { STATUS_COLORS, STATUS_LABELS, finishReasonText, lockReasonText } from './lib/proctorText'

type Exam = {
  id: string
  subject_id: string
  title: string
  status: 'draft' | 'live' | 'closed'
  duration_minutes: number
  policy: Record<string, unknown>
  my_caps: string[]
}

type Props = {
  examId: string
  onBack: (subjectId: string | null) => void
  onOpenSession: (sessionId: string) => void
  onOpenResults: () => void
}

type Action =
  | { kind: 'lock'; row: MonitorRow }
  | { kind: 'resume'; row: MonitorRow }
  | { kind: 'terminate'; row: MonitorRow }

function formatRemaining(ms: number): string {
  if (ms <= 0) return 'Time up'
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

const pageStyle = {
  maxWidth: '1200px',
  margin: '24px auto',
  padding: '16px',
  fontFamily: 'Arial, sans-serif',
  // #root centres text and h1 is 56px with a tight line height (index.css);
  // without this the title overlapped the Back button.
  textAlign: 'left' as const,
}

const titleStyle = {
  margin: 0,
  fontSize: '30px',
  lineHeight: 1.25,
  letterSpacing: 'normal',
}

const dangerButton = {
  color: '#ffffff',
  background: '#b91c1c',
  border: '1px solid #991b1b',
  borderRadius: '6px',
  padding: '6px 14px',
  fontWeight: 'bold' as const,
}

function Dialog({
  title,
  children,
  onClose,
  busy,
}: {
  title: string
  children: ReactNode
  onClose: () => void
  busy: boolean
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, busy])

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(0, 0, 0, 0.55)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px',
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={{
          background: '#ffffff',
          color: '#111827',
          borderRadius: '12px',
          padding: '22px',
          maxWidth: '500px',
          width: '100%',
          boxShadow: '0 20px 40px rgba(0, 0, 0, 0.3)',
        }}
      >
        <h3 style={{ marginTop: 0 }}>{title}</h3>
        {children}
      </div>
    </div>
  )
}

export default function MonitorPage({ examId, onBack, onOpenSession, onOpenResults }: Props) {
  const { notify } = useToast()

  const [exam, setExam] = useState<Exam | null>(null)
  const [examError, setExamError] = useState('')
  const [batchCode, setBatchCode] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const [soundOn, setSoundOn] = useState(false)
  const audioRef = useRef<AudioContext | null>(null)

  const [action, setAction] = useState<Action | null>(null)
  const [busy, setBusy] = useState(false)
  const [lockReason, setLockReason] = useState('locked_by_professor')
  const [extraMinutes, setExtraMinutes] = useState(0)
  const [resetStrikes, setResetStrikes] = useState(true)
  const [approveDevice, setApproveDevice] = useState(true)
  const [terminateReason, setTerminateReason] = useState('')
  const [closeOpen, setCloseOpen] = useState(false)

  const beep = useCallback(() => {
    const ctx = audioRef.current
    if (!ctx) return
    // Two short tones, so it is noticeable but not alarming.
    ;[0, 0.22].forEach((offset, i) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.frequency.value = i === 0 ? 880 : 660
      gain.gain.setValueAtTime(0.15, ctx.currentTime + offset)
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + offset + 0.18)
      osc.connect(gain).connect(ctx.destination)
      osc.start(ctx.currentTime + offset)
      osc.stop(ctx.currentTime + offset + 0.2)
    })
  }, [])

  const onAlert = useCallback(
    (row: MonitorRow, change: string) => {
      if (soundOn) beep()
      notify(
        change === 'device_change'
          ? `${row.student.full_name} switched laptop; approve or reject when resuming.`
          : `${row.student.full_name} was locked: ${lockReasonText(row.lock_reason)}.`,
        'error',
      )
    },
    [soundOn, beep, notify],
  )

  const monitor = useLiveMonitor({ examId, batchCode, onAlert })

  const loadExam = useCallback(async () => {
    const result = await api.GET('/api/v1/exams/{exam_id}', {
      params: { path: { exam_id: examId } },
    })
    if (result.error) {
      setExamError(
        result.response.status === 404
          ? 'Not found or you don’t have access.'
          : errorText(result.error, 'Could not load the exam.'),
      )
      return
    }
    setExam(result.data as Exam)
  }, [examId])

  useEffect(() => {
    const timer = window.setTimeout(() => void loadExam(), 0)
    return () => window.clearTimeout(timer)
  }, [loadExam])

  // Keep the exam header in step when the monitor reports a status change.
  const examStatus = monitor.examStatus ?? exam?.status ?? null
  useEffect(() => {
    if (exam && monitor.examStatus && monitor.examStatus !== exam.status) {
      const timer = window.setTimeout(() => void loadExam(), 0)
      return () => window.clearTimeout(timer)
    }
  }, [exam, monitor.examStatus, loadExam])

  // Tick once a second for the remaining-time column.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    return () => {
      void audioRef.current?.close()
    }
  }, [])

  function toggleSound() {
    if (!soundOn) {
      // Browsers only allow audio after a click, so create it here.
      if (!audioRef.current) audioRef.current = new AudioContext()
      void audioRef.current.resume()
      setSoundOn(true)
      beep()
    } else {
      setSoundOn(false)
    }
  }

  function openAction(next: Action) {
    setLockReason('locked_by_professor')
    setExtraMinutes(0)
    setResetStrikes(true)
    setApproveDevice(true)
    setTerminateReason('')
    setAction(next)
  }

  const closeAction = useCallback(() => setAction(null), [])

  async function runAction() {
    if (!action?.row.session_id) return
    const path = { exam_id: examId, session_id: action.row.session_id }
    setBusy(true)
    try {
      const result =
        action.kind === 'lock'
          ? await api.POST('/api/v1/exams/{exam_id}/sessions/{session_id}/lock', {
              params: { path },
              body: { reason: lockReason.trim() || 'locked_by_professor' },
            })
          : action.kind === 'resume'
            ? await api.POST('/api/v1/exams/{exam_id}/sessions/{session_id}/resume', {
                params: { path },
                body: {
                  extra_minutes: extraMinutes,
                  reset_strikes: resetStrikes,
                  approve_new_device: approveDevice,
                },
              })
            : await api.POST('/api/v1/exams/{exam_id}/sessions/{session_id}/terminate', {
                params: { path },
                body: { reason: terminateReason.trim() },
              })
      if (result.error) throw new Error(errorText(result.error, 'The action failed.'))
      // Apply the returned row now; do not wait for the push.
      monitor.applyRow(result.data as MonitorRow)
      notify(
        action.kind === 'lock'
          ? `${action.row.student.full_name} locked.`
          : action.kind === 'resume'
            ? `${action.row.student.full_name} resumed.`
            : `${action.row.student.full_name}'s exam was ended.`,
        'success',
      )
      setAction(null)
    } catch (err) {
      notify(err instanceof Error ? err.message : 'The action failed.', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function closeExam() {
    setBusy(true)
    const result = await api.POST('/api/v1/exams/{exam_id}/close', {
      params: { path: { exam_id: examId } },
    })
    setBusy(false)
    if (result.error) {
      notify(errorText(result.error, 'Could not close the exam.'), 'error')
      return
    }
    setExam(result.data as Exam)
    setCloseOpen(false)
    notify('The exam is closed. Unfinished sessions were submitted and are being graded.', 'success')
    void monitor.refresh()
  }

  const canProctor = exam?.my_caps.includes('proctor') ?? false
  const isLive = examStatus === 'live'
  const maxStrikes = Number(exam?.policy.max_strikes ?? 3)
  const serverNow = now + monitor.clockOffsetMs

  const counterOrder = ['not_started', 'active', 'locked', 'submitted', 'terminated']

  if (examError) {
    return (
      <main style={pageStyle}>
        <button type="button" onClick={() => onBack(null)}>← Back</button>
        <p role="alert" style={{ color: '#b91c1c' }}>{examError}</p>
      </main>
    )
  }

  return (
    <main style={pageStyle}>
      <button type="button" onClick={() => onBack(exam?.subject_id ?? null)}>
        ← Back to exams
      </button>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap', marginTop: '12px' }}>
        <h1 style={titleStyle}>
          {exam?.title ?? 'Live monitor'}{' '}
          {examStatus && (
            <span style={{ fontSize: '0.55em', verticalAlign: 'middle', padding: '3px 10px', borderRadius: '999px', background: examStatus === 'live' ? '#dcfce7' : '#f3f4f6' }}>
              {examStatus}
            </span>
          )}
        </h1>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button type="button" onClick={onOpenResults}>
            Results →
          </button>
          <button type="button" onClick={toggleSound} aria-pressed={soundOn}>
            {soundOn ? '🔔 Alert sound on' : '🔕 Turn on alert sound'}
          </button>
          {canProctor && isLive && (
            <button type="button" style={dangerButton} onClick={() => setCloseOpen(true)}>
              Close exam
            </button>
          )}
        </div>
      </div>

      {/* Connection state */}
      <p style={{ margin: '10px 0', fontSize: '0.9em', color: '#4b5563' }}>
        {monitor.liveStatus === 'live' && <span style={{ color: '#166534' }}>● Live updates on</span>}
        {(monitor.liveStatus === 'connecting' || monitor.liveStatus === 'reconnecting') && (
          <span style={{ color: '#92400e' }}>
            ● Live updates unavailable; refreshing every 5 seconds while reconnecting…
          </span>
        )}
        {monitor.liveStatus === 'too_many' && (
          <span style={{ color: '#92400e' }}>
            ● Too many live monitors are open for your account. Close other monitor tabs;
            refreshing every 5 seconds meanwhile.
          </span>
        )}
        {monitor.liveStatus === 'forbidden' && (
          <span style={{ color: '#991b1b' }}>
            ● Live updates stopped: you no longer have access or were signed out.
          </span>
        )}
      </p>
      {monitor.pollError && (
        <p role="alert" style={{ color: '#b91c1c' }}>{monitor.pollError}</p>
      )}

      {/* Counters */}
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', margin: '12px 0' }}>
        {counterOrder.map((status) => (
          <div
            key={status}
            style={{
              minWidth: '110px',
              padding: '10px 14px',
              borderRadius: '10px',
              background: STATUS_COLORS[status].bg,
              color: STATUS_COLORS[status].fg,
            }}
          >
            <div style={{ fontSize: '1.6em', fontWeight: 'bold' }}>{monitor.counts[status] ?? 0}</div>
            <div>{STATUS_LABELS[status]}</div>
          </div>
        ))}
        <div
          style={{
            minWidth: '110px',
            padding: '10px 14px',
            borderRadius: '10px',
            background: (monitor.counts.disconnected ?? 0) > 0 ? '#fef3c7' : '#f9fafb',
            color: '#92400e',
          }}
        >
          <div style={{ fontSize: '1.6em', fontWeight: 'bold' }}>{monitor.counts.disconnected ?? 0}</div>
          <div>Disconnected</div>
        </div>
      </div>

      {/* Batch filter */}
      {monitor.batchCodes.length > 0 && (
        <label style={{ display: 'inline-flex', gap: '8px', alignItems: 'center', marginBottom: '10px' }}>
          Batch
          <select
            value={batchCode ?? ''}
            onChange={(e) => setBatchCode(e.target.value || null)}
            style={{ padding: '6px' }}
          >
            <option value="">All batches</option>
            {monitor.batchCodes.map((code) => (
              <option key={code} value={code}>{code}</option>
            ))}
          </select>
        </label>
      )}

      {!monitor.loaded && !monitor.pollError && <p>Loading students…</p>}
      {monitor.loaded && monitor.rows.length === 0 && (
        <p>No students are enrolled{batchCode ? ` in ${batchCode}` : ''}.</p>
      )}

      {monitor.rows.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="monitor-table" style={{ width: '100%', borderCollapse: 'collapse', minWidth: '900px' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '2px solid #d1d5db' }}>
                <th style={{ padding: '8px' }}>Student</th>
                <th>Batch</th>
                <th>Status</th>
                <th>Online</th>
                <th>Strikes</th>
                <th>Lock reason / notes</th>
                <th>Time left</th>
                {canProctor && isLive && <th>Actions</th>}
              </tr>
            </thead>
            <tbody>
              {monitor.rows.map((row) => {
                const locked = row.status === 'locked'
                const flashing = row.student.id in monitor.flashing
                const running = row.status === 'active' || row.status === 'locked'
                const remaining = row.deadline_at ? Date.parse(row.deadline_at) - serverNow : null
                const colors = STATUS_COLORS[row.status] ?? STATUS_COLORS.not_started
                return (
                  <tr
                    key={row.student.id}
                    className={flashing ? 'monitor-row--flash' : undefined}
                    style={{
                      borderBottom: '1px solid #e5e7eb',
                      background: locked ? '#fee2e2' : undefined,
                      verticalAlign: 'top',
                    }}
                  >
                    <td style={{ padding: '8px' }}>
                      {row.session_id ? (
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => onOpenSession(row.session_id as string)}
                          title="Open this student's session: timeline, code and marks"
                        >
                          <strong>{row.student.full_name}</strong>
                        </button>
                      ) : (
                        <strong>{row.student.full_name}</strong>
                      )}
                      <div style={{ fontSize: '0.85em', color: '#6b7280' }}>
                        {row.student.college_id ?? row.student.email}
                      </div>
                    </td>
                    <td>{row.batch_code ?? '—'}</td>
                    <td>
                      <span style={{ padding: '2px 8px', borderRadius: '999px', background: colors.bg, color: colors.fg, fontWeight: 'bold', whiteSpace: 'nowrap' }}>
                        {STATUS_LABELS[row.status] ?? row.status}
                      </span>
                      {row.flagged && (
                        <div style={{ marginTop: '4px', color: '#991b1b', fontSize: '0.85em', fontWeight: 'bold' }}>
                          ⚑ Flagged
                        </div>
                      )}
                    </td>
                    <td>
                      {running ? (
                        <span title={row.connected ? 'Connected' : 'Not connected'} style={{ color: row.connected ? '#16a34a' : '#9ca3af', fontSize: '1.3em' }}>
                          ●
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>
                      {row.session_id ? `${row.strikes} / ${maxStrikes}` : '—'}
                    </td>
                    <td style={{ maxWidth: '260px' }}>
                      {locked && <div style={{ color: '#991b1b', fontWeight: 'bold' }}>{lockReasonText(row.lock_reason)}</div>}
                      {row.device_change_pending && (
                        <span style={{ display: 'inline-block', marginTop: '4px', padding: '2px 8px', borderRadius: '999px', background: '#fef3c7', color: '#92400e', fontSize: '0.85em' }}>
                          Device change pending
                        </span>
                      )}
                      {row.finish_reason && !running && (
                        <div style={{ fontSize: '0.85em', color: '#6b7280' }}>
                          {finishReasonText(row.finish_reason)}
                        </div>
                      )}
                    </td>
                    <td style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                      {running && remaining !== null ? (
                        <span style={{ color: remaining < 5 * 60_000 ? '#b91c1c' : undefined }}>
                          {formatRemaining(remaining)}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                    {canProctor && isLive && (
                      <td style={{ whiteSpace: 'nowrap' }}>
                        {row.status === 'active' && (
                          <button type="button" onClick={() => openAction({ kind: 'lock', row })}>Lock</button>
                        )}
                        {row.status === 'locked' && (
                          <button type="button" style={{ fontWeight: 'bold' }} onClick={() => openAction({ kind: 'resume', row })}>
                            Resume
                          </button>
                        )}{' '}
                        {running && (
                          <button type="button" onClick={() => openAction({ kind: 'terminate', row })}>
                            Terminate
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <p style={{ fontSize: '0.85em', color: '#6b7280', marginTop: '14px' }}>
        Click a student’s name to see their timeline, code and marks.
      </p>

      {/* Row action dialogs */}
      {action?.kind === 'lock' && (
        <Dialog title={`Lock ${action.row.student.full_name}?`} onClose={closeAction} busy={busy}>
          <p>The student can’t save, run or submit until you resume them. Their timer keeps running.</p>
          <label style={{ display: 'block' }}>
            Reason (shown in the timeline)
            <input
              value={lockReason}
              maxLength={60}
              onChange={(e) => setLockReason(e.target.value)}
              style={{ display: 'block', width: '100%', boxSizing: 'border-box', padding: '8px', margin: '6px 0 14px' }}
            />
          </label>
          <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
            <button type="button" onClick={closeAction} disabled={busy}>Cancel</button>
            <button type="button" style={dangerButton} onClick={() => void runAction()} disabled={busy}>
              {busy ? 'Locking…' : 'Lock'}
            </button>
          </div>
        </Dialog>
      )}

      {action?.kind === 'resume' && (() => {
        const remaining = action.row.deadline_at ? Date.parse(action.row.deadline_at) - serverNow : 0
        const timeUp = remaining <= 0
        return (
          <Dialog title={`Resume ${action.row.student.full_name}`} onClose={closeAction} busy={busy}>
            <p style={{ marginTop: 0 }}>
              Locked: {lockReasonText(action.row.lock_reason)}. Time left: {formatRemaining(remaining)}.
            </p>
            <label style={{ display: 'block' }}>
              Extra minutes (0–240)
              <NumberInput
                min={0}
                max={240}
                value={extraMinutes}
                onChange={setExtraMinutes}
                style={{ display: 'block', width: '120px', padding: '8px', margin: '6px 0 6px' }}
              />
            </label>
            {timeUp && extraMinutes === 0 && (
              <p style={{ color: '#b91c1c', marginTop: 0 }}>Their time is over; add minutes to resume.</p>
            )}
            <label style={{ display: 'block', margin: '8px 0' }}>
              <input type="checkbox" checked={resetStrikes} onChange={(e) => setResetStrikes(e.target.checked)} />{' '}
              Reset strikes to 0
            </label>
            {action.row.device_change_pending && (
              <label style={{ display: 'block', margin: '8px 0' }}>
                <input type="checkbox" checked={approveDevice} onChange={(e) => setApproveDevice(e.target.checked)} />{' '}
                Approve the new laptop (untick to keep them on the original one)
              </label>
            )}
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '14px' }}>
              <button type="button" onClick={closeAction} disabled={busy}>Cancel</button>
              <button
                type="button"
                onClick={() => void runAction()}
                disabled={busy || (timeUp && extraMinutes === 0)}
                style={{ fontWeight: 'bold' }}
              >
                {busy ? 'Resuming…' : 'Resume'}
              </button>
            </div>
          </Dialog>
        )
      })()}

      {action?.kind === 'terminate' && (
        <Dialog title={`End ${action.row.student.full_name}’s exam?`} onClose={closeAction} busy={busy}>
          <p style={{ marginTop: 0 }}>
            Their saved code is submitted and graded now, and the session is flagged.
            They can’t continue. <strong>This cannot be undone.</strong>
          </p>
          <label style={{ display: 'block' }}>
            Reason (required)
            <textarea
              value={terminateReason}
              maxLength={500}
              onChange={(e) => setTerminateReason(e.target.value)}
              style={{ display: 'block', width: '100%', boxSizing: 'border-box', minHeight: '70px', padding: '8px', margin: '6px 0 14px' }}
            />
          </label>
          <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
            <button type="button" onClick={closeAction} disabled={busy}>Cancel</button>
            <button
              type="button"
              style={dangerButton}
              onClick={() => void runAction()}
              disabled={busy || !terminateReason.trim()}
            >
              {busy ? 'Ending…' : 'End exam for this student'}
            </button>
          </div>
        </Dialog>
      )}

      {closeOpen && (
        <Dialog title="Close the exam?" onClose={() => setCloseOpen(false)} busy={busy}>
          <p style={{ marginTop: 0, fontWeight: 'bold', color: '#991b1b' }}>
            This ends the exam for every student.
          </p>
          <ul>
            <li>Everyone still working or locked is submitted with their last saved code.</li>
            <li>No one can join; the access code stops working.</li>
            <li>Answers are graded automatically.</li>
          </ul>
          <p><strong>This cannot be undone. A closed exam cannot be reopened.</strong></p>
          <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
            <button type="button" onClick={() => setCloseOpen(false)} disabled={busy} autoFocus>Cancel</button>
            <button type="button" style={dangerButton} onClick={() => void closeExam()} disabled={busy}>
              {busy ? 'Closing…' : 'Close exam for everyone'}
            </button>
          </div>
        </Dialog>
      )}
    </main>
  )
}
