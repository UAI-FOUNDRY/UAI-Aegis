import { useEffect, useState } from 'react'
import { api } from './api/client'
import { errorText } from './apiErrors'
import NumberInput from './components/NumberInput'
import { useToast } from './components/ToastProvider'

export type EditorQuestion = {
  id: string
  title: string
  difficulty: string
  grader: string
}

type TestRow = {
  id: string
  ordinal: number
  is_sample: boolean
  group: string
  weight: number
  has_expected: boolean
  input_preview: string
  expected_preview: string | null
}

type Draft = {
  // null = adding a new test
  id: string | null
  input: string
  expected_output: string
  is_sample: boolean
  weight: number
}

type Props = {
  exam: { id: string; title: string; status: 'draft' | 'live' | 'closed'; question_ids: string[] }
  questions: EditorQuestion[]
  onClose: () => void
}

const sectionStyle = {
  border: '1px solid #e5e7eb',
  borderRadius: '10px',
  padding: '14px 16px',
  marginTop: '14px',
}

const textareaStyle = {
  display: 'block',
  width: '100%',
  boxSizing: 'border-box' as const,
  minHeight: '90px',
  padding: '8px',
  margin: '6px 0 12px',
  fontFamily: 'Consolas, "Courier New", monospace',
}

const previewStyle = {
  fontFamily: 'Consolas, "Courier New", monospace',
  whiteSpace: 'pre-wrap' as const,
  wordBreak: 'break-all' as const,
  maxWidth: '260px',
  maxHeight: '4.5em',
  overflow: 'hidden',
}

const emptyDraft = (): Draft => ({
  id: null,
  input: '',
  expected_output: '',
  is_sample: false,
  weight: 1,
})

/**
 * Add, edit and delete the tests of an exam's pool questions.
 * Only for draft or closed exams: the backend applies test changes immediately,
 * so editing during a live exam would change grading mid-exam.
 */
