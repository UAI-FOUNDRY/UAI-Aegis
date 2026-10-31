/** Shapes from backend/app/api/v1/exams.py (session detail, results, answers). */
import type { MonitorRow } from './liveMonitor'

export type AnswerOut = {
  question_id: string
  title: string
  ordinal: number
  marks: number
  auto_max: number
  manual_max: number
  language: string | null
  code: string | null
  revision: number | null
  updated_at: string | null
  is_final: boolean
  receipt: string | null
  auto_fraction: number | null
  auto_details: { verdicts?: Record<string, number>; compile_output?: string | null } | null
  manual_marks: number | null
  override_marks: number | null
  manual_note: string | null
  total: number
}

export type EventOut = {
  id: string
  kind: string
  severity: 'info' | 'minor' | 'severe'
  source: string
  details: Record<string, unknown>
  client_ts: string | null
  created_at: string
  caused_lock: boolean
}

export type RunBrief = {
  id: string
  question_id: string
  kind: 'run' | 'submit' | 'final' | string
  language: string
  status: string
  fraction: number | null
  verdicts: Record<string, number>
  created_at: string
}

export type TestResult = {
  test_id: string
  ordinal: number
  is_sample: boolean
  group: string
  weight: number
  verdict: string
  credit: number
  time_ms: number | null
  memory_kb: number | null
  stdout: string
  stderr: string
  message: string
}

export type RunDetail = RunBrief & { code: string; results: TestResult[] }

export type SessionDetail = {
  row: MonitorRow
  events: EventOut[]
  answers: AnswerOut[]
  runs: RunBrief[]
}

export type ResultRow = {
  student: MonitorRow['student']
  batch_id: string | null
  batch_code: string | null
  languages: string[]
  session_id: string
  status: string
  finish_reason: string | null
  flagged: boolean
  grading_status: string
  total: number
  max_total: number
  questions: { question_id: string; title: string; total: number; max: number }[]
}

export type AnswerRow = {
  session_id: string
  student: MonitorRow['student']
  batch_code: string | null
  question_id: string
  question_title: string
  language: string
  code: string
  is_final: boolean
  updated_at: string
  auto_fraction: number | null
  manual_marks: number | null
  override_marks: number | null
  total: number
  marks: number
}

export type ExamInfo = {
  id: string
  subject_id: string
  title: string
  status: 'draft' | 'live' | 'closed'
  marking: { auto_percent?: number }
  languages: string[] | null
  my_caps: string[]
  closed_at: string | null
  results_released_at: string | null
}

/** Grading still to come: show "Grading…" instead of a total. */
export function isGrading(status: string | null | undefined): boolean {
  return status === 'pending' || status === 'running'
}

// Verdict codes from backend/app/grading/registry.py (class Verdict).
export const VERDICT_TEXT: Record<string, string> = {
  AC: 'Passed',
  PA: 'Partly passed',
  WA: 'Wrong answer',
  TLE: 'Time limit exceeded',
  MLE: 'Memory limit exceeded',
  RE: 'Runtime error',
  CE: 'Compile error',
  IE: 'Grader error',
}

export function verdictText(verdict: string): string {
  return VERDICT_TEXT[verdict] ?? verdict
}

export function isPass(verdict: string): boolean {
  return verdict === 'AC'
}

/** "3 passed, 1 wrong answer" from a {verdict: count} map. */
export function verdictSummary(verdicts: Record<string, number> | undefined | null): string {
  if (!verdicts || Object.keys(verdicts).length === 0) return 'No tests run'
  return Object.entries(verdicts)
    .sort(([a], [b]) => (a === 'AC' ? -1 : b === 'AC' ? 1 : a.localeCompare(b)))
    .map(([v, n]) => `${n} ${verdictText(v).toLowerCase()}`)
    .join(', ')
}

const RUN_KIND_TEXT: Record<string, string> = {
  run: 'Ran sample tests',
  submit: 'Checked against all tests',
  final: 'Final grading',
}

export function runKindText(kind: string): string {
  return RUN_KIND_TEXT[kind] ?? kind
}

export function fmtMarks(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return String(Math.round(n * 100) / 100)
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function fmtDuration(fromIso: string | null, toIso: string | null): string {
  if (!fromIso || !toIso) return '—'
  const ms = Date.parse(toIso) - Date.parse(fromIso)
  if (!(ms >= 0)) return '—'
  const minutes = Math.floor(ms / 60000)
  const seconds = Math.floor((ms % 60000) / 1000)
  const h = Math.floor(minutes / 60)
  return h > 0 ? `${h} h ${minutes % 60} min` : `${minutes} min ${seconds} s`
}
