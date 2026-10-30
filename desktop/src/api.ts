// Typed wrappers around the Rust commands (src-tauri/src/commands.rs).
// The web view never talks to the server or sees the device key; Rust does both.
import { invoke } from "@tauri-apps/api/core";

export type ApiError = {
  status: number;
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

export function isApiError(e: unknown): e is ApiError {
  return typeof e === "object" && e !== null && "code" in e && "message" in e;
}

export function errorMessage(e: unknown): string {
  if (isApiError(e)) return e.message;
  return String(e);
}

export type User = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  must_change_password: boolean;
};

export type LoginResult = {
  user: User;
  device_id: string | null;
  needs_password_change: boolean;
};

export type SessionStatus = "active" | "locked" | "submitted" | "terminated";

export type StudentExam = {
  exam_id: string;
  title: string;
  subject_code: string;
  subject_name: string;
  status: "draft" | "live" | "closed";
  duration_minutes: number;
  opened_at: string | null;
  join_closes_at: string | null;
  session_id: string | null;
  session_status: SessionStatus | null;
  results_released: boolean;
};

export type Status = {
  status: SessionStatus;
  lock_reason: string | null;
  strikes: number;
  strikes_left: number;
  server_now: string;
  deadline_at: string;
  seconds_left: number;
  device_change_pending: boolean;
};

export type Question = {
  id: string;
  ordinal: number;
  title: string;
  statement_md: string;
  difficulty: string;
  marks: number;
  languages: { key: string; name: string; monaco_id: string }[];
  starter_code: Record<string, string>;
  samples: { ordinal: number; input: string; expected_output: string }[];
  hidden_test_count: number;
  draft: Draft | null;
};

export type Draft = {
  language: string;
  code: string;
  revision: number | null;
  updated_at: string;
  /** Saved on this laptop but never confirmed by the server (added by Rust). */
  unsynced?: boolean;
};

export type TestFeedback = {
  ordinal: number;
  is_sample: boolean;
  verdict: string;
  time_ms: number | null;
  input?: string | null;
  expected_output?: string | null;
  stdout?: string | null;
  stderr?: string | null;
  message?: string | null;
};

export type RunOut = {
  passed: number;
  total: number;
  compile_output: string;
  tests: TestFeedback[];
  hidden_summary: string | null;
};

export type Receipt = { question_id: string; receipt: string };

export type SessionView = {
  session_id: string;
  exam_id: string;
  exam_title: string;
  instructions_md: string;
  status: SessionStatus;
  lock_reason: string | null;
  strikes: number;
  max_strikes: number;
  device_change_pending: boolean;
  server_now: string;
  started_at: string;
  deadline_at: string;
  seconds_left: number;
  finish_reason: string | null;
  questions: Question[];
  receipts: Receipt[];
};

export const api = {
  login: (email: string, password: string) => invoke<LoginResult>("login", { email, password }),
  changePassword: (currentPassword: string, newPassword: string) =>
    invoke<LoginResult>("change_password", { currentPassword, newPassword }),
  logout: () => invoke<void>("logout"),
  listExams: () => invoke<StudentExam[]>("list_exams"),
  startExam: (examId: string, accessCode: string | null) =>
    invoke<SessionView>("start_exam", { examId, accessCode }),
  getSession: (sessionId: string) => invoke<SessionView>("get_session", { sessionId }),
  heartbeat: (sessionId: string) => invoke<Status>("heartbeat", { sessionId }),
  saveAnswer: (sessionId: string, questionId: string, language: string, code: string, baseRevision: number | null) =>
    invoke<{ revision: number; saved_at: string }>("save_answer", {
      sessionId,
      questionId,
      language,
      code,
      baseRevision,
    }),
  runCode: (sessionId: string, questionId: string, language: string, code: string) =>
    invoke<RunOut>("run_code", { sessionId, questionId, language, code }),
  submitCode: (sessionId: string, questionId: string, language: string, code: string) =>
    invoke<RunOut>("submit_code", { sessionId, questionId, language, code }),
  finishExam: (sessionId: string) =>
    invoke<{ status: SessionStatus; receipts: Receipt[] }>("finish_exam", { sessionId }),
  precheck: () => invoke<{ ok: boolean; problems: { kind: string; message: string }[] }>("precheck"),
  reportBlockedPaste: (sessionId: string, chars: number) =>
    invoke<Status>("report_blocked_paste", { sessionId, chars }),
};
