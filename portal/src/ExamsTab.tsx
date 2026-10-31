import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from './api/client'
import { errorText } from './apiErrors'
import NumberInput from './components/NumberInput'
import { useToast } from './components/ToastProvider'
import ExamTestsEditor from './ExamTestsEditor'

type Difficulty = 'easy' | 'medium' | 'hard'

type PoolQuestion = {
  id: string
  title: string
  difficulty: Difficulty
  tags: string[]
  marks: number
  grader: string
}

type Exam = {
  id: string
  title: string
  instructions_md: string
  duration_minutes: number
  join_window_minutes: number
  status: 'draft' | 'live' | 'closed'
  selection: { mode?: 'random' | 'fixed'; rules?: SelectionRule[] }
  policy: Record<string, unknown>
  marking: { auto_percent?: number }
  languages: string[] | null
  batch_ids: string[] | null
  question_ids: string[]
  access_code: string | null
  opened_at: string | null
  closed_at: string | null
}

type SelectionRule = {
  difficulty?: Difficulty | null
  tags?: string[]
  count: number
}

type Readiness = { ready: boolean; problems: string[]; warnings: string[] }

export type SubjectLanguage = { key: string; name: string }

export type BatchOption = { id: string; code: string; name: string }

type FixTarget = 'Questions' | 'Settings'

type Props = {
  subjectId: string
  subjectLanguages: SubjectLanguage[]
  canEdit: boolean
  onGoToTab: (tab: FixTarget) => void
  /** Open the existing question editor on this question. */
  onEditQuestion: (questionId: string) => void
  /** Batches linked to the subject (the only ones an exam may be limited to). */
  subjectBatches: BatchOption[]
  /** Every college batch, for linking one to the subject. */
  allBatches: BatchOption[]
  /** Link an existing batch to the subject (PUT /subjects/{id}/batches). */
  onLinkBatch: (batchId: string) => Promise<void>
}

/** Per-question language settings, only available from GET /questions/{id}. */
type QuestionInfo = {
  allowed_languages: string[] | null
  reference_language: string | null
}

const DIFFICULTIES: Difficulty[] = ['easy', 'medium', 'hard']

const cardStyle = {
  border: '1px solid #d1d5db',
  borderRadius: '12px',
  padding: '18px',
  marginTop: '16px',
  background: '#ffffff',
  color: '#111827',
}

const sectionStyle = {
  border: '1px solid #e5e7eb',
  borderRadius: '10px',
  padding: '14px 16px',
  marginTop: '14px',
}

const inputStyle = {
  display: 'block',
  width: '100%',
  maxWidth: '420px',
  boxSizing: 'border-box' as const,
  padding: '8px',
  margin: '6px 0 12px',
}

const difficultyColors: Record<Difficulty, string> = {
  easy: '#166534',
  medium: '#92400e',
  hard: '#991b1b',
}

type ExamForm = {
  title: string
  instructions_md: string
  duration_minutes: number
  join_window_minutes: number
  // Empty = no limit: every batch linked to the subject.
  batch_ids: string[]
  question_ids: string[]
  counts: Record<Difficulty, number>
  max_strikes: number
  lock_on_severe: boolean
  runs_per_minute: number
  reveal_hidden_results: boolean
  auto_percent: number
  languages: string[]
}

const emptyForm = (): ExamForm => ({
  title: '',
  instructions_md: '',
  duration_minutes: 60,
  join_window_minutes: 15,
  batch_ids: [],
  question_ids: [],
  counts: { easy: 0, medium: 0, hard: 0 },
  // Backend defaults from ExamPolicy / Marking.
  max_strikes: 3,
  lock_on_severe: true,
  runs_per_minute: 6,
  reveal_hidden_results: false,
  auto_percent: 100,
  languages: [],
})

function formFromExam(exam: Exam): ExamForm {
  const counts: Record<Difficulty, number> = { easy: 0, medium: 0, hard: 0 }
  for (const rule of exam.selection.rules ?? []) {
    if (rule.difficulty && !(rule.tags ?? []).length) {
      counts[rule.difficulty] += rule.count
    }
  }
  const policy = exam.policy
  return {
    title: exam.title,
    instructions_md: exam.instructions_md,
    duration_minutes: exam.duration_minutes,
    join_window_minutes: exam.join_window_minutes,
    batch_ids: exam.batch_ids ?? [],
    question_ids: exam.question_ids,
    counts,
    max_strikes: Number(policy.max_strikes ?? 3),
    lock_on_severe: Boolean(policy.lock_on_severe ?? true),
    runs_per_minute: Number(policy.runs_per_minute ?? 6),
    reveal_hidden_results: Boolean(policy.reveal_hidden_results ?? false),
    auto_percent: exam.marking.auto_percent ?? 100,
    languages: exam.languages ?? [],
  }
}

/** Which screen fixes a readiness problem. Based on the backend's check_ready messages. */
function fixFor(problem: string): { label: string; target: FixTarget | 'setup' } | null {
  if (problem.startsWith('No students are enrolled')) {
    return { label: 'Subject batches', target: 'Settings' }
  }
  if (problem.startsWith('The question pool') || problem.startsWith('The pool has')) {
    return { label: 'Edit pool & rules', target: 'setup' }
  }
  if (problem.startsWith("'")) {
    return { label: 'Question editor', target: 'Questions' }
  }
  return null
}

