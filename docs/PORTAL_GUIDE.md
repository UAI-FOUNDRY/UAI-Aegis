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

**Dashboard (professor home)**
- `GET /dashboard`: every subject I teach, its languages, its exams, and per batch:
  enrolled, started, submitted, locked, flagged, still grading; plus which languages
  students answered in. Build the home screen from this one call.
- `GET /subjects`: subjects shared with me (admins see all).
- `POST /api/v1/subjects`: create (the creating professor becomes owner).

**Subject**
- `PUT /subjects/{id}/languages` with language keys from `GET /catalog/languages`.
- `PUT /subjects/{id}/batches` with ids from `GET /catalog/batches`.
- Sharing: `GET/POST /subjects/{id}/access`, `DELETE /subjects/{id}/access/{grant_id}`.
  Roles: owner, editor, grader, viewer. Add `exam_id` to share one exam only (a
  co-evaluator). Find colleagues with `GET /catalog/professors?q=`.

**Question editor** (recommended flow, show it as steps)
1. `POST /subjects/{id}/questions` with statement (Markdown), difficulty, tags, marks,
   and the reference solution + its language. (`starter_code` exists but leave it out
   of the form: the students' editor should start empty.)
2. `POST /questions/{id}/tests` with inputs (expected outputs optional), sample toggle,
   group and weight.
3. `POST /questions/{id}/generate-outputs`: the reference solution writes every expected
   output and measures the time limit. Show failures per test if `ok` is false.
4. `POST /questions/{id}/try` with a wrong or slow solution to prove the tests catch it.
- `GET /questions/{id}/tests` gives previews; `GET /questions/{id}/tests/{tid}` gives the
  full text. Grader options: `GET /graders`; comparison modes in `grader_config.compare`
  (`lines`, `exact`, `whitespace`, `case_insensitive`, `float`, `unordered_lines`).

**SQL questions** (grader `sql`, language `sql`)
- `grader_config`: `schema_sql` (CREATE TABLEs), `mode` `query` (compare a SELECT's
  result) or `script` (run statements, then compare `check_query`), `order_matters`,
  `check_column_names`.
- Each test's input is that test's data (INSERT statements). Reference solution in
  `sql`, then generate outputs as usual.

**Function questions** (grader `function`, languages Python or JavaScript)
- LeetCode-style: students write a function and **return** the answer. No input
  parsing or printing. The function's **name does not matter**: the grader finds the
  student's function (helpers and `class Solution` style included) and calls it with
  each test's arguments; only whether it works decides the marks.
- `grader_config`: `params` (e.g. `["nums", "target"]`; every test must have that many
  arguments), `compare` (`exact`, `float`, or `unordered` for a list in any order),
  `tolerance`, and optionally `function_name` (used first if the student has a function
  with that name).
- Students start with an **empty editor**; describe the inputs and the expected return
  value in the statement.
- A test's input is the **arguments as a JSON list**: `[[2, 7, 11, 15], 9]`. Expected
  outputs are the return value as JSON (`[0, 1]`); normally generate them from the
  reference solution. Bad JSON or the wrong number of arguments is refused with
  `400 bad_test` when the test is saved, so show that message next to the test.
- Show a small hint in the editor: "Input: one JSON list of arguments".

**ML questions** (grader `ml`, language `python3-ml`)
- `grader_config`: `task` (classification/regression), `target` (label column),
  `metric`, `thresholds` like `[{"at": 0.85, "credit": 1}, {"at": 0.7, "credit": 0.5}]`.
- Add tests with `POST /questions/{id}/datasets` (multipart: `train`, `test` CSV files,
  `is_sample`, `weight`). Mark one as sample so students can see the data. No output
  generation needed; `validate` checks the reference solution reaches the thresholds.
- Test results carry a `message` such as "accuracy 0.8833 (full marks at >= 0.85)".

**Exam setup**
- `POST /subjects/{id}/exams`: title, duration, join window, pool (`question_ids`),
  selection rules (e.g. 1 easy + 1 medium per student), policy (strikes, what locks,
  run limit, reveal hidden results), marking (`auto_percent`), and `languages` to lock
  the exam (e.g. `["python3"]` for a Python lab exam; must be languages of the subject;
  cannot change once the exam is live).
- `GET /exams/{id}/readiness`: show problems and warnings before opening.
- `POST /exams/{id}/open`: returns `access_code`. Show it **large** for the projector.
  `POST /exams/{id}/rotate-code` if it leaks.

**Live monitor** (pushed over a WebSocket; no polling needed)
- `GET /exams/{id}/monitor`: one row per enrolled student: not started, active, locked,
  submitted, terminated; connected; strikes; lock reason; device change pending.
  Sort locked students to the top (the API already does) and make them stand out.
- For instant updates, open the live stream instead of polling (example below). The
  server pushes a change within about a second: a student starts, comes back online,
  gets a strike, is locked, asks to switch laptops, hands in, is graded.
- Row actions: `POST /exams/{id}/sessions/{sid}/lock`, `.../resume`
  (`extra_minutes`, `reset_strikes`, `approve_new_device`), `.../terminate` (reason
  required). Confirm before terminating.
- `GET /exams/{id}/sessions/{sid}`: event timeline, current code, run history.
- `POST /exams/{id}/close` ends the exam for everyone.

**Grading and results**
- `GET /exams/{id}/results?batch_id=&language=`: totals per student with their batch and
  the languages they used.
- `GET /exams/{id}/answers?language=python3&batch_id=&question_id=`: every answer with
  its code and marks, for checking answers one after another (paginated).
- `GET /exams/{id}/monitor?batch_code=`: the live monitor for one division.
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

## Live monitor WebSocket

Browsers cannot send the `Authorization` header on a WebSocket, so the portal first gets
a one-time ticket (valid 30 s, single use), then connects with it.

```ts
async function openLiveMonitor(examId: string, onSnapshot, onRow, batchCode?: string) {
  let stopped = false, socket: WebSocket | undefined, retry = 1000;
  async function connect() {
    const r = await api.post(`/api/v1/exams/${examId}/live-ticket`);   // normal bearer request
    if (r.status === 403 || r.status === 404) return;                   // no access: stop
    const { path } = await r.json();
    const base = API_URL.replace(/^http/, "ws");                        // http->ws, https->wss
    socket = new WebSocket(base + path + (batchCode ? `&batch_code=${encodeURIComponent(batchCode)}` : ""));
    socket.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "snapshot") onSnapshot(msg.data);  // same shape as GET /monitor: replace everything
      if (msg.type === "row") onRow(msg.data, msg.change); // replace the row with the same student.id
      retry = 1000;
    };
    socket.onclose = (e) => {
      if (stopped || e.code === 4403) return;             // closed by us, or access removed
      setTimeout(connect, retry);                         // 4401 (old ticket), 1012 (restart), network
      retry = Math.min(retry * 2, 15000);
    };
  }
  connect();
  return () => { stopped = true; socket?.close(); };
}
```

- Messages: `{"type": "snapshot", "data": <GET /monitor>}` on connect, about every
  10 s, and after exam-wide changes (opened, closed, regrade); and
  `{"type": "row", "change": "locked", "changes": [...], "server_now": "...", "data":
  <MonitorRow>}` for one student.
- After a `row`, recompute the counts from your rows: count by `status`, plus
  `disconnected` = active or locked and not `connected`.
- `change` values: `started`, `connected`, `strike`, `event`, `locked`, `resumed`,
  `device_change`, `submitted`, `terminated`, `grading`, `graded`. Flash the row and
  play a short sound on `locked` and `device_change`, since those need the professor.
- Close codes: `4401` get a new ticket and reconnect, `4403` stop (no access or signed
  out), `4429` too many monitors open, `1012` server restarting (reconnect).
- If the WebSocket cannot connect at all (an old proxy, say), fall back to polling
  `GET /monitor` every 5 s.
- The lock/resume/terminate endpoints still return the updated row, so the screen can
  update at once without waiting for the push.

## Demo data

```bash
cd backend && alembic upgrade head && python -m app.cli demo-data && uvicorn app.main:app --reload
```

`prof.demo@aegis.local` and `admin.demo@aegis.local`, password `Aegis-demo-2026!`.
Four demo labs: Programming (Python-only exam), DBMS (SQL), ML, and DSA (a function
question, "Two sum").

## Documentation maintenance

Portal setup and usage instructions are maintained by the project team.