# UAI Aegis: what is built so far

Backend version 0.7 (Dev 1). The desktop app (Dev 2) and the professor portal (Dev 3)
build against it using docs/CLIENT_PROTOCOL.md and docs/PORTAL_GUIDE.md.

## Accounts and access

- Admin, professor and student roles. Sign-in with email and password (Argon2id); college
  SSO plugs into the same sign-in later, once IT approves.
- Admins manage departments, batches, users (single or CSV import with a dry run),
  languages and allowed app versions, but cannot read questions or answers.
- Professors see a subject only if they own it or it was shared with them (editor,
  grader or viewer), for the whole subject or one exam. Everyone else gets "not found".
- Students see only their own exams and results.

## Labs, subjects and languages (all data, no code changes)

- New labs, subjects, batches, professors and languages are added from the admin API.
- A professor can teach several subjects and languages. An exam can be locked to one
  language (a Python lab exam offers only Python, and the server refuses anything else).
- The dashboard shows each subject's exams with per-batch progress, and answers can be
  listed by exam, batch, language and question ("batch A's Python answers").

## Question types

- Programs (any language): input on stdin, output compared (exact, by line, ignoring
  spaces or case, float tolerance, any order).
- SQL: a query whose result is compared, or statements checked by a query. Each test has
  its own data.
- ML: `solve(train_csv, test_csv)` returns predictions, scored on hidden labels
  (accuracy, F1, precision, recall, R², RMSE, MAE) with partial marks by threshold.
  Professors upload the datasets.
- Function (LeetCode style), Python or JavaScript: the student writes a function with any
  name and returns the answer; only whether it works counts.
- The student's editor starts empty for every type.

## Test cases and marking

- Professors add test inputs; the reference solution generates the expected outputs and
  the time limit. Malformed tests are refused when saved.
- "Try" any solution (e.g. a wrong or slow one) to check the tests catch it, and a
  readiness check before opening (reference passes all tests, enough questions, students
  enrolled...).
- Random questions per student from a pool (e.g. one easy + one medium each).
- Marks: share of tests passed (weights, groups), plus optional manual marks or an
  override with a note. Results released when the professor chooses.

## Exam day

- Professor opens the exam and shows an access code in the lab.
- Students start from the desktop app; every request is signed with a key that lives only
  on that laptop, so answers cannot be sent from anywhere else.
- Autosave, run against samples, submit against all tests, server-side countdown.
- Violations: minor events (switching windows, leaving full screen) are strikes; at the
  limit, or on serious events (remote desktop, VM, screen capture, second screen, another
  laptop), the session locks until the professor resumes (optionally with extra time) or
  ends it. Time keeps running while locked; locked sessions are auto-submitted and
  flagged at the deadline.
- Live monitor: changes appear on the professor's screen within a moment (WebSocket),
  including across several server workers via Redis.
- Closing the exam submits everyone's saved code; grading runs in the background.

## Security

- Student code, hidden tests, reference solutions and access codes are encrypted at rest
  (AES-256-GCM, each value bound to its row so values cannot be swapped).
- Student code runs only in the Judge0 sandbox on the server, with no network.
- Tamper-evident audit log of every sensitive action (HMAC hash chain).
- Rate limits, replay protection, security headers, and production refuses to start with
  development keys or settings. Details in docs/SECURITY.md.

## Checks

- 82 automated tests (SQLite, and PostgreSQL + Redis as on the server).
- `python -m app.cli simulate` plays a full exam in all four demo labs (Python, SQL, ML,
  function) against a running server and prints `ALL CHECKS PASSED`.

## Not built yet

- Desktop app (Dev 2) and professor portal (Dev 3).
- Hosting on the college server; Judge0 set up there (code in languages other than
  Python needs it).
- College SSO (after mentor/IT approval).
- Excel export of marks; question files (e.g. `open("marks.csv")` in a Python lab);
  input generators; code-similarity flags.
