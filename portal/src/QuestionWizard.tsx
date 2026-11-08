import { useCallback, useEffect, useState } from 'react'
import { API_URL } from './api/client'
import Stepper from './components/Stepper'
import NumberInput from './components/NumberInput'
import SafeMarkdown from './components/SafeMarkdown'
import { useToast } from './components/ToastProvider'
import QuestionFilesPanel from './QuestionFilesPanel'

type Question = {
  id: string
  title: string
  statement_md: string
  difficulty: string
  tags: string[]
  grader: string
  grader_config: Record<string, unknown>
  marks: number
  reference_language?: string | null
  reference_code?: string | null
  version?: number
}

type TestCase = {
  id: string
  ordinal: number
  is_sample: boolean
  group: string
  weight: number
  has_expected: boolean
  input_preview: string
  expected_preview: string | null
  input?: string
  expected_output?: string | null
}

type Grader = { key: string; description: string }

type CatalogLanguage = { key: string; name: string; is_enabled: boolean }

/**
 * Languages each grader accepts (backend/app/grading/*.py validate_language):
 * stdio = anything but SQL; sql = sql; function = Python or JavaScript; ml = python3-ml.
 */
function languagesForGrader(grader: string, all: CatalogLanguage[]): CatalogLanguage[] {
  const enabled = all.filter((l) => l.is_enabled)
  if (grader === 'sql') return enabled.filter((l) => l.key === 'sql')
  if (grader === 'ml') return enabled.filter((l) => l.key === 'python3-ml')
  if (grader === 'html') return enabled.filter((l) => l.key === 'html')
  if (grader === 'plot') return enabled.filter((l) => l.key === 'python3-ml')
  if (grader === 'function') {
    return enabled.filter((l) => l.key.startsWith('python') || l.key === 'javascript' || l.key === 'nodejs')
  }
  return enabled.filter((l) => l.key !== 'sql' && l.key !== 'html')
}

/** Per-test result of validating an HTML reference page: verdict, message and the ✓/✗ check list. */
function HtmlValidation({ report }: { report: GradeReport }) {
  return (
    <div data-testid="html-validation">
      <p>
        <strong>{Math.round(report.fraction * 100)}%</strong> of checks passed.
      </p>
      <ul style={{ paddingLeft: '18px' }}>
        {report.tests.map((test) => (
          <li key={test.test_id} style={{ marginBottom: '8px' }}>
            Test {test.ordinal}: <strong>{test.verdict === 'AC' ? 'passed' : test.verdict}</strong>
            {test.message ? ` — ${test.message}` : ''}
            {test.stdout && <pre style={{ margin: '4px 0', whiteSpace: 'pre-wrap' }}>{test.stdout}</pre>}
            {test.stderr && <pre style={{ margin: '4px 0', whiteSpace: 'pre-wrap', color: '#b91c1c' }}>{test.stderr}</pre>}
          </li>
        ))}
      </ul>
    </div>
  )
}

// Shown next to the HTML test box; matches backend/app/grading/html.py.
const HTML_CHECK_HELP = `doctype                         page starts with <!DOCTYPE html>
exists  SELECTOR                at least one element matches
missing SELECTOR                no element matches
count   SELECTOR = N            also >=  <=  >  <
text    SELECTOR contains WORDS some match's text contains WORDS
text    SELECTOR = WORDS        some match's text is exactly WORDS
attr    SELECTOR NAME           every match has the attribute
attr    SELECTOR NAME = VALUE   ... with exactly this value
Selectors: tag  #id  .class  [type=email]  [href^=https]  form input  ul > li  h1, h2`

// Shown next to the plot test box; matches backend/app/grading/plot.py.
const PLOT_CHECK_HELP = `chart KIND                  bar, hist, line, scatter, pie, box, violin, heatmap, area, kde
title contains WORDS        (or title = WORDS; also the figure's suptitle)
xlabel contains WORDS       (or xlabel = WORDS)
ylabel contains WORDS       (or ylabel = WORDS)
legend                      a legend is shown
xticks include A, B, C      x-axis tick labels include these
count WHAT = N              WHAT: figures, axes, bars, lines, points, wedges (also >= <= > <)`

const PLOT_CHECK_EXAMPLE = `chart bar
title contains total sales
xlabel = Region
ylabel = Sales
count bars >= 4`

const HTML_CHECK_EXAMPLE = `doctype
exists form#signup
exists form input[type=email][required]
count ul.menu > li = 3
text h1 contains welcome
attr img alt`

type TestDraft = {
  input: string
  expected_output: string
  is_sample: boolean
  group: string
  weight: number
}

type GradeReport = {
  fraction: number
  passed: number
  total: number
  compile_output?: string
  tests: {
    test_id: string
    ordinal: number
    verdict: string
    message?: string
    time_ms?: number | null
    stdout?: string
    stderr?: string
  }[]
}

type Threshold = { at: number; credit: number }

const steps = ['Write question', 'Add tests', 'Generate outputs', 'Try solution']

const compareModes = [
  'lines',
  'exact',
  'whitespace',
  'case_insensitive',
  'float',
  'unordered_lines',
]

const emptyTest = (): TestDraft => ({
  input: '',
  expected_output: '',
  is_sample: true,
  group: 'main',
  weight: 1,
})

const defaultQuestionForm = () => ({
  title: '',
  statement_md: '',
  difficulty: 'easy',
  tags: '',
  marks: 10,
  grader: 'stdio',
  compare: 'lines',
  reference_language: 'python3',
  reference_code: '',

  html_partial_credit: true,
  plot_data_csv: '',
  plot_data_file: 'data.csv',

  sql_mode: 'query',
  sql_schema: '',
  sql_check_query: '',
  sql_order_matters: false,
  sql_check_column_names: false,

  function_name: 'solve',
  function_params: 'nums, target',
  function_compare: 'exact',
  function_tolerance: '0.000001',

  ml_task: 'classification',
  ml_target: '',
  ml_metric: 'accuracy',
  ml_thresholds:
    '[{"at":0.85,"credit":1},{"at":0.75,"credit":0.5}]',
})