function ExamsTab({
  subjectId,
  subjectLanguages,
  canEdit,
  onGoToTab,
  onEditQuestion,
  subjectBatches,
  allBatches,
  onLinkBatch,
}: Props) {
  const navigate = useNavigate()
  const { notify } = useToast()

  const [exams, setExams] = useState<Exam[]>([])
  const [questions, setQuestions] = useState<PoolQuestion[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // null = form closed; 'new' = creating; otherwise the exam being edited.
  const [editing, setEditing] = useState<'new' | Exam | null>(null)
  const [form, setForm] = useState<ExamForm>(emptyForm)
  const [saving, setSaving] = useState(false)
  // When creating, the closed exam whose settings were copied (if any).
  const [cloneFrom, setCloneFrom] = useState<Exam | null>(null)
  const [questionInfo, setQuestionInfo] = useState<Record<string, QuestionInfo>>({})
  // "Link a batch to the subject" panel in the Batches section.
  const [linkOpen, setLinkOpen] = useState(false)
  const [linkBatchId, setLinkBatchId] = useState('')
  const [linking, setLinking] = useState(false)
  const [linkError, setLinkError] = useState('')

  const [readiness, setReadiness] = useState<Record<string, Readiness>>({})
  const [busyExam, setBusyExam] = useState<string | null>(null)
  const [projector, setProjector] = useState<Exam | null>(null)
  const [testsForId, setTestsForId] = useState<string | null>(null)
  const [stopTarget, setStopTarget] = useState<Exam | null>(null)
  const [stopping, setStopping] = useState(false)

  const testsFor = exams.find((e) => e.id === testsForId) ?? null

  useEffect(() => {
    let cancelled = false
    void Promise.all([
      api.GET('/api/v1/subjects/{subject_id}/exams', {
        params: { path: { subject_id: subjectId } },
      }),
      api.GET('/api/v1/subjects/{subject_id}/questions', {
        params: { path: { subject_id: subjectId } },
      }),
    ]).then(([examResult, questionResult]) => {
      if (cancelled) return
      if (examResult.error) {
        setError(errorText(examResult.error, 'Could not load exams.'))
      } else {
        setExams((examResult.data ?? []) as Exam[])
      }
      if (questionResult.error) {
        setError(errorText(questionResult.error, 'Could not load questions.'))
      } else {
        setQuestions((questionResult.data ?? []) as PoolQuestion[])
      }
      setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [subjectId])

  function replaceExam(updated: Exam) {
    setExams((list) => list.map((e) => (e.id === updated.id ? updated : e)))
  }

  function startCreate() {
    setCloneFrom(null)
    setForm(emptyForm())
    setEditing('new')
  }

  function startEdit(exam: Exam) {
    setCloneFrom(null)
    setForm(formFromExam(exam))
    setEditing(exam)
    void loadQuestionInfo(exam.question_ids)
  }

  /**
   * There is no clone endpoint, so a copy is a new exam created with
   * POST /subjects/{id}/exams from the old exam's settings. Sessions,
   * submissions, marks and the access code are never copied.
   */
  function startClone(exam: Exam) {
    const available = exam.question_ids.filter((id) => questions.some((q) => q.id === id))
    const dropped = exam.question_ids.length - available.length
    if (dropped > 0) {
      notify(`${dropped} question(s) of the original are archived or deleted and were left out.`, 'info')
    }
    // The backend only accepts batches still linked to the subject.
    const originalBatches = exam.batch_ids ?? []
    const linkedBatches = originalBatches.filter((id) => subjectBatches.some((b) => b.id === id))
    if (linkedBatches.length < originalBatches.length) {
      notify(
        `${originalBatches.length - linkedBatches.length} batch(es) of the original are no longer linked to this subject and were left out.`,
        'info',
      )
    }
    setCloneFrom(exam)
    setForm({
      ...formFromExam(exam),
      title: `${exam.title} (copy)`.slice(0, 200),
      batch_ids: linkedBatches,
      question_ids: available,
    })
    setEditing('new')
    void loadQuestionInfo(available)
  }

  async function loadQuestionInfo(ids: string[]) {
    const results = await Promise.all(
      ids.map((id) =>
        api.GET('/api/v1/questions/{question_id}', {
          params: { path: { question_id: id } },
        }),
      ),
    )
    setQuestionInfo((current) => {
      const next = { ...current }
      results.forEach((result, index) => {
        const data = result.data as QuestionInfo | undefined
        if (data) {
          next[ids[index]] = {
            allowed_languages: data.allowed_languages ?? null,
            reference_language: data.reference_language ?? null,
          }
        }
      })
      return next
    })
  }

  async function linkBatch() {
    const batch = allBatches.find((b) => b.id === linkBatchId)
    if (!batch) {
      setLinkError('Choose a batch to link.')
      return
    }
    if (subjectBatches.some((b) => b.id === batch.id)) {
      setLinkError(`${batch.name} is already linked to this subject.`)
      return
    }
    if (
      !window.confirm(
        `Link ${batch.name} (${batch.code}) to this subject?\n\n` +
          'Its students become part of the subject. Every exam of this subject without a ' +
          'batch limit, including live ones, will include them.\n\n' +
          'It will not be added to this exam unless you tick it.',
      )
    ) {
      return
    }
    setLinking(true)
    setLinkError('')
    try {
      await onLinkBatch(batch.id)
      notify(`${batch.name} linked to the subject. Tick it to include it in this exam.`, 'success')
      setLinkBatchId('')
      setLinkOpen(false)
    } catch (err) {
      setLinkError(err instanceof Error ? err.message : 'Could not link the batch.')
    } finally {
      setLinking(false)
    }
  }

  function editQuestion(questionId: string) {
    if (
      editing !== null &&
      !(editing !== 'new' && editing.status === 'closed') &&
      !window.confirm('Open the question editor? Unsaved changes on this exam screen will be lost.')
    ) {
      return
    }
    onEditQuestion(questionId)
  }

  function update<K extends keyof ExamForm>(key: K, value: ExamForm[K]) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  function toggle(list: string[], value: string) {
    return list.includes(value)
      ? list.filter((item) => item !== value)
      : [...list, value]
  }

  // --- selection-rule check against the selected pool ---------------------
  const selectedQuestions = questions.filter((q) =>
    form.question_ids.includes(q.id),
  )
  const poolCounts: Record<Difficulty, number> = { easy: 0, medium: 0, hard: 0 }
  for (const q of selectedQuestions) poolCounts[q.difficulty] += 1
  const totalPerStudent = DIFFICULTIES.reduce((n, d) => n + form.counts[d], 0)
  const ruleWarnings = DIFFICULTIES.filter(
    (d) => form.counts[d] > poolCounts[d],
  ).map(
    (d) =>
      `Not enough ${d} questions selected: the rule needs ${form.counts[d]}, the pool has ${poolCounts[d]}.`,
  )

  async function saveExam(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!form.title.trim()) {
      notify('Give the exam a title.', 'error')
      return
    }

    const rules = DIFFICULTIES.filter((d) => form.counts[d] > 0).map((d) => ({
      difficulty: d,
      tags: [] as string[],
      count: form.counts[d],
    }))
    const selection = {
      mode: (rules.length ? 'random' : 'fixed') as 'random' | 'fixed',
      rules,
    }
    const policyFields = {
      max_strikes: form.max_strikes,
      lock_on_severe: form.lock_on_severe,
      runs_per_minute: form.runs_per_minute,
      reveal_hidden_results: form.reveal_hidden_results,
    }
    const common = {
      title: form.title.trim(),
      instructions_md: form.instructions_md,
      duration_minutes: form.duration_minutes,
      join_window_minutes: form.join_window_minutes,
      selection,
      marking: { auto_percent: form.auto_percent },
      languages: form.languages.length ? form.languages : null,
      // null = no batch limit (the backend stores an empty list as null too).
      batch_ids: form.batch_ids.length ? form.batch_ids : null,
    }

    setSaving(true)
    try {
      if (editing === 'new') {
        const result = await api.POST('/api/v1/subjects/{subject_id}/exams', {
          params: { path: { subject_id: subjectId } },
          body: {
            ...common,
            // A copy keeps policy settings this form does not show (event severities, grace, ...).
            policy: cloneFrom ? { ...cloneFrom.policy, ...policyFields } : policyFields,
            question_ids: form.question_ids,
          } as never,
        })
        if (result.error) throw new Error(errorText(result.error, 'Could not create the exam.'))
        setExams((list) => [result.data as Exam, ...list])
        notify(
          cloneFrom
            ? `New draft created from "${cloneFrom.title}". The original stays closed. Check readiness next.`
            : 'Exam created as a draft. Check readiness next.',
          'success',
        )
        setCloneFrom(null)
      } else if (editing && editing.status === 'live') {
        // While live the backend only accepts these fields (PATCH /exams/{id}).
        const patch = await api.PATCH('/api/v1/exams/{exam_id}', {
          params: { path: { exam_id: editing.id } },
          body: {
            title: common.title,
            instructions_md: common.instructions_md,
            join_window_minutes: common.join_window_minutes,
            policy: { ...editing.policy, ...policyFields },
          } as never,
        })
        if (patch.error) throw new Error(errorText(patch.error, 'Could not save the exam.'))
        replaceExam(patch.data as Exam)
        notify('Live exam updated.', 'success')
      } else if (editing && editing.status === 'draft') {
        // PATCH replaces the whole policy, so keep settings this form does not show.
        const patch = await api.PATCH('/api/v1/exams/{exam_id}', {
          params: { path: { exam_id: editing.id } },
          body: { ...common, policy: { ...editing.policy, ...policyFields } } as never,
        })
        if (patch.error) throw new Error(errorText(patch.error, 'Could not save the exam.'))
        const pool = await api.PUT('/api/v1/exams/{exam_id}/pool', {
          params: { path: { exam_id: editing.id } },
          body: { question_ids: form.question_ids },
        })
        if (pool.error) throw new Error(errorText(pool.error, 'Could not save the question pool.'))
        replaceExam(pool.data as Exam)
        // The old readiness report no longer applies.
        setReadiness((current) => {
          const next = { ...current }
          delete next[editing.id]
          return next
        })
        notify('Exam saved. Check readiness again.', 'success')
      }
      setEditing(null)
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Could not save the exam.', 'error')
    } finally {
      setSaving(false)
    }
  }

  async function checkReadiness(exam: Exam) {
    setBusyExam(exam.id)
    const result = await api.GET('/api/v1/exams/{exam_id}/readiness', {
      params: { path: { exam_id: exam.id } },
    })
    setBusyExam(null)
    if (result.error) {
      notify(errorText(result.error, 'Could not check readiness.'), 'error')
      return
    }
    setReadiness((current) => ({ ...current, [exam.id]: result.data as Readiness }))
  }

  async function openExam(exam: Exam) {
    if (!window.confirm(`Open "${exam.title}" now? Students can join with the access code, and the languages, pool and selection rules can no longer change.`)) {
      return
    }
    setBusyExam(exam.id)
    const result = await api.POST('/api/v1/exams/{exam_id}/open', {
      params: { path: { exam_id: exam.id } },
    })
    setBusyExam(null)
    if (result.error) {
      const detail = (result.error as { detail?: { problems?: string[] } }).detail
      if (detail?.problems) {
        setReadiness((current) => ({
          ...current,
          [exam.id]: { ready: false, problems: detail.problems ?? [], warnings: [] },
        }))
      }
      notify(errorText(result.error, 'Could not open the exam.'), 'error')
      return
    }
    const opened = result.data as Exam
    replaceExam(opened)
    setProjector(opened)
  }

  async function rotateCode(exam: Exam) {
    if (!window.confirm('Replace the access code? The current code stops working immediately. Students who already started are not affected.')) {
      return
    }
    const result = await api.POST('/api/v1/exams/{exam_id}/rotate-code', {
      params: { path: { exam_id: exam.id } },
    })
    if (result.error) {
      notify(errorText(result.error, 'Could not rotate the code.'), 'error')
      return
    }
    const rotated = result.data as Exam
    replaceExam(rotated)
    setProjector((current) => (current?.id === rotated.id ? rotated : current))
    notify('New access code created.', 'success')
  }

  function goToResults(exam: Exam) {
    navigate(`/exams/${encodeURIComponent(exam.id)}/results`)
  }

  function goToMonitor(exam: Exam) {
    navigate(`/exams/${encodeURIComponent(exam.id)}/monitor`)
  }

  async function stopExam(exam: Exam) {
    setStopping(true)
    const result = await api.POST('/api/v1/exams/{exam_id}/close', {
      params: { path: { exam_id: exam.id } },
    })
    setStopping(false)
    if (result.error) {
      notify(errorText(result.error, 'Could not stop the exam.'), 'error')
      // e.g. someone else already stopped it: show the real status.
      const fresh = await api.GET('/api/v1/exams/{exam_id}', {
        params: { path: { exam_id: exam.id } },
      })
      if (fresh.data) replaceExam(fresh.data as Exam)
      setStopTarget(null)
      return
    }
    replaceExam(result.data as Exam)
    setStopTarget(null)
    notify(`"${exam.title}" has been stopped.`, 'success')
  }

  // What the backend lets us change (PATCH /exams/{id}, PUT /exams/{id}/pool):
  // draft = everything; live = title, instructions, join window, policy; closed = nothing.
  const editedExam = editing !== null && editing !== 'new' ? editing : null
  const editStatus = editedExam?.status ?? 'draft'
  const setupLocked = editStatus !== 'draft'
  const readOnly = editStatus === 'closed'
  const visibleQuestions = setupLocked
    ? questions.filter((q) => form.question_ids.includes(q.id))
    : questions

  // Subject batches, plus any selected batch that was unlinked from the subject
  // since the exam was saved (so it can be seen and unticked).
  const batchRows = [
    ...subjectBatches.map((b) => ({ ...b, linked: true })),
    ...form.batch_ids
      .filter((id) => !subjectBatches.some((b) => b.id === id))
      .map((id) => {
        const known = allBatches.find((b) => b.id === id)
        return { id, code: known?.code ?? '?', name: known?.name ?? 'Unknown batch', linked: false }
      }),
  ]
  const unlinkedBatches = allBatches.filter(
    (b) => !subjectBatches.some((s) => s.id === b.id),
  )

  if (projector) {
    return (
      <ProjectorView
        exam={projector}
        onClose={() => setProjector(null)}
        onRotate={() => void rotateCode(projector)}
        onMonitor={() => goToMonitor(projector)}
      />
    )
  }

  return (
    <section style={cardStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>Exams</h2>
        {canEdit && editing === null && testsFor === null && (
          <button type="button" onClick={startCreate}>
            + Create Exam
          </button>
        )}
      </div>

      {error && (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {error}
        </p>
      )}

      {editing !== null && (
        <form onSubmit={saveExam} style={{ marginTop: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            <h3 style={{ margin: 0 }}>
              {editing === 'new'
                ? cloneFrom
                  ? `New exam from "${cloneFrom.title}"`
                  : 'Create exam'
                : `${readOnly ? 'View' : 'Edit'} "${editing.title}"`}{' '}
              {editedExam && (
                <span style={{ fontSize: '0.75em', padding: '2px 8px', borderRadius: '999px', background: editStatus === 'live' ? '#dcfce7' : '#f3f4f6' }}>
                  {editStatus}
                </span>
              )}
            </h3>
            {editedExam && editStatus === 'closed' && canEdit && (
              <button type="button" onClick={() => startClone(editedExam)}>
                Create new exam from this
              </button>
            )}
          </div>

          {cloneFrom && (
            <p style={{ background: '#eff6ff', padding: '8px 12px', borderRadius: '6px' }}>
              Settings and questions are copied from “{cloneFrom.title}”. Not
              copied: student sessions, submissions, marks and the access code.
              The new exam starts as a draft; check readiness and open it as
              usual. The original exam stays closed.
            </p>
          )}
          {editStatus === 'live' && (
            <p style={{ background: '#fef3c7', color: '#92400e', padding: '8px 12px', borderRadius: '6px' }}>
              This exam is live. You can change the title, instructions, join
              window and policy. Duration, questions, selection rules, marking
              and languages are locked while students are taking it.
            </p>
          )}
          {editStatus === 'closed' && (
            <p style={{ background: '#f3f4f6', padding: '8px 12px', borderRadius: '6px' }}>
              This exam is stopped, and a stopped exam can’t be changed or
              reopened. To run it again, use <strong>Create new exam from
              this</strong>. You can still fix tests and re-grade below.
            </p>
          )}

          {/* 1. Basics */}
          <fieldset style={sectionStyle} disabled={readOnly}>
            <legend><strong>1. Basics</strong></legend>
            <label>
              Exam title
              <input
                style={inputStyle}
                value={form.title}
                maxLength={200}
                required
                onChange={(e) => update('title', e.target.value)}
              />
            </label>
            <label>
              Duration (minutes, 5–600)
              <NumberInput
                style={inputStyle}
                min={5}
                max={600}
                required
                disabled={setupLocked}
                value={form.duration_minutes}
                onChange={(value) => update('duration_minutes', value)}
              />
            </label>
            <label>
              Join window (minutes after opening, 0–600)
              <NumberInput
                style={inputStyle}
                min={0}
                max={600}
                required
                value={form.join_window_minutes}
                onChange={(value) => update('join_window_minutes', value)}
              />
            </label>
            <p style={{ color: '#4b5563', marginTop: 0 }}>
              Students can start during this many minutes after you open the
              exam. 0 means they can start any time while it is open.
            </p>
            <label>
              Instructions (optional, Markdown)
              <textarea
                style={{ ...inputStyle, maxWidth: '100%', minHeight: '70px' }}
                value={form.instructions_md}
                onChange={(e) => update('instructions_md', e.target.value)}
              />
            </label>
          </fieldset>

          {/* 2. Batches */}
          <fieldset style={sectionStyle} disabled={setupLocked}>
            <legend><strong>2. Batches</strong></legend>
            <p style={{ color: '#4b5563', marginTop: 0 }}>
              Choose which of the subject’s batches take this exam.{' '}
              <strong>None ticked = no limit:</strong> every batch linked to the
              subject, including batches linked later. Ticking all limits the
              exam to exactly those batches.
            </p>
            {editStatus === 'live' && (
              <p style={{ color: '#92400e', marginTop: 0 }}>
                Batches can’t change while the exam is live.
              </p>
            )}

            {batchRows.length === 0 && (
              <p style={{ background: '#f9fafb', padding: '8px 12px', borderRadius: '6px' }}>
                This subject has no batches yet, so the exam can’t have any
                students. Link a batch below or in{' '}
                <button type="button" onClick={() => onGoToTab('Settings')}>
                  Subject settings
                </button>
                .
              </p>
            )}

            {batchRows.length > 0 && (
              <>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '6px' }}>
                  <button
                    type="button"
                    onClick={() => update('batch_ids', subjectBatches.map((b) => b.id))}
                  >
                    Select all
                  </button>
                  <button type="button" onClick={() => update('batch_ids', [])}>
                    Clear selection
                  </button>
                  <span style={{ color: '#4b5563' }}>
                    {form.batch_ids.length === 0
                      ? `No limit: all ${subjectBatches.length} subject batch(es)`
                      : `${form.batch_ids.length} of ${subjectBatches.length} selected`}
                  </span>
                </div>
                {batchRows.map((b) => (
                  <label
                    key={b.id}
                    style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap', padding: '6px 0', borderBottom: '1px solid #f3f4f6' }}
                  >
                    <input
                      type="checkbox"
                      checked={form.batch_ids.includes(b.id)}
                      onChange={() => update('batch_ids', toggle(form.batch_ids, b.id))}
                    />
                    <span style={{ flex: 1, minWidth: '180px' }}>
                      {b.name} <code style={{ color: '#6b7280' }}>({b.code})</code>
                    </span>
                    {editedExam?.batch_ids?.includes(b.id) && (
                      <span style={{ fontSize: '0.8em', padding: '2px 8px', borderRadius: '999px', background: '#dbeafe' }}>
                        assigned
                      </span>
                    )}
                    {!b.linked && (
                      <span style={{ fontSize: '0.8em', color: '#991b1b' }}>
                        no longer linked to the subject; untick it before saving
                      </span>
                    )}
                  </label>
                ))}
              </>
            )}

            {canEdit && !setupLocked && (
              <div style={{ marginTop: '12px' }}>
                {!linkOpen ? (
                  <button
                    type="button"
                    onClick={() => {
                      setLinkOpen(true)
                      setLinkError('')
                    }}
                  >
                    + Add batch to subject
                  </button>
                ) : (
                  <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: '8px', padding: '12px' }}>
                    <p style={{ marginTop: 0 }}>
                      Link an existing college batch to this subject. Creating a
                      brand-new batch needs an administrator; professors
                      can’t create batches.
                    </p>
                    {unlinkedBatches.length === 0 ? (
                      <p style={{ color: '#4b5563' }}>
                        Every college batch is already linked to this subject.
                      </p>
                    ) : (
                      <label style={{ display: 'block' }}>
                        Batch
                        <select
                          style={inputStyle}
                          value={linkBatchId}
                          onChange={(e) => {
                            setLinkBatchId(e.target.value)
                            setLinkError('')
                          }}
                        >
                          <option value="">Choose a batch…</option>
                          {unlinkedBatches.map((b) => (
                            <option key={b.id} value={b.id}>
                              {b.name} ({b.code})
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    {linkError && (
                      <p role="alert" style={{ color: '#b91c1c', margin: '0 0 8px' }}>
                        {linkError}
                      </p>
                    )}
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                      {unlinkedBatches.length > 0 && (
                        <button type="button" disabled={linking || !linkBatchId} onClick={() => void linkBatch()}>
                          {linking ? 'Linking…' : 'Link to subject'}
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={linking}
                        onClick={() => {
                          setLinkOpen(false)
                          setLinkBatchId('')
                          setLinkError('')
                        }}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </fieldset>

          {/* 3. Question pool */}
          <fieldset style={sectionStyle}>
            <legend><strong>3. Questions</strong></legend>
            {questions.length === 0 && (
              <p>
                This subject has no questions yet.{' '}
                <button type="button" onClick={() => onGoToTab('Questions')}>
                  Go to the question editor
                </button>
              </p>
            )}
            {setupLocked && (
              <p style={{ color: '#4b5563', marginTop: 0 }}>
                The question pool can only change before the exam opens.
              </p>
            )}
            {visibleQuestions.map((q) => {
              const info = questionInfo[q.id]
              const inPool = form.question_ids.includes(q.id)
              return (
                <div
                  key={q.id}
                  style={{
                    display: 'flex',
                    gap: '10px',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    padding: '6px 0',
                    borderBottom: '1px solid #f3f4f6',
                  }}
                >
                  <label style={{ display: 'flex', gap: '10px', alignItems: 'center', flex: 1, minWidth: '240px' }}>
                    <input
                      type="checkbox"
                      checked={inPool}
                      disabled={setupLocked}
                      onChange={() => update('question_ids', toggle(form.question_ids, q.id))}
                    />
                    <span>
                      {q.title}
                      <span style={{ color: '#6b7280', fontSize: '0.9em' }}>
                        {' '}· {q.marks} marks
                        {q.tags.length > 0 && ` · ${q.tags.join(', ')}`}
                        {inPool && info && (
                          <>
                            {' '}· languages:{' '}
                            {info.allowed_languages?.join(', ') ?? 'any subject language'}
                            {info.reference_language && ` · reference: ${info.reference_language}`}
                          </>
                        )}
                      </span>
                    </span>
                  </label>
                  <span
                    style={{
                      color: difficultyColors[q.difficulty],
                      fontWeight: 'bold',
                      textTransform: 'capitalize',
                    }}
                  >
                    {q.difficulty}
                  </span>
                  {editedExam && inPool && canEdit && (
                    <button
                      type="button"
                      disabled={editStatus === 'live'}
                      title={editStatus === 'live' ? 'Stop the exam before editing its questions.' : undefined}
                      onClick={() => editQuestion(q.id)}
                    >
                      Edit question →
                    </button>
                  )}
                </div>
              )
            })}
            <p>
              {form.question_ids.length} selected ({poolCounts.easy} easy,{' '}
              {poolCounts.medium} medium, {poolCounts.hard} hard)
            </p>
            {editedExam && (
              <p style={{ color: '#92400e', background: '#fef3c7', padding: '6px 10px', borderRadius: '6px' }}>
                ⚠ Questions are shared. <strong>Edit question</strong> opens the
                question editor (title, statement, difficulty, marks, tags,
                reference solution, tests), and changes apply to every exam
                that uses the question.
                {editStatus === 'live' && ' Questions of a live exam can’t be edited here; stop the exam first.'}
              </p>
            )}
            {editedExam && canEdit && (
              <button
                type="button"
                disabled={editStatus === 'live'}
                title={editStatus === 'live' ? 'Stop the exam before editing its tests.' : undefined}
                onClick={() => {
                  setEditing(null)
                  setTestsForId(editedExam.id)
                }}
              >
                {editStatus === 'closed' ? 'Edit tests & re-grade →' : 'Edit tests →'}
              </button>
            )}
          </fieldset>

          {/* 3. Selection rules */}
          <fieldset style={sectionStyle} disabled={setupLocked}>
            <legend><strong>4. Selection rules</strong></legend>
            <p style={{ color: '#4b5563', marginTop: 0 }}>
              How many questions of each difficulty every student gets, picked
              at random from the pool. Leave all at 0 to give every student the
              whole pool in shuffled order.
            </p>
            <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
              {DIFFICULTIES.map((d) => (
                <label key={d} style={{ textTransform: 'capitalize' }}>
                  {d}
                  <NumberInput
                    style={{ ...inputStyle, width: '100px' }}
                    min={0}
                    max={50}
                    value={form.counts[d]}
                    onChange={(value) =>
                      update('counts', { ...form.counts, [d]: Math.max(0, value) })
                    }
                  />
                </label>
              ))}
            </div>
            <p>
              Questions per student:{' '}
              <strong>{totalPerStudent || `all ${form.question_ids.length}`}</strong>
            </p>
            {ruleWarnings.map((w) => (
              <p key={w} role="alert" style={{ color: '#92400e', background: '#fef3c7', padding: '6px 10px', borderRadius: '6px' }}>
                ⚠ {w}
              </p>
            ))}
          </fieldset>

          {/* 4. Policy */}
          <fieldset style={sectionStyle} disabled={readOnly}>
            <legend><strong>5. Policy</strong></legend>
            <label>
              Strikes allowed (1–50)
              <NumberInput
                style={inputStyle}
                min={1}
                max={50}
                value={form.max_strikes}
                onChange={(value) => update('max_strikes', value)}
              />
            </label>
            <p style={{ color: '#4b5563', marginTop: 0 }}>
              Minor events (leaving the window, exiting full screen, app
              restart, pasting from outside) each add a strike. The session
              locks when the student reaches this number.
            </p>
            <label style={{ display: 'block', marginBottom: '12px' }}>
              <input
                type="checkbox"
                checked={form.lock_on_severe}
                onChange={(e) => update('lock_on_severe', e.target.checked)}
              />{' '}
              Lock immediately on a severe event (extra display, VM, remote
              desktop, screen capture, blocked app, debugger, device change)
            </label>
            <label>
              Run limit (runs/submits per minute, per question, 1–60)
              <NumberInput
                style={inputStyle}
                min={1}
                max={60}
                value={form.runs_per_minute}
                onChange={(value) => update('runs_per_minute', value)}
              />
            </label>
            <label style={{ display: 'block' }}>
              <input
                type="checkbox"
                checked={form.reveal_hidden_results}
                onChange={(e) => update('reveal_hidden_results', e.target.checked)}
              />{' '}
              Show pass/fail of hidden tests when students submit
            </label>
          </fieldset>

          {/* 5. Marking */}
          <fieldset style={sectionStyle} disabled={setupLocked}>
            <legend><strong>6. Marking</strong></legend>
            <label>
              Automatic marks (% of each question's marks from tests, 0–100)
              <NumberInput
                style={inputStyle}
                min={0}
                max={100}
                value={form.auto_percent}
                onChange={(value) => update('auto_percent', value)}
              />
            </label>
            <p style={{ color: '#4b5563', marginTop: 0 }}>
              The remaining {100 - form.auto_percent}% is given by hand when
              you review answers.
            </p>
          </fieldset>

          {/* 6. Languages */}
          <fieldset style={sectionStyle} disabled={setupLocked}>
            <legend><strong>7. Languages</strong></legend>
            {subjectLanguages.length === 0 && (
              <p>
                This subject has no languages yet.{' '}
                <button type="button" onClick={() => onGoToTab('Settings')}>
                  Add languages in Settings
                </button>
              </p>
            )}
            {subjectLanguages.map((lang) => (
              <label key={lang.key} style={{ display: 'block', padding: '4px 0' }}>
                <input
                  type="checkbox"
                  checked={form.languages.includes(lang.key)}
                  onChange={() => update('languages', toggle(form.languages, lang.key))}
                />{' '}
                {lang.name} <code>({lang.key})</code>
              </label>
            ))}
            <p style={{ color: '#4b5563' }}>
              Leave all unticked to allow every language the subject teaches.
            </p>
            <p style={{ color: '#991b1b', fontWeight: 'bold' }}>
              The language choice cannot be changed once the exam is live.
            </p>
          </fieldset>

          <div style={{ display: 'flex', gap: '10px', marginTop: '16px' }}>
            {!readOnly && (
              <button type="submit" disabled={saving}>
                {saving
                  ? 'Saving…'
                  : editing === 'new'
                    ? cloneFrom
                      ? 'Create new draft'
                      : 'Create exam'
                    : 'Save changes'}
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setEditing(null)
                setCloneFrom(null)
              }}
              disabled={saving}
            >
              {readOnly ? '← Back to exams' : 'Cancel'}
            </button>
          </div>
        </form>
      )}

      {editing === null && testsFor !== null && (
        <ExamTestsEditor
          exam={testsFor}
          questions={questions}
          onClose={() => setTestsForId(null)}
        />
      )}

      {editing === null && testsFor === null && (
        <>
          {loading && <p>Loading exams…</p>}
          {!loading && exams.length === 0 && <p>No exams yet.</p>}
          {exams.map((exam) => {
            const report = readiness[exam.id]
            const busy = busyExam === exam.id
            return (
              <article key={exam.id} style={sectionStyle}>
                <h3 style={{ margin: '0 0 6px' }}>
                  {exam.title}{' '}
                  <span style={{ fontSize: '0.75em', padding: '2px 8px', borderRadius: '999px', background: exam.status === 'live' ? '#dcfce7' : '#f3f4f6' }}>
                    {exam.status}
                  </span>
                </h3>
                <p style={{ margin: '0 0 10px', color: '#4b5563' }}>
                  {exam.duration_minutes} min · join window{' '}
                  {exam.join_window_minutes || 'any time'}
                  {exam.join_window_minutes ? ' min' : ''} ·{' '}
                  {exam.question_ids.length} pool question(s) · languages:{' '}
                  {exam.languages?.join(', ') ?? 'all subject languages'} · batches:{' '}
                  {exam.batch_ids?.length
                    ? exam.batch_ids
                        .map((id) => allBatches.find((b) => b.id === id)?.code ?? '?')
                        .join(', ')
                    : 'all subject batches'}
                </p>

                {exam.status === 'draft' && (
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    {canEdit && (
                      <button type="button" onClick={() => startEdit(exam)}>
                        Edit exam
                      </button>
                    )}
                    {canEdit && (
                      <button type="button" onClick={() => setTestsForId(exam.id)}>
                        Edit tests
                      </button>
                    )}
                    <button type="button" disabled={busy} onClick={() => void checkReadiness(exam)}>
                      {busy ? 'Checking…' : 'Check readiness'}
                    </button>
                    <button
                      type="button"
                      disabled={busy || !report?.ready}
                      title={report ? undefined : 'Check readiness first'}
                      onClick={() => void openExam(exam)}
                      style={{ fontWeight: 'bold' }}
                    >
                      Open exam
                    </button>
                  </div>
                )}

                {exam.status === 'draft' && report && (
                  <div style={{ marginTop: '12px' }}>
                    {report.problems.length === 0 && report.warnings.length === 0 && (
                      <p style={{ color: '#166534' }}>✓ Ready to open. No problems found.</p>
                    )}
                    {report.problems.length > 0 && (
                      <p style={{ margin: '0 0 6px' }}>
                        <strong>Fix these before opening:</strong>
                      </p>
                    )}
                    <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                      {report.problems.map((p) => {
                        const fix = fixFor(p)
                        return (
                          <li key={`p-${p}`} style={{ color: '#991b1b', background: '#fee2e2', padding: '6px 10px', borderRadius: '6px', marginBottom: '6px' }}>
                            ✖ {p}{' '}
                            {fix && (
                              <button
                                type="button"
                                onClick={() =>
                                  fix.target === 'setup' ? startEdit(exam) : onGoToTab(fix.target)
                                }
                              >
                                {fix.label} →
                              </button>
                            )}
                          </li>
                        )
                      })}
                      {report.warnings.map((w) => (
                        <li key={`w-${w}`} style={{ color: '#92400e', background: '#fef3c7', padding: '6px 10px', borderRadius: '6px', marginBottom: '6px' }}>
                          ⚠ {w}
                        </li>
                      ))}
                    </ul>
                    {report.problems.length === 0 && report.warnings.length > 0 && (
                      <p style={{ color: '#4b5563' }}>Warnings do not stop you from opening.</p>
                    )}
                  </div>
                )}

                {exam.status === 'live' && (
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      disabled={!exam.access_code}
                      title={exam.access_code ? undefined : 'You need proctor access to see the code'}
                      onClick={() => setProjector(exam)}
                    >
                      Show access code
                    </button>
                    <button type="button" onClick={() => void rotateCode(exam)}>
                      Rotate code
                    </button>
                    {canEdit && (
                      <button type="button" onClick={() => startEdit(exam)}>
                        Edit exam
                      </button>
                    )}
                    {canEdit && (
                      <button
                        type="button"
                        disabled
                        title="Tests change grading immediately. Stop the exam first, then edit tests and re-grade."
                      >
                        Edit tests
                      </button>
                    )}
                    <button type="button" onClick={() => goToMonitor(exam)} style={{ fontWeight: 'bold' }}>
                      Go to Live Monitor →
                    </button>
                    <button
                      type="button"
                      onClick={() => setStopTarget(exam)}
                      style={{ color: '#ffffff', background: '#b91c1c', border: '1px solid #991b1b', borderRadius: '6px', padding: '4px 12px' }}
                    >
                      Stop exam
                    </button>
                  </div>
                )}

                {exam.status === 'live' && canEdit && (
                  <p style={{ margin: '8px 0 0', color: '#6b7280', fontSize: '0.9em' }}>
                    While live you can change the title, instructions, join
                    window and policy. Questions and tests can’t be edited
                    until the exam is stopped.
                  </p>
                )}

                {exam.status === 'closed' && (
                  <>
                    <p style={{ margin: '0 0 10px', color: '#4b5563' }}>
                      Stopped{exam.closed_at ? ` ${new Date(exam.closed_at).toLocaleString()}` : ''}.
                    </p>
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                      <button type="button" onClick={() => startEdit(exam)}>
                        View exam
                      </button>
                      {canEdit && (
                        <button type="button" onClick={() => setTestsForId(exam.id)}>
                          Edit tests &amp; re-grade
                        </button>
                      )}
                      {canEdit && (
                        <button type="button" onClick={() => startClone(exam)} style={{ fontWeight: 'bold' }}>
                          Create new exam from this
                        </button>
                      )}
                      <button type="button" onClick={() => goToMonitor(exam)}>
                        Live Monitor →
                      </button>
                      <button type="button" onClick={() => goToResults(exam)} style={{ fontWeight: 'bold' }}>
                        Results &amp; grading →
                      </button>
                    </div>
                  </>
                )}
              </article>
            )
          })}
        </>
      )}

      {stopTarget && (
        <StopExamDialog
          exam={stopTarget}
          stopping={stopping}
          onCancel={() => setStopTarget(null)}
          onConfirm={() => void stopExam(stopTarget)}
        />
      )}
    </section>
  )
}

type StopExamDialogProps = {
  exam: Exam
  stopping: boolean
  onCancel: () => void
  onConfirm: () => void
}

/** Confirmation before POST /exams/{id}/close, which cannot be undone. */
function StopExamDialog({ exam, stopping, onCancel, onConfirm }: StopExamDialogProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !stopping) onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, stopping])

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
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="stop-exam-title"
        style={{
          background: '#ffffff',
          color: '#111827',
          borderRadius: '12px',
          padding: '24px',
          maxWidth: '520px',
          width: '100%',
          boxShadow: '0 20px 40px rgba(0, 0, 0, 0.3)',
        }}
      >
        <h3 id="stop-exam-title" style={{ marginTop: 0, color: '#991b1b' }}>
          Stop “{exam.title}”?
        </h3>
        <p>This ends the exam for everyone, right now:</p>
        <ul>
          <li>Students still working are submitted automatically with their last saved code and cannot continue.</li>
          <li>Locked students are submitted too.</li>
          <li>No one else can join; the access code stops working.</li>
          <li>Submitted answers are graded automatically.</li>
        </ul>
        <p><strong>This cannot be undone. A stopped exam cannot be reopened.</strong></p>
        <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '18px' }}>
          <button type="button" onClick={onCancel} disabled={stopping} autoFocus>
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={stopping}
            style={{ color: '#ffffff', background: '#b91c1c', border: '1px solid #991b1b', borderRadius: '6px', padding: '6px 14px', fontWeight: 'bold' }}
          >
            {stopping ? 'Stopping…' : 'Confirm stop'}
          </button>
        </div>
      </div>
    </div>
  )
}

type ProjectorProps = {
  exam: Exam
  onClose: () => void
  onRotate: () => void
  onMonitor: () => void
}

function ProjectorView({ exam, onClose, onRotate, onMonitor }: ProjectorProps) {
  const [copiedCode, setCopiedCode] = useState<string | null>(null)

  // Enter full screen once when shown; leave it when closed.
  useEffect(() => {
    document.documentElement.requestFullscreen?.().catch(() => {
      // Full screen may be refused (e.g. no user gesture); the overlay still covers the page.
    })
    return () => {
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
    }
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Remember which code was copied, so a rotated code shows "Copy" again.
  const copied = copiedCode !== null && copiedCode === exam.access_code

  async function copy() {
    if (!exam.access_code) return
    try {
      await navigator.clipboard.writeText(exam.access_code)
      setCopiedCode(exam.access_code)
    } catch {
      setCopiedCode(null)
    }
  }

  const buttonStyle = {
    fontSize: '20px',
    padding: '12px 22px',
    borderRadius: '10px',
    border: '2px solid #ffffff',
    background: '#000000',
    color: '#ffffff',
    cursor: 'pointer',
  }

  return (
    <div
      role="dialog"
      aria-label="Exam access code"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: '#000000',
        color: '#ffffff',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '24px',
        fontFamily: 'Arial, sans-serif',
        textAlign: 'center',
        padding: '24px',
      }}
    >
      <div style={{ fontSize: 'clamp(24px, 4vw, 48px)', fontWeight: 'bold', color: '#4ade80' }}>
        EXAM IS NOW OPEN
      </div>
      <div style={{ fontSize: 'clamp(18px, 2.5vw, 32px)' }}>{exam.title}</div>
      <div style={{ fontSize: 'clamp(16px, 2vw, 28px)', letterSpacing: '0.2em', color: '#d1d5db' }}>
        STUDENT ACCESS CODE
      </div>
      <div
        style={{
          fontFamily: 'Consolas, "Courier New", monospace',
          fontSize: 'clamp(64px, 14vw, 240px)',
          fontWeight: 'bold',
          letterSpacing: '0.08em',
          lineHeight: 1,
          userSelect: 'all',
          wordBreak: 'break-all',
        }}
      >
        {exam.access_code ?? '—'}
      </div>
      <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', justifyContent: 'center' }}>
        <button type="button" style={buttonStyle} onClick={() => void copy()}>
          {copied ? '✓ Copied' : 'Copy access code'}
        </button>
        <button type="button" style={buttonStyle} onClick={onRotate}>
          Rotate code
        </button>
        <button type="button" style={{ ...buttonStyle, background: '#ffffff', color: '#000000' }} onClick={onMonitor}>
          Go to Live Monitor →
        </button>
        <button type="button" style={buttonStyle} onClick={onClose}>
          Close (Esc)
        </button>
      </div>
    </div>
  )
}

export default ExamsTab