export default function ExamTestsEditor({ exam, questions, onClose }: Props) {
  const { notify } = useToast()

  const poolQuestions = questions.filter((q) => exam.question_ids.includes(q.id))
  const [questionId, setQuestionId] = useState(poolQuestions[0]?.id ?? '')
  const [tests, setTests] = useState<TestRow[]>([])
  const [loadingTests, setLoadingTests] = useState(poolQuestions.length > 0)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  // Set when a closed exam's tests change, so we can suggest re-grading.
  const [changed, setChanged] = useState(false)

  const selected = poolQuestions.find((q) => q.id === questionId)
  const isML = selected?.grader === 'ml'

  async function fetchTests(id: string) {
    const result = await api.GET('/api/v1/questions/{question_id}/tests', {
      params: { path: { question_id: id } },
    })
    if (result.error) throw new Error(errorText(result.error, 'Could not load tests.'))
    return (result.data ?? []) as TestRow[]
  }

  useEffect(() => {
    if (!questionId) return
    let cancelled = false
    void fetchTests(questionId)
      .then((rows) => {
        if (!cancelled) setTests(rows)
      })
      .catch((err: unknown) => {
        if (!cancelled) notify(err instanceof Error ? err.message : 'Could not load tests.', 'error')
      })
      .finally(() => {
        if (!cancelled) setLoadingTests(false)
      })
    return () => {
      cancelled = true
    }
    // notify is stable from the toast provider.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [questionId])

  function selectQuestion(id: string) {
    if (id === questionId) return
    setDraft(null)
    setTests([])
    setLoadingTests(true)
    setQuestionId(id)
  }

  async function reload() {
    setTests(await fetchTests(questionId))
    setChanged(true)
  }

  async function startEdit(test: TestRow) {
    setBusy(true)
    const result = await api.GET('/api/v1/questions/{question_id}/tests/{test_id}', {
      params: { path: { question_id: questionId, test_id: test.id } },
    })
    setBusy(false)
    if (result.error) {
      notify(errorText(result.error, 'Could not load the test.'), 'error')
      return
    }
    const full = result.data as TestRow & { input: string; expected_output: string | null }
    setDraft({
      id: full.id,
      input: full.input,
      expected_output: full.expected_output ?? '',
      is_sample: full.is_sample,
      weight: full.weight,
    })
  }

  async function saveDraft(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draft) return
    // An empty box means "no expected output yet" (generate it from the reference solution).
    const expected = draft.expected_output === '' ? null : draft.expected_output
    setBusy(true)
    try {
      const result = draft.id
        ? await api.PATCH('/api/v1/questions/{question_id}/tests/{test_id}', {
            params: { path: { question_id: questionId, test_id: draft.id } },
            body: {
              input: draft.input,
              expected_output: expected,
              is_sample: draft.is_sample,
              weight: draft.weight,
            },
          })
        : await api.POST('/api/v1/questions/{question_id}/tests', {
            params: { path: { question_id: questionId } },
            body: {
              tests: [
                {
                  input: draft.input,
                  expected_output: expected,
                  is_sample: draft.is_sample,
                  group: 'main',
                  weight: draft.weight,
                },
              ],
            },
          })
      if (result.error) throw new Error(errorText(result.error, 'Could not save the test.'))
      notify(draft.id ? 'Test updated.' : 'Test added.', 'success')
      setDraft(null)
      await reload()
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Could not save the test.', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function deleteTest(test: TestRow) {
    if (!window.confirm(`Delete test ${test.ordinal}? This cannot be undone.`)) return
    setBusy(true)
    try {
      const result = await api.DELETE('/api/v1/questions/{question_id}/tests/{test_id}', {
        params: { path: { question_id: questionId, test_id: test.id } },
      })
      if (result.error) throw new Error(errorText(result.error, 'Could not delete the test.'))
      if (draft?.id === test.id) setDraft(null)
      notify('Test deleted.', 'success')
      await reload()
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Could not delete the test.', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function regradeAll() {
    if (!window.confirm('Re-grade every submitted session of this exam with the current tests? Manual marks are kept.')) {
      return
    }
    setBusy(true)
    const result = await api.POST('/api/v1/exams/{exam_id}/regrade', {
      params: { path: { exam_id: exam.id }, query: { everyone: true } },
    })
    setBusy(false)
    if (result.error) {
      notify(errorText(result.error, 'Could not start re-grading.'), 'error')
      return
    }
    const queued = (result.data as { queued?: number } | undefined)?.queued ?? 0
    notify(`Re-grading started for ${queued} session(s).`, 'success')
    setChanged(false)
  }

  if (exam.status === 'live') {
    // Safety net; the Exams tab does not offer this for live exams.
    return (
      <section style={sectionStyle}>
        <p>Stop the exam before editing its tests.</p>
        <button type="button" onClick={onClose}>Back to exams</button>
      </section>
    )
  }

  return (
    <section style={{ marginTop: '12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
        <h3 style={{ margin: 0 }}>Edit tests — {exam.title}</h3>
        <button type="button" onClick={onClose}>← Back to exams</button>
      </div>

      <p style={{ color: '#92400e', background: '#fef3c7', padding: '8px 12px', borderRadius: '6px' }}>
        ⚠ Tests belong to the question, so changes here also apply in the
        question editor and in any other exam that uses the question.
        {exam.status === 'closed' &&
          ' Submitted answers keep their old marks until you re-grade.'}
      </p>

      {exam.status === 'closed' && (
        <div style={{ ...sectionStyle, borderColor: changed ? '#f59e0b' : '#e5e7eb' }}>
          <strong>Re-grade</strong>
          <p style={{ margin: '6px 0 10px' }}>
            {changed
              ? 'You changed tests. Re-grade so submitted answers are marked with the new tests.'
              : 'Re-run grading of all submitted answers with the current tests.'}
          </p>
          <button type="button" disabled={busy} onClick={() => void regradeAll()}>
            Re-grade all submissions
          </button>
        </div>
      )}

      {poolQuestions.length === 0 && <p>This exam has no questions in its pool.</p>}

      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '14px' }}>
        {poolQuestions.map((q) => (
          <button
            type="button"
            key={q.id}
            aria-pressed={q.id === questionId}
            onClick={() => selectQuestion(q.id)}
            style={{
              padding: '8px 14px',
              borderRadius: '8px',
              border: '1px solid #9ca3af',
              background: q.id === questionId ? '#dbeafe' : '#ffffff',
              fontWeight: q.id === questionId ? 'bold' : 'normal',
            }}
          >
            {q.title} <span style={{ color: '#6b7280', textTransform: 'capitalize' }}>· {q.difficulty}</span>
          </button>
        ))}
      </div>

      {selected && isML && (
        <p style={sectionStyle}>
          This is an ML question; its tests are uploaded datasets. Change them
          in the question editor (Questions tab).
        </p>
      )}

      {selected && !isML && (
        <div style={sectionStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <strong>Tests for “{selected.title}”</strong>
            <button type="button" disabled={busy || draft !== null} onClick={() => setDraft(emptyDraft())}>
              + Add test
            </button>
          </div>

          {loadingTests && <p>Loading tests…</p>}
          {!loadingTests && tests.length === 0 && <p>No tests yet.</p>}

          {tests.length > 0 && (
            <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '10px' }}>
              <thead>
                <tr style={{ textAlign: 'left', borderBottom: '1px solid #d1d5db' }}>
                  <th>#</th>
                  <th>Type</th>
                  <th>Weight</th>
                  <th>Input</th>
                  <th>Expected output</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tests.map((t) => (
                  <tr key={t.id} style={{ borderBottom: '1px solid #f3f4f6', verticalAlign: 'top' }}>
                    <td>{t.ordinal}</td>
                    <td>{t.is_sample ? 'Sample' : 'Hidden'}</td>
                    <td>{t.weight}</td>
                    <td><div style={previewStyle}>{t.input_preview}</div></td>
                    <td>
                      {t.has_expected ? (
                        <div style={previewStyle}>{t.expected_preview}</div>
                      ) : (
                        <span style={{ color: '#991b1b' }}>missing</span>
                      )}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button type="button" disabled={busy || draft !== null} onClick={() => void startEdit(t)}>
                        Edit
                      </button>{' '}
                      <button type="button" disabled={busy} onClick={() => void deleteTest(t)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {draft && (
            <form onSubmit={saveDraft} style={sectionStyle}>
              <strong>{draft.id ? 'Edit test' : 'New test'}</strong>
              <label style={{ display: 'block', marginTop: '10px' }}>
                Input
                <textarea
                  style={textareaStyle}
                  value={draft.input}
                  onChange={(e) => setDraft({ ...draft, input: e.target.value })}
                />
              </label>
              <label style={{ display: 'block' }}>
                Expected output
                <textarea
                  style={textareaStyle}
                  value={draft.expected_output}
                  placeholder="Leave empty to generate it later from the reference solution"
                  onChange={(e) => setDraft({ ...draft, expected_output: e.target.value })}
                />
              </label>
              <label style={{ display: 'block', marginBottom: '10px' }}>
                <input
                  type="checkbox"
                  checked={draft.is_sample}
                  onChange={(e) => setDraft({ ...draft, is_sample: e.target.checked })}
                />{' '}
                Sample test (students can see it)
              </label>
              <label style={{ display: 'block' }}>
                Weight (0–1000)
                <NumberInput
                  min={0}
                  max={1000}
                  value={draft.weight}
                  onChange={(weight) => setDraft((d) => (d ? { ...d, weight } : d))}
                  style={{ display: 'block', width: '120px', padding: '8px', margin: '6px 0 12px' }}
                />
              </label>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button type="submit" disabled={busy}>
                  {busy ? 'Saving…' : 'Save test'}
                </button>
                <button type="button" disabled={busy} onClick={() => setDraft(null)}>
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </section>
  )
}