function getSessionToken(): string | null {
  const savedSession = localStorage.getItem('aegis_session')

  if (!savedSession) return null

  try {
    const session = JSON.parse(savedSession) as {
      token?: string
    }

    return session.token ?? null
  } catch {
    return null
  }
}

async function apiRequest<T>(
  path: string,
  method = 'GET',
  body?: unknown,
): Promise<T> {
  const token = getSessionToken()

  const response = await fetch(`${API_URL}/api/v1${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })

  if (!response.ok) {
    let detail = `Request failed (${response.status})`

    try {
      const data = await response.json()
      if (typeof data.detail === 'string') detail = data.detail
      else if (data.detail) detail = JSON.stringify(data.detail)
      else if (data.message) detail = data.message
    } catch {
      // Keep the HTTP status message if the response is not JSON.
    }

    throw new Error(detail)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

async function uploadDataset(
  questionId: string,
  train: File,
  test: File,
  isSample: boolean,
  weight: number,
  group: string,
) {
  const token = getSessionToken()
  const form = new FormData()

  form.append('train', train)
  form.append('test', test)
  form.append('is_sample', String(isSample))
  form.append('weight', String(weight))
  form.append('group', group)

  const response = await fetch(
    `${API_URL}/api/v1/questions/${questionId}/datasets`,
    {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    },
  )

  if (!response.ok) {
    let detail = `Dataset upload failed (${response.status})`

    try {
      const data = await response.json()
      if (typeof data.detail === 'string') detail = data.detail
      else if (data.detail) detail = JSON.stringify(data.detail)
    } catch {
      // Use the HTTP error message.
    }

    throw new Error(detail)
  }

  if (response.status === 204) return undefined
  return response.json()
}

export default function QuestionWizard({
  subjectId,
  openQuestionId,
}: {
  subjectId: string
  /** Open this question right away, e.g. when coming from an exam. */
  openQuestionId?: string
}) {
  const { notify } = useToast()

  const [questions, setQuestions] = useState<Question[]>([])
  const [graders, setGraders] = useState<Grader[]>([])
  const [questionId, setQuestionId] = useState('')
  const [currentStep, setCurrentStep] = useState(1)
  const [, setMaxUnlocked] = useState(1)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const [tests, setTests] = useState<TestCase[]>([])
  const [testDraft, setTestDraft] = useState<TestDraft>(emptyTest())
  const [editingTestId, setEditingTestId] = useState('')
  const [generation, setGeneration] = useState<Record<string, unknown> | null>(
    null,
  )
  const [grade, setGrade] = useState<GradeReport | null>(null)
  const [tryCode, setTryCode] = useState('')
  const [tryLanguage, setTryLanguage] = useState('python3')
  // Every enabled language (GET /catalog/languages); admins add more in Admin → Languages.
  const [languages, setLanguages] = useState<CatalogLanguage[]>([])

  const [questionForm, setQuestionForm] = useState(defaultQuestionForm)

  const [trainFile, setTrainFile] = useState<File | null>(null)
  const [testFile, setTestFile] = useState<File | null>(null)
  const [datasetIsSample, setDatasetIsSample] = useState(true)
  const [datasetWeight, setDatasetWeight] = useState(1)
  const [datasetGroup, setDatasetGroup] = useState('main')
  const [datasetResult, setDatasetResult] = useState<unknown>(null)
  const [mlValidation, setMlValidation] = useState<unknown>(null)

  const isML = questionForm.grader === 'ml'
  const isSQL = questionForm.grader === 'sql'
  const isFunction = questionForm.grader === 'function'
  const isStandard = questionForm.grader === 'stdio'
  const isHTML = questionForm.grader === 'html'
  const isPlot = questionForm.grader === 'plot'
  // Tests are lists of checks (no expected output): validate instead of generating outputs.
  const isChecklist = isHTML || isPlot

  /** Languages this question's grader accepts, plus the current value so it never disappears. */
  const languageOptions = (current: string): CatalogLanguage[] => {
    const options = languagesForGrader(questionForm.grader, languages)
    if (current && !options.some((l) => l.key === current)) {
      const known = languages.find((l) => l.key === current)
      return [{ key: current, name: known ? known.name : current, is_enabled: true }, ...options]
    }
    return options
  }

  const setField = (name: string, value: string | number | boolean) => {
    setQuestionForm((previous) => ({ ...previous, [name]: value }))
  }

  useEffect(() => {
    if (error) notify(error, 'error')
  }, [error, notify])

  useEffect(() => {
    if (!message) return

    const informational =
      message.startsWith('Warning:') ||
      message.startsWith('Trial completed.')

    notify(message, informational ? 'info' : 'success')
  }, [message, notify])

  const loadQuestions = useCallback(async () => {
    const data = await apiRequest<Question[]>(
      `/subjects/${subjectId}/questions`,
    )
    setQuestions(data)
  }, [subjectId])

  const loadTests = useCallback(async (id: string) => {
    const data = await apiRequest<TestCase[]>(`/questions/${id}/tests`)
    setTests(data)
  }, [])

  useEffect(() => {
    let active = true

    Promise.all([
      apiRequest<Question[]>(`/subjects/${subjectId}/questions`),
      apiRequest<Grader[]>('/graders'),
      apiRequest<CatalogLanguage[]>('/catalog/languages'),
    ])
      .then(([questionData, graderData, languageData]) => {
        if (!active) return
        setQuestions(questionData)
        setGraders(graderData)
        setLanguages(languageData)
      })
      .catch((err: unknown) => {
        if (active) {
          setError(
            err instanceof Error ? err.message : 'Could not load questions.',
          )
        }
      })

    return () => {
      active = false
    }
  }, [subjectId])

  const resetEditor = () => {
    setQuestionId('')
    setCurrentStep(1)
    setMaxUnlocked(1)
    setQuestionForm(defaultQuestionForm())
    setTests([])
    setTestDraft(emptyTest())
    setEditingTestId('')
    setGeneration(null)
    setGrade(null)
    setTryCode('')
    setTryLanguage('python3')
    setTrainFile(null)
    setTestFile(null)
    setDatasetResult(null)
    setMlValidation(null)
    setError('')
    setMessage('')
  }

  const openQuestion = async (id: string) => {
    setLoading(true)
    setError('')
    setMessage('')

    try {
      const question = await apiRequest<Question>(`/questions/${id}`)
      const config = question.grader_config ?? {}

      setQuestionId(question.id)
      setQuestionForm({
        ...defaultQuestionForm(),
        title: question.title ?? '',
        statement_md: question.statement_md ?? '',
        difficulty: question.difficulty ?? 'easy',
        tags: Array.isArray(question.tags) ? question.tags.join(', ') : '',
        marks: question.marks ?? 10,
        grader: question.grader ?? 'stdio',
        compare: String(config.compare ?? 'lines'),
        reference_language: question.reference_language ?? 'python3',
        reference_code: question.reference_code ?? '',

        html_partial_credit: config.partial_credit !== false,
        plot_data_csv: typeof config.data_csv === 'string' ? config.data_csv : '',
        plot_data_file: typeof config.data_file === 'string' ? config.data_file : 'data.csv',

        sql_mode: String(config.mode ?? 'query'),
        sql_schema: String(config.schema_sql ?? ''),
        sql_check_query: String(config.check_query ?? ''),
        sql_order_matters: Boolean(config.order_matters),
        sql_check_column_names: Boolean(config.check_column_names),

        function_name: String(config.function_name ?? 'solve'),
        function_params: Array.isArray(config.params)
          ? config.params.join(', ')
          : 'nums, target',
        function_compare: String(config.compare ?? 'exact'),
        function_tolerance: String(config.tolerance ?? 0.000001),

        ml_task: String(config.task ?? 'classification'),
        ml_target: String(config.target ?? ''),
        ml_metric: String(config.metric ?? 'accuracy'),
        ml_thresholds: JSON.stringify(
          config.thresholds ?? [
            { at: 0.85, credit: 1 },
            { at: 0.75, credit: 0.5 },
          ],
          null,
          2,
        ),
      })

      await loadTests(id)
      // Try solutions in the question's own language by default (e.g. html, sql).
      setTryLanguage(question.reference_language ?? 'python3')
      setCurrentStep(1)
      setMaxUnlocked(4)
      setGeneration(null)
      setGrade(null)
      setDatasetResult(null)
      setMlValidation(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not open question.')
    } finally {
      setLoading(false)
    }
  }

  // Coming from an exam's "Edit question": open that question once.
  // openQuestion shows "Working…" first, then fetches; that one extra render is intended.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (openQuestionId) void openQuestion(openQuestionId)
    // openQuestion only uses state setters and stable helpers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openQuestionId])

  const buildGraderConfig = (): Record<string, unknown> => {
    if (isStandard) {
      return { compare: questionForm.compare }
    }

    if (isHTML) {
      return { partial_credit: questionForm.html_partial_credit }
    }

    if (isPlot) {
      return {
        data_csv: questionForm.plot_data_csv.trim() ? questionForm.plot_data_csv : null,
        data_file: questionForm.plot_data_file.trim() || 'data.csv',
        partial_credit: questionForm.html_partial_credit,
      }
    }

    if (isSQL) {
      return {
        mode: questionForm.sql_mode,
        schema_sql: questionForm.sql_schema,
        check_query:
          questionForm.sql_mode === 'script'
            ? questionForm.sql_check_query
            : null,
        order_matters: questionForm.sql_order_matters,
        check_column_names: questionForm.sql_check_column_names,
      }
    }

    if (isFunction) {
      const rawParams = questionForm.function_params.trim()
      const params = rawParams
        ? rawParams.split(',').map((param) => param.trim()).filter(Boolean)
        : null

      return {
        function_name: questionForm.function_name.trim() || 'solve',
        params,
        compare: questionForm.function_compare,
        tolerance: Number(questionForm.function_tolerance),
      }
    }

    let thresholds: Threshold[]

    try {
      thresholds = JSON.parse(questionForm.ml_thresholds)
    } catch {
      throw new Error('ML thresholds must be valid JSON.')
    }

    if (!Array.isArray(thresholds) || thresholds.length === 0) {
      throw new Error('Add at least one ML scoring threshold.')
    }

    return {
      task: questionForm.ml_task,
      target: questionForm.ml_target.trim(),
      metric: questionForm.ml_metric,
      thresholds,
    }
  }

  const saveQuestion = async () => {
    setLoading(true)
    setError('')
    setMessage('')

    try {
      if (!questionForm.title.trim()) {
        throw new Error('Question title is required.')
      }

      if (!questionForm.statement_md.trim()) {
        throw new Error('Problem statement is required.')
      }

      if (!questionForm.reference_code.trim()) {
        throw new Error('Reference solution code is required.')
      }

      if (isSQL && questionForm.reference_language !== 'sql') {
        throw new Error('SQL questions must use the sql reference language.')
      }

      if (isHTML && questionForm.reference_language !== 'html') {
        throw new Error('HTML questions must use the html reference language.')
      }

      if (isML && !questionForm.ml_target.trim()) {
        throw new Error('ML target column is required.')
      }

      if (isFunction) {
        if (!questionForm.function_name.trim()) {
          throw new Error('Function name is required.')
        }

        const tolerance = Number(questionForm.function_tolerance)
        if (!Number.isFinite(tolerance) || tolerance < 0 || tolerance > 1) {
          throw new Error('Function tolerance must be between 0 and 1.')
        }

        const params = questionForm.function_params
          .split(',')
          .map((param) => param.trim())
          .filter(Boolean)

        if (params.length > 20) {
          throw new Error('Function questions support up to 20 parameters.')
        }

        if (new Set(params).size !== params.length) {
          throw new Error('Function parameter names must be unique.')
        }
      }

      const graderConfig = buildGraderConfig()
      const body = {
        title: questionForm.title.trim(),
        statement_md: questionForm.statement_md,
        difficulty: questionForm.difficulty,
        tags: questionForm.tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
        marks: Number(questionForm.marks),
        grader: questionForm.grader,
        grader_config: graderConfig,
        reference_language: isML
          ? 'python3-ml'
          : questionForm.reference_language,
        reference_code: questionForm.reference_code,
      }

      const saved = questionId
        ? await apiRequest<Question>(
            `/questions/${questionId}`,
            'PATCH',
            body,
          )
        : await apiRequest<Question>(
            `/subjects/${subjectId}/questions`,
            'POST',
            body,
          )

      setQuestionId(saved.id)
      setMessage('Question saved.')
      await loadQuestions()
      setCurrentStep(2)
      setMaxUnlocked((previous) => Math.max(previous, 2))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save question.')
    } finally {
      setLoading(false)
    }
  }

  const saveTest = async () => {
    if (!questionId) return

    setLoading(true)
    setError('')
    setMessage('')

    try {
      if (!testDraft.input.trim()) {
        throw new Error('Test input is required.')
      }

      if (isFunction) {
        try {
          const args = JSON.parse(testDraft.input)
          if (!Array.isArray(args)) {
            throw new Error('Test input must be a JSON array.')
          }
          if (testDraft.expected_output.trim()) {
            JSON.parse(testDraft.expected_output)
          }
        } catch {
          throw new Error(
            'Function tests need JSON input arguments and valid JSON expected output.',
          )
        }
      }

      const item = {
        input: testDraft.input,
        expected_output: testDraft.expected_output.trim()
          ? testDraft.expected_output
          : null,
        is_sample: testDraft.is_sample,
        group: testDraft.group.trim() || 'main',
        weight: Number(testDraft.weight),
      }

      if (editingTestId) {
        await apiRequest(
          `/questions/${questionId}/tests/${editingTestId}`,
          'PATCH',
          item,
        )
        setMessage('Test updated.')
      } else {
        await apiRequest(`/questions/${questionId}/tests`, 'POST', {
          tests: [item],
        })
        setMessage('Test added.')
      }

      await loadTests(questionId)
      setTestDraft(emptyTest())
      setEditingTestId('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save test.')
    } finally {
      setLoading(false)
    }
  }

  const editTest = async (id: string) => {
    if (!questionId) return

    setError('')

    try {
      const item = await apiRequest<TestCase>(
        `/questions/${questionId}/tests/${id}`,
      )

      setEditingTestId(id)
      setTestDraft({
        input: item.input ?? '',
        expected_output: item.expected_output ?? '',
        is_sample: item.is_sample,
        group: item.group,
        weight: item.weight,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load test.')
    }
  }

  const deleteTest = async (id: string) => {
    if (!questionId || !window.confirm('Delete this test?')) return

    setError('')

    try {
      await apiRequest(`/questions/${questionId}/tests/${id}`, 'DELETE')
      await loadTests(questionId)
      setMessage('Test deleted.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete test.')
    }
  }

  const generateOutputs = async () => {
    if (!questionId) return

    setLoading(true)
    setError('')
    setMessage('')
    setGeneration(null)

    try {
      const report = await apiRequest<Record<string, unknown>>(
        `/questions/${questionId}/generate-outputs`,
        'POST',
      )

      setGeneration(report)

      if (report.ok === true) {
        setMessage('Reference outputs generated successfully.')
        setCurrentStep(4)
        setMaxUnlocked((previous) => Math.max(previous, 4))
        await loadTests(questionId)
      } else {
        setError(
          'Output generation did not complete successfully. Review the report below.',
        )
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not generate outputs.',
      )
    } finally {
      setLoading(false)
    }
  }

  const uploadMLDatasets = async () => {
    if (!questionId) return

    if (!trainFile || !testFile) {
      setError('Choose both the training CSV and test CSV.')
      return
    }

    setLoading(true)
    setError('')
    setMessage('')
    setDatasetResult(null)

    try {
      const result = await uploadDataset(
        questionId,
        trainFile,
        testFile,
        datasetIsSample,
        datasetWeight,
        datasetGroup,
      )

      setDatasetResult(result)
      await loadTests(questionId)
      setMessage('ML dataset uploaded.')
      setCurrentStep(3)
      setMaxUnlocked((previous) => Math.max(previous, 3))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not upload dataset.')
    } finally {
      setLoading(false)
    }
  }

  const validateML = async () => {
    if (!questionId) return

    setLoading(true)
    setError('')
    setMessage('')
    setMlValidation(null)

    try {
      const result = await apiRequest<unknown>(
        `/questions/${questionId}/validate`,
        'POST',
      )

      setMlValidation(result)
      if (isChecklist) {
        // Stay on Step C so the professor sees which checks the reference page fails.
        const fraction = (result as GradeReport).fraction
        setMessage(fraction === 1 ? 'The reference passes every check.' : 'Warning: the reference fails some checks.')
      } else {
        setMessage('ML validation completed. Review the metric results below.')
        setCurrentStep(4)
        setMaxUnlocked((previous) => Math.max(previous, 4))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'ML validation failed.')
    } finally {
      setLoading(false)
    }
  }

  const trySolution = async () => {
    if (!questionId) return

    setLoading(true)
    setError('')
    setMessage('')
    setGrade(null)

    try {
      const report = await apiRequest<GradeReport>(
        `/questions/${questionId}/try`,
        'POST',
        { language: tryLanguage, code: tryCode },
      )

      setGrade(report)

      const allPassed =
        report.tests.length > 0 &&
        report.tests.every(
          (test) =>
            test.verdict.toLowerCase() === 'accepted' ||
            test.verdict.toLowerCase() === 'passed',
        )

      if (allPassed) {
        setMessage(
          'Warning: this solution passed every test. Try a deliberately incorrect solution.',
        )
      } else {
        setMessage('Trial completed. Review each test verdict below.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not try solution.')
    } finally {
      setLoading(false)
    }
  }

 

  const nextFromTests = () => {
    if (!questionId) {
      setError('Save the question before adding tests.')
      return
    }

    if (isML) {
      if (tests.length === 0) {
        setError('Upload at least one ML dataset first.')
        return
      }

      setCurrentStep(3)
      setMaxUnlocked((previous) => Math.max(previous, 3))
      setError('')
      setMessage('')
      return
    }

    if (tests.length === 0) {
      setError('Add at least one test case before continuing.')
      return
    }

    setCurrentStep(3)
    setMaxUnlocked((previous) => Math.max(previous, 3))
    setError('')
    setMessage('')
  }

  const metricOptions =
    questionForm.ml_task === 'classification'
      ? ['accuracy', 'f1_macro', 'precision_macro', 'recall_macro']
      : ['r2', 'rmse', 'mae']

  return (
    <section className="question-wizard">
      <header className="question-wizard__header">
        <div>
          <h2>Question editor</h2>
          <p>Create, test, and validate questions for this subject.</p>
        </div>

        <button type="button" onClick={resetEditor}>
          New question
        </button>
      </header>

      <section className="question-wizard__existing">
        <h3>Existing questions</h3>

        {questions.length === 0 ? (
          <p>No questions yet. Create your first question below.</p>
        ) : (
          <ul>
            {questions.map((question) => (
              <li key={question.id}>
                <span>
                  <strong>{question.title}</strong>
                  {' — '}
                  {question.grader}
                </span>
                <button
                  type="button"
                  onClick={() => void openQuestion(question.id)}
                  disabled={loading}
                >
                  Edit
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

     <Stepper
  steps={steps}
  currentStep={currentStep}
  onStepClick={setCurrentStep}
/>
      {error && (
        <div className="question-wizard__error" role="alert">
          {error}
          <button type="button" onClick={() => setError('')}>
            Dismiss
          </button>
        </div>
      )}

      {message && (
        <div className="question-wizard__message" role="status">
          {message}
          <button type="button" onClick={() => setMessage('')}>
            Dismiss
          </button>
        </div>
      )}

      {loading && <p role="status">Working…</p>}

      {currentStep === 1 && (
        <section>
          <h3>Step A — Write question</h3>

          <label>
            Question title
            <input
              value={questionForm.title}
              onChange={(event) => setField('title', event.target.value)}
              required
            />
          </label>

          <label>
            Problem statement (Markdown)
            <textarea
              rows={8}
              value={questionForm.statement_md}
              onChange={(event) =>
                setField('statement_md', event.target.value)
              }
              required
            />
          </label>

          <div className="question-wizard__preview">
            <h4>Preview</h4>
            <SafeMarkdown content={questionForm.statement_md} />
          </div>

          {isML && (
            <p>
              ML hint: implement{' '}
              <code>solve(train_csv, test_csv)</code> and return a list of
              predictions for the test rows.
            </p>
          )}

          <label>
            Difficulty
            <select
              value={questionForm.difficulty}
              onChange={(event) => setField('difficulty', event.target.value)}
            >
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </label>

          <label>
            Marks
            <NumberInput
              min={1}
              value={questionForm.marks}
              onChange={(value) => setField('marks', value)}
            />
          </label>

          <label>
            Topic tags (comma-separated)
            <input
              value={questionForm.tags}
              onChange={(event) => setField('tags', event.target.value)}
              placeholder="arrays, loops, sorting"
            />
          </label>

          <label>
            Grader
            <select
              value={questionForm.grader}
              onChange={(event) => {
                const grader = event.target.value
                setField('grader', grader)

                // Graders with one language pick it; leaving them goes back to Python.
                const fixed: Record<string, string> = { sql: 'sql', ml: 'python3-ml', html: 'html', plot: 'python3-ml' }
                if (fixed[grader]) {
                  setField('reference_language', fixed[grader])
                  setTryLanguage(fixed[grader])
                } else if (Object.values(fixed).includes(questionForm.reference_language)) {
                  setField('reference_language', 'python3')
                  setTryLanguage('python3')
                }
              }}
            >
              {graders.length ? (
                graders.map((grader) => (
                  <option key={grader.key} value={grader.key}>
                    {grader.key} — {grader.description}
                  </option>
                ))
              ) : (
                <>
                  <option value="stdio">Standard</option>
                  <option value="sql">SQL</option>
                  <option value="function">Function</option>
                  <option value="ml">ML</option>
                </>
              )}
            </select>
          </label>

          {isStandard && (
            <label>
              Output comparison
              <select
                value={questionForm.compare}
                onChange={(event) => setField('compare', event.target.value)}
              >
                {compareModes.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            </label>
          )}

          {isPlot && (
            <fieldset>
              <legend>Plot grader configuration</legend>
              <p style={{ marginTop: 0 }}>
                Students draw charts with matplotlib or seaborn (Python 3 ML). The code runs
                off-screen; each test is a list of checks on the figure (chart type, title,
                axis labels, legend, number of bars…). Add the checks in Step B.
              </p>
              <label>
                Data file name (students read it with pd.read_csv)
                <input
                  value={questionForm.plot_data_file}
                  onChange={(event) => setField('plot_data_file', event.target.value)}
                  placeholder="data.csv"
                />
              </label>
              <label>
                Data (CSV text, optional, up to 2 MB)
                <textarea
                  rows={6}
                  value={questionForm.plot_data_csv}
                  onChange={(event) => setField('plot_data_csv', event.target.value)}
                  placeholder={'month,region,sales\nJan,North,120\n…'}
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={questionForm.html_partial_credit}
                  onChange={(event) => setField('html_partial_credit', event.target.checked)}
                />{' '}
                Partial credit: a test earns the share of its checks that pass
              </label>
            </fieldset>
          )}

          {isHTML && (
            <fieldset>
              <legend>HTML grader configuration</legend>
              <p style={{ marginTop: 0 }}>
                Students write an HTML page. It is checked, not rendered: each test is a
                list of checks (elements, attributes, text) the page must pass. Add the
                checks in Step B.
              </p>
              <label>
                <input
                  type="checkbox"
                  checked={questionForm.html_partial_credit}
                  onChange={(event) => setField('html_partial_credit', event.target.checked)}
                />{' '}
                Partial credit: a test earns the share of its checks that pass (untick: all or nothing)
              </label>
            </fieldset>
          )}

          {isSQL && (
            <fieldset>
              <legend>SQL grader configuration</legend>

              <label>
                Schema SQL (CREATE TABLE statements)
                <textarea
                  rows={6}
                  value={questionForm.sql_schema}
                  onChange={(event) =>
                    setField('sql_schema', event.target.value)
                  }
                  placeholder="CREATE TABLE students (...);"
                />
              </label>

              <label>
                SQL mode
                <select
                  value={questionForm.sql_mode}
                  onChange={(event) =>
                    setField('sql_mode', event.target.value)
                  }
                >
                  <option value="query">Query — compare result rows</option>
                  <option value="script">Script — run statements and check query</option>
                </select>
              </label>

              {questionForm.sql_mode === 'script' && (
                <label>
                  Check query (SELECT statement)
                  <textarea
                    rows={4}
                    value={questionForm.sql_check_query}
                    onChange={(event) =>
                      setField('sql_check_query', event.target.value)
                    }
                    placeholder="SELECT * FROM results;"
                    required
                  />
                </label>
              )}

              <label>
                <input
                  type="checkbox"
                  checked={questionForm.sql_order_matters}
                  onChange={(event) =>
                    setField('sql_order_matters', event.target.checked)
                  }
                />
                Row order matters
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={questionForm.sql_check_column_names}
                  onChange={(event) =>
                    setField('sql_check_column_names', event.target.checked)
                  }
                />
                Check column names
              </label>

              <p>Test input should contain SQL INSERT statements.</p>
            </fieldset>
          )}

          {isFunction && (
            <fieldset>
              <legend>Function grader configuration</legend>

              <label>
                Function name
                <input
                  value={questionForm.function_name}
                  onChange={(event) =>
                    setField('function_name', event.target.value)
                  }
                  placeholder="solve"
                />
              </label>

              <label>
                Parameters (comma-separated)
                <input
                  value={questionForm.function_params}
                  onChange={(event) =>
                    setField('function_params', event.target.value)
                  }
                  placeholder="nums, target"
                />
              </label>

              <label>
                Comparison mode
                <select
                  value={questionForm.function_compare}
                  onChange={(event) =>
                    setField('function_compare', event.target.value)
                  }
                >
                  <option value="exact">Exact</option>
                  <option value="float">Floating point</option>
                  <option value="unordered">Unordered</option>
                </select>
              </label>

              <label>
                Floating-point tolerance
                <NumberInput
                  min={0}
                  max={1}
                  step="any"
                  value={questionForm.function_tolerance}
                  onChange={(value) =>
                    setField('function_tolerance', String(value))
                  }
                />
              </label>

              <p>
                Function test input must be a JSON array of arguments, for
                example <code>[[2, 7, 11, 15], 9]</code>. Expected output must
                be valid JSON.
              </p>
            </fieldset>
          )}

          {isML && (
            <fieldset>
              <legend>Machine-learning grader configuration</legend>

              <label>
                Task
                <select
                  value={questionForm.ml_task}
                  onChange={(event) => {
                    const task = event.target.value
                    setField('ml_task', task)
                    setField(
                      'ml_metric',
                      task === 'classification' ? 'accuracy' : 'r2',
                    )
                  }}
                >
                  <option value="classification">Classification</option>
                  <option value="regression">Regression</option>
                </select>
              </label>

              <label>
                Target column
                <input
                  value={questionForm.ml_target}
                  onChange={(event) =>
                    setField('ml_target', event.target.value)
                  }
                  placeholder="target"
                  required
                />
              </label>

              <label>
                Metric
                <select
                  value={questionForm.ml_metric}
                  onChange={(event) => setField('ml_metric', event.target.value)}
                >
                  {metricOptions.map((metric) => (
                    <option key={metric} value={metric}>
                      {metric}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Scoring thresholds (JSON)
                <textarea
                  rows={5}
                  value={questionForm.ml_thresholds}
                  onChange={(event) =>
                    setField('ml_thresholds', event.target.value)
                  }
                />
              </label>

              <p>
                Thresholds define the metric score needed for each credit
                level. For RMSE and MAE, lower values are better.
              </p>
            </fieldset>
          )}

          <fieldset>
            <legend>Reference solution</legend>

            <label>
              Language
              <select
                value={questionForm.reference_language}
                onChange={(event) =>
                  setField('reference_language', event.target.value)
                }
                disabled={isSQL || isML || isHTML || isPlot}
              >
                {languageOptions(questionForm.reference_language).map((l) => (
                  <option key={l.key} value={l.key}>{l.name}</option>
                ))}
              </select>
            </label>

            <label>
              Reference solution code
              <textarea
                rows={10}
                value={questionForm.reference_code}
                onChange={(event) =>
                  setField('reference_code', event.target.value)
                }
                placeholder={
                  isSQL
                    ? 'SELECT ...'
                    : isPlot
                      ? 'import pandas as pd\nimport matplotlib.pyplot as plt\ndf = pd.read_csv("data.csv")\n…'
                      : isHTML
                      ? '<!DOCTYPE html>\n<html>\n  <head><title>…</title></head>\n  <body>…</body>\n</html>'
                      : isML
                      ? 'def solve(train_csv, test_csv):\n    ...'
                      : isFunction
                        ? 'def solve(nums, target):\n    ...'
                        : 'Write the reference solution here'
                }
                required
              />
            </label>
          </fieldset>

          <button type="button" onClick={() => void saveQuestion()} disabled={loading}>
            {questionId ? 'Save question and continue' : 'Create question and continue'}
          </button>
        </section>
      )}

      {currentStep === 2 && (
        <section>
          <h3>Step B — {isML ? 'Upload datasets' : 'Add tests'}</h3>

          {questionId && !isHTML && <QuestionFilesPanel key={questionId} questionId={questionId} />}

          {!questionId ? (
            <p>Save the question first.</p>
          ) : isML ? (
            <>
              <p>
                Upload training and test CSV files. The backend uses the target
                column for scoring; ordinary expected-output generation is not
                used for ML questions.
              </p>

              <label>
                Training CSV
                <input
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(event) =>
                    setTrainFile(event.target.files?.[0] ?? null)
                  }
                />
              </label>

              <label>
                Test CSV
                <input
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(event) =>
                    setTestFile(event.target.files?.[0] ?? null)
                  }
                />
              </label>

              <label>
                Dataset group
                <input
                  value={datasetGroup}
                  onChange={(event) => setDatasetGroup(event.target.value)}
                />
              </label>

              <label>
                Dataset weight
                <NumberInput
                  min={1}
                  value={datasetWeight}
                  onChange={setDatasetWeight}
                />
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={datasetIsSample}
                  onChange={(event) => setDatasetIsSample(event.target.checked)}
                />
                Mark this dataset as a sample
              </label>

              <button
                type="button"
                onClick={() => void uploadMLDatasets()}
                disabled={loading || !trainFile || !testFile}
              >
                Upload dataset
              </button>

              {datasetResult !== null && (
                <pre>{JSON.stringify(datasetResult, null, 2)}</pre>
              )}

              <h4>Uploaded dataset tests</h4>
              {tests.length === 0 ? (
                <p>No dataset tests are listed yet.</p>
              ) : (
                <ul>
                  {tests.map((test) => (
                    <li key={test.id}>
                      Dataset {test.ordinal} — group: {test.group}, weight:{' '}
                      {test.weight}, sample: {test.is_sample ? 'yes' : 'no'}
                    </li>
                  ))}
                </ul>
              )}

              <button
                type="button"
                onClick={nextFromTests}
                disabled={loading || tests.length === 0}
              >
                Continue to ML validation
              </button>
            </>
          ) : (
            <>
              <label>
                {isSQL
                  ? 'Test input — SQL INSERT statements'
                  : isChecklist
                    ? 'Test checks — one per line'
                    : isFunction
                      ? 'Test input — JSON array of function arguments'
                      : 'Test input — stdin'}
                <textarea
                  rows={isChecklist ? 8 : 5}
                  value={testDraft.input}
                  onChange={(event) =>
                    setTestDraft((previous) => ({
                      ...previous,
                      input: event.target.value,
                    }))
                  }
                  placeholder={
                    isSQL
                      ? "INSERT INTO students VALUES (1, 'A');"
                      : isHTML
                        ? HTML_CHECK_EXAMPLE
                        : isPlot
                        ? PLOT_CHECK_EXAMPLE
                        : isFunction
                        ? '[[2, 7, 11, 15], 9]'
                        : 'Input for the program'
                  }
                />
              </label>

              {isChecklist ? (
                <details>
                  <summary>Check syntax</summary>
                  <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.85em' }}>{isPlot ? PLOT_CHECK_HELP : HTML_CHECK_HELP}</pre>
                  <p style={{ fontSize: '0.9em' }}>
                    These tests have no expected output: the checks are the test.
                    Mark one test as a sample so students see which checks they fail.
                  </p>
                </details>
              ) : (
                <label>
                  Expected output {isFunction ? '(valid JSON)' : '(optional)'}
                  <textarea
                    rows={4}
                    value={testDraft.expected_output}
                    onChange={(event) =>
                      setTestDraft((previous) => ({
                        ...previous,
                        expected_output: event.target.value,
                      }))
                    }
                    placeholder={
                      isFunction ? '[0, 1]' : 'Can be generated in Step C'
                    }
                  />
                </label>
              )}

              <label>
                Group
                <input
                  value={testDraft.group}
                  onChange={(event) =>
                    setTestDraft((previous) => ({
                      ...previous,
                      group: event.target.value,
                    }))
                  }
                />
              </label>

              <label>
                Weight
                <NumberInput
                  min={1}
                  value={testDraft.weight}
                  onChange={(value) =>
                    setTestDraft((previous) => ({
                      ...previous,
                      weight: value,
                    }))
                  }
                />
              </label>

              <label>
                <input
                  type="checkbox"
                  checked={testDraft.is_sample}
                  onChange={(event) =>
                    setTestDraft((previous) => ({
                      ...previous,
                      is_sample: event.target.checked,
                    }))
                  }
                />
                Sample test (visible to students)
              </label>

              <div>
                <button
                  type="button"
                  onClick={() => void saveTest()}
                  disabled={loading}
                >
                  {editingTestId ? 'Update test' : 'Add test'}
                </button>

                {editingTestId && (
                  <button
                    type="button"
                    onClick={() => {
                      setEditingTestId('')
                      setTestDraft(emptyTest())
                    }}
                  >
                    Cancel edit
                  </button>
                )}
              </div>

              <h4>Saved tests</h4>

              {tests.length === 0 ? (
                <p>No tests added yet.</p>
              ) : (
                <ul>
                  {tests.map((test) => (
                    <li key={test.id}>
                      <div>
                        <strong>Test {test.ordinal}</strong>
                        {' — '}
                        {test.input_preview}
                        <br />
                        Expected:{' '}
                        {test.expected_preview ?? 'Not generated yet'}
                        <br />
                        Group: {test.group}, weight: {test.weight},{' '}
                        {test.is_sample ? 'sample' : 'hidden'}
                      </div>

                      <button
                        type="button"
                        onClick={() => void editTest(test.id)}
                      >
                        Edit
                      </button>

                      <button
                        type="button"
                        onClick={() => void deleteTest(test.id)}
                      >
                        Delete
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <button
                type="button"
                onClick={nextFromTests}
                disabled={loading || tests.length === 0}
              >
                Continue to Step C
              </button>
            </>
          )}
        </section>
      )}

      {currentStep === 3 && (
        <section>
          <h3>Step C — {isML ? 'Validate ML solution' : isHTML ? 'Validate reference page' : isPlot ? 'Validate reference chart' : 'Generate outputs'}</h3>

          {isChecklist ? (
            <>
              <p>
                These tests have no expected outputs. Check that your reference
                {isPlot ? ' code draws a chart that passes' : ' page passes'} every check; exams refuse to open otherwise.
              </p>
              <button
                type="button"
                onClick={() => void validateML()}
                disabled={loading || !questionId || tests.length === 0}
              >
                {isPlot ? 'Validate reference chart' : 'Validate reference page'}
              </button>
              {mlValidation !== null && (
                <HtmlValidation report={mlValidation as GradeReport} />
              )}
              {(mlValidation as GradeReport | null)?.fraction === 1 && (
                <button
                  type="button"
                  onClick={() => {
                    setCurrentStep(4)
                    setMaxUnlocked((previous) => Math.max(previous, 4))
                  }}
                >
                  Continue to Step D
                </button>
              )}
            </>
          ) : isML ? (
            <>
              <p>
                The backend runs the reference solution and evaluates the
                configured metric against the hidden target labels.
              </p>

              <button
                type="button"
                onClick={() => void validateML()}
                disabled={loading || !questionId || tests.length === 0}
              >
                Validate ML reference solution
              </button>

              {mlValidation !== null && (
                <pre>{JSON.stringify(mlValidation, null, 2)}</pre>
              )}
            </>
          ) : (
            <>
              <p>
                Run the reference solution against every test to generate
                expected outputs.
              </p>

              <button
                type="button"
                onClick={() => void generateOutputs()}
                disabled={
                  loading ||
                  !questionId ||
                  tests.length === 0 ||
                  !questionForm.reference_code.trim()
                }
              >
                Generate outputs
              </button>

              {generation !== null && (
                <pre>{JSON.stringify(generation, null, 2)}</pre>
              )}

              {generation?.ok === true && (
                <button
                  type="button"
                  onClick={() => {
                    setCurrentStep(4)
                    setMaxUnlocked((previous) => Math.max(previous, 4))
                  }}
                >
                  Continue to Step D
                </button>
              )}
            </>
          )}
        </section>
      )}

      {currentStep === 4 && (
        <section>
          <h3>Step D — {isML ? 'ML validation results' : 'Try solution'}</h3>

          {isML ? (
            <>
              <p>Review the metric results and threshold messages from Step C.</p>
              {mlValidation !== null ? (
                <pre>{JSON.stringify(mlValidation, null, 2)}</pre>
              ) : (
                <p>Run ML validation in Step C to see the results.</p>
              )}

              <button
                type="button"
                onClick={() => void validateML()}
                disabled={loading || !questionId}
              >
                Run validation again
              </button>
            </>
          ) : (
            <>
              <p>
                Paste a deliberately incorrect or slow solution to check
                whether your test cases detect it.
              </p>

              <label>
                Language
                <select
                  value={tryLanguage}
                  onChange={(event) => setTryLanguage(event.target.value)}
                >
                  {languageOptions(tryLanguage).map((l) => (
                    <option key={l.key} value={l.key}>{l.name}</option>
                  ))}
                </select>
              </label>

              <label>
                Trial solution
                <textarea
                  rows={10}
                  value={tryCode}
                  onChange={(event) => setTryCode(event.target.value)}
                  placeholder="Paste the solution you want to test"
                />
              </label>

              <button
                type="button"
                onClick={() => void trySolution()}
                disabled={loading || !questionId || !tryCode.trim()}
              >
                Try solution
              </button>

              {grade && (
                <div>
                  <h4>Trial report</h4>
                  <p>
                    Passed: {grade.passed} / {grade.total}
                  </p>
                  <p>
                    Score fraction: {grade.fraction}
                  </p>

                  {grade.compile_output && (
                    <pre>{grade.compile_output}</pre>
                  )}

                  <ul>
                    {grade.tests.map((test) => (
                      <li key={test.test_id}>
                        <strong>
                          Test {test.ordinal}: {test.verdict}
                        </strong>

                        {test.message && <p>{test.message}</p>}

                        {test.time_ms != null && (
                          <p>Time: {test.time_ms} ms</p>
                        )}

                        {test.stdout && (
                          <details>
                            <summary>Standard output</summary>
                            <pre>{test.stdout}</pre>
                          </details>
                        )}

                        {test.stderr && (
                          <details>
                            <summary>Standard error</summary>
                            <pre>{test.stderr}</pre>
                          </details>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </section>
      )}
    </section>
  )
}