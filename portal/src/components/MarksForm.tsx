import { useState } from 'react'
import { api } from '../api/client'
import { errorText } from '../apiErrors'
import { fmtMarks, type AnswerOut } from '../lib/grading'
import NumberInput from './NumberInput'
import { useToast } from './ToastProvider'

type Props = {
  examId: string
  sessionId: string
  answer: AnswerOut
  /** my_caps includes "grade". */
  canGrade: boolean
  /** Marks can only be given after the student's exam ends (backend rule). */
  sessionFinished: boolean
  onSaved: (answer: AnswerOut) => void
}

/**
 * Marks for one answer: auto marks (from tests) and manual marks shown separately,
 * the total from the backend, and an editor for manual marks or an override.
 * Saves with PUT /exams/{id}/sessions/{sid}/answers/{qid}/marks.
 */
export default function MarksForm({ examId, sessionId, answer, canGrade, sessionFinished, onSaved }: Props) {
  const { notify } = useToast()
  const hasManualPart = answer.manual_max > 0
  const [mode, setMode] = useState<'manual' | 'override'>(
    answer.override_marks !== null || !hasManualPart ? 'override' : 'manual',
  )
  const [manual, setManual] = useState(answer.manual_marks ?? 0)
  const [override, setOverride] = useState(answer.override_marks ?? answer.total)
  const [note, setNote] = useState(answer.manual_note ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const autoMarks = answer.auto_fraction === null ? null : answer.auto_fraction * answer.auto_max

  async function save(body: { manual_marks: number | null; override_marks: number | null; note: string | null }) {
    setSaving(true)
    setError('')
    const result = await api.PUT('/api/v1/exams/{exam_id}/sessions/{session_id}/answers/{question_id}/marks', {
      params: { path: { exam_id: examId, session_id: sessionId, question_id: answer.question_id } },
      body,
    })
    setSaving(false)
    if (result.error) {
      const message = errorText(result.error, 'Could not save the marks.')
      setError(message)
      notify(message, 'error')
      return
    }
    const updated = result.data as AnswerOut
    onSaved(updated)
    notify(`Marks saved for “${answer.title}”: ${fmtMarks(updated.total)} / ${answer.marks}.`, 'success')
  }

  function submit() {
    const trimmed = note.trim()
    if (mode === 'manual') {
      if (manual < 0 || manual > answer.manual_max) {
        setError(`Manual marks must be between 0 and ${fmtMarks(answer.manual_max)}.`)
        return
      }
      // Saving manual marks removes any override (the backend stores both fields as sent).
      void save({ manual_marks: manual, override_marks: null, note: trimmed || null })
    } else {
      if (override < 0 || override > answer.marks) {
        setError(`The override must be between 0 and ${answer.marks}.`)
        return
      }
      if (!trimmed) {
        setError('Add a note explaining the override.')
        return
      }
      void save({ manual_marks: answer.manual_marks, override_marks: override, note: trimmed })
    }
  }

  const cell = { padding: '4px 10px 4px 0' }

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: '8px', padding: '10px 12px', marginTop: '10px' }}>
      <table style={{ borderCollapse: 'collapse', marginBottom: '6px' }}>
        <tbody>
          <tr>
            <td style={cell}>Automatic (tests)</td>
            <td style={cell}>
              <strong>{autoMarks === null ? 'not graded yet' : fmtMarks(autoMarks)}</strong> / {fmtMarks(answer.auto_max)}
            </td>
          </tr>
          <tr>
            <td style={cell}>Manual</td>
            <td style={cell}>
              <strong>{fmtMarks(answer.manual_marks)}</strong> / {fmtMarks(answer.manual_max)}
            </td>
          </tr>
          {answer.override_marks !== null && (
            <tr>
              <td style={cell}>Override</td>
              <td style={{ ...cell, color: '#92400e' }}>
                <strong>{fmtMarks(answer.override_marks)}</strong> (replaces the total)
              </td>
            </tr>
          )}
          <tr>
            <td style={cell}><strong>Total</strong></td>
            <td style={cell}>
              <strong style={{ fontSize: '1.1em' }}>{fmtMarks(answer.total)}</strong> / {answer.marks}
            </td>
          </tr>
        </tbody>
      </table>
      {answer.manual_note && (
        <p style={{ margin: '0 0 8px', fontSize: '0.9em', color: '#4b5563' }}>Note: {answer.manual_note}</p>
      )}

      {canGrade && !sessionFinished && (
        <p style={{ margin: 0, color: '#6b7280', fontSize: '0.9em' }}>
          Marks can be given after the student’s exam ends.
        </p>
      )}

      {canGrade && sessionFinished && (
        <div>
          <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', margin: '6px 0' }}>
            <label>
              <input
                type="radio"
                name={`mode-${answer.question_id}`}
                checked={mode === 'manual'}
                disabled={!hasManualPart}
                onChange={() => setMode('manual')}
              />{' '}
              Manual marks{!hasManualPart && ' (this exam is 100% automatic)'}
            </label>
            <label>
              <input
                type="radio"
                name={`mode-${answer.question_id}`}
                checked={mode === 'override'}
                onChange={() => setMode('override')}
              />{' '}
              Override the total
            </label>
          </div>

          {mode === 'manual' ? (
            <label style={{ display: 'block' }}>
              Manual marks (out of {fmtMarks(answer.manual_max)})
              <NumberInput
                min={0}
                max={answer.manual_max}
                step="0.5"
                value={manual}
                onChange={setManual}
                style={{ display: 'block', width: '120px', padding: '6px', margin: '4px 0 8px' }}
              />
            </label>
          ) : (
            <label style={{ display: 'block' }}>
              Total marks (out of {answer.marks})
              <NumberInput
                min={0}
                max={answer.marks}
                step="0.5"
                value={override}
                onChange={setOverride}
                style={{ display: 'block', width: '120px', padding: '6px', margin: '4px 0 8px' }}
              />
            </label>
          )}
          <label style={{ display: 'block' }}>
            Note {mode === 'override' ? '(required)' : '(optional)'}
            <textarea
              value={note}
              maxLength={2000}
              onChange={(e) => setNote(e.target.value)}
              style={{ display: 'block', width: '100%', boxSizing: 'border-box', minHeight: '50px', padding: '6px', margin: '4px 0 8px' }}
            />
          </label>
          {error && <p role="alert" style={{ color: '#b91c1c', margin: '0 0 8px' }}>{error}</p>}
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <button
              type="button"
              onClick={submit}
              disabled={saving || (mode === 'override' && !note.trim())}
              style={{ fontWeight: 'bold' }}
            >
              {saving ? 'Saving…' : 'Save marks'}
            </button>
            {answer.override_marks !== null && (
              <button
                type="button"
                disabled={saving}
                onClick={() => void save({ manual_marks: answer.manual_marks, override_marks: null, note: note.trim() || null })}
              >
                Remove override
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
