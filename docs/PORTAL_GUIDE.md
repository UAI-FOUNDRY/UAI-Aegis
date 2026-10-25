# Professor portal ↔ API guide (Dev 3)

The contract is `api/openapi.json`. Generate a typed client from it instead of writing
fetch calls by hand, e.g. with `openapi-typescript` + `openapi-fetch`, into `shared/`, so
the portal (and the desktop app's screens) always match the backend. Interactive docs:
http://localhost:8000/docs while the backend runs.

## Basics

- Sign in: `POST /api/v1/auth/login` with `"client": "portal"`; send
  `Authorization: Bearer <token>`. No cookies.
- Errors: `{"detail": {"code": "...", "message": "..."}}`. Show `message`. Validation
  errors also carry `errors: [{field, message}]` for inline form errors.
- `403 password_change_required`: route to the change-password page.
- `404` also means "not shared with you", by design.
- Buttons: hide what the user cannot do. Subjects carry `my_role`; exams carry `my_caps`
  (`view`, `edit`, `grade`, `proctor`, `share`).
- Set `AEGIS_CORS_ORIGINS` to the portal's dev URL (default `http://localhost:5173`).
- Names, event details, code and Markdown come from users: render them as text, and
  render Markdown with raw HTML disabled plus a sanitiser (e.g. DOMPurify).

## Screens and their endpoints

All paths below start with `/api/v1`.

**Dashboard**
- `GET /api/v1/subjects`: subjects shared with me (admins see all).
- `POST /api/v1/subjects`: create (the creating professor becomes owner).

**Subject**
- `PUT /subjects/{id}/languages` with language keys from `GET /catalog/languages`.
- `PUT /subjects/{id}/batches` with ids from `GET /catalog/batches`.
- Sharing: `GET/POST /subjects/{id}/access`, `DELETE /subjects/{id}/access/{grant_id}`.
  Roles: owner, editor, grader, viewer. Add `exam_id` to share one exam only (a
  co-evaluator). Find colleagues with `GET /catalog/professors?q=`.

**Question editor** (recommended flow, show it as steps)
1. `POST /subjects/{id}/questions` with statement (Markdown), difficulty, tags, marks,
   starter code per language, and the reference solution + its language.
2. `POST /questions/{id}/tests` with inputs (expected outputs optional), sample toggle,
   group and weight.
3. `POST /questions/{id}/generate-outputs`: the reference solution writes every expected
   output and measures the time limit. Show failures per test if `ok` is false.
4. `POST /questions/{id}/try` with a wrong or slow solution to prove the tests catch it.
- `GET /questions/{id}/tests` gives previews; `GET /questions/{id}/tests/{tid}` gives the
  full text. Grader options: `GET /graders`; comparison modes in `grader_config.compare`
  (`lines`, `exact`, `whitespace`, `case_insensitive`, `float`, `unordered_lines`).

**Exam setup**
- `POST /subjects/{id}/exams`: title, duration, join window, pool (`question_ids`),
  selection rules (e.g. 1 easy + 1 medium per student), policy (strikes, what locks,
  run limit, reveal hidden results), marking (`auto_percent`).
- `GET /exams/{id}/readiness`: show problems and warnings before opening.
- `POST /exams/{id}/open`: returns `access_code`. Show it **large** for the projector.
  `POST /exams/{id}/rotate-code` if it leaks.

**Live monitor** (poll every 3-5 s)
- `GET /exams/{id}/monitor`: one row per enrolled student: not started, active, locked,
  submitted, terminated; connected; strikes; lock reason; device change pending.
  Sort locked students to the top (the API already does) and make them stand out.
- Row actions: `POST /exams/{id}/sessions/{sid}/lock`, `.../resume`
  (`extra_minutes`, `reset_strikes`, `approve_new_device`), `.../terminate` (reason
  required). Confirm before terminating.
- `GET /exams/{id}/sessions/{sid}`: event timeline, current code, run history.
- `POST /exams/{id}/close` ends the exam for everyone.

**Grading and results**
- `GET /exams/{id}/results`: totals per student, per question, flags, grading status.
- Session detail shows each answer's code, test verdicts and marks; run code via
  `GET /exams/{id}/sessions/{sid}/runs/{run_id}`.
- `PUT /exams/{id}/sessions/{sid}/answers/{qid}/marks`: `manual_marks` (out of
  `manual_max`) or `override_marks` with a required note.
- `POST /exams/{id}/regrade` after fixing a test (`?everyone=true` to redo all).
- `POST /exams/{id}/release-results` lets students see marks. Excel export comes next.

**Admin**
- Users: `GET/POST /admin/users`, `PATCH /admin/users/{id}`,
  `POST /admin/users/{id}/temporary-password`.
- CSV import: `POST /admin/users/import` (multipart `file`). It is a **dry run** unless
  `?dry_run=false`; show the report and errors with line numbers, then confirm.
  Columns: `email, full_name, role, college_id, department_code, batch_code`.
- Departments, batches and members, languages, app releases: `/admin/...`.
- Audit: `GET /admin/audit`, and a "verify integrity" button on `GET /admin/audit/verify`.

## Demo data

```bash
cd backend && alembic upgrade head && python -m app.cli demo-data && uvicorn app.main:app --reload
```

`prof.demo@aegis.local` and `admin.demo@aegis.local`, password `Aegis-demo-2026!`.
