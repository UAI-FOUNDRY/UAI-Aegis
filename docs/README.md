# UAI Aegis

Proctored practical exams for Universal AI University. Students code in a locked-down
desktop app on their own laptops; hidden test cases grade the work on the college
server; professors run everything from a web portal.

This repository holds the whole project: the **backend** (built and tested), the
**professor portal** (guide steps 0–12 built and browser-tested) and the **student
desktop app** (works on Windows: desktop/README.md). See
[What's new for the team](#whats-new-for-the-team) for what each developer needs to know
about the latest changes.

```
uai-aegis/
  backend/      FastAPI API, database, security, grading, exam engine   (Dev 1)
  desktop/      Tauri student app                                        (Dev 2)
  portal/       React professor portal                                   (Dev 3)
  shared/       UI components + API client generated from the contract  (Dev 3)
  api/          openapi.json: the API contract all three apps share
  judge/        Judge0 sandbox config
  docs/         STATUS.md (what is built), SECURITY.md, CLIENT_PROTOCOL.md (Dev 2), PORTAL_GUIDE.md (Dev 3)
```

## Run the backend

New on Windows? [docs/DEV_SETUP_WINDOWS.md](docs/DEV_SETUP_WINDOWS.md) sets up and tests all three
parts (backend, portal, student app) in one pass.

### Option A: Python only (fastest for development)

Needs **Python 3.12, 3.13 or 3.14**; every library installs ready-made on Windows, no
compiler needed. Always install into the project's virtual environment (`.venv`): if
pip says "Defaulting to user installation", the environment is not active.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head                 # creates aegis-dev.db (SQLite)
python -m app.cli demo-data          # demo accounts + Python, SQL and ML lab exams
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs for the interactive API. Demo logins are printed by
`demo-data` and listed under [Demo accounts](#demo-accounts).

Without Judge0, code runs through the **local dev runner**: Python only, and not a
sandbox. That is fine on your own laptop and refused in production.

**ML / data-science labs** (`python3-ml`: NumPy, pandas, matplotlib, seaborn, scikit-learn)
need those libraries in a separate virtualenv, which the local runner finds by itself:

```bash
python -m venv .venv-ml
.venv-ml/bin/pip install -r requirements-ml.txt     # Windows: .venv-ml\Scripts\pip install -r requirements-ml.txt
python -m app.cli bml-lab                           # optional: the BML lab, one validated question per practical
```

`bml-lab` talks to the running server, so start `uvicorn` first.

### Demo accounts

Created by `python -m app.cli demo-data` (development only: never on the college server).
**Every demo account uses the password `Aegis-demo-2026!`** (capital A, ends with `!`).

| Role | Email | Password | Use it for |
|---|---|---|---|
| Admin | `admin.demo@aegis.local` | `Aegis-demo-2026!` | Portal admin area: users, CSV import, departments, batches, languages, app releases, audit |
| Professor | `prof.demo@aegis.local` | `Aegis-demo-2026!` | Portal: subjects, questions, exams, live monitor, grading, results (owns all demo subjects) |
| Student 1 | `student1.demo@aegis.local` | `Aegis-demo-2026!` | Desktop app (college ID `UAI-DEMO-001`, batch `CSE-AIML-2025-A`) |
| Student 2 | `student2.demo@aegis.local` | `Aegis-demo-2026!` | Desktop app (`UAI-DEMO-002`) |
| Student 3 | `student3.demo@aegis.local` | `Aegis-demo-2026!` | Desktop app (`UAI-DEMO-003`) |

Admins and professors sign in to the **portal**; students sign in to the **desktop app**
(the portal refuses student accounts). The admin area only appears for the admin
account. If a demo login says *Wrong email or password*, check the account is still
active (Admin → Users) and use the portal's **👁 Show** button to check the password.

### Run the professor portal

Needs Node.js 20+. The backend must be running (above).

```bash
cd portal
npm install
npm run dev                          # http://localhost:5173
```

`portal/.env.development` points it at `VITE_API_URL=http://localhost:8000`. Start the
backend with `AEGIS_CORS_ORIGINS=http://localhost:5173` (PowerShell:
`$env:AEGIS_CORS_ORIGINS = "http://localhost:5173"`) so the browser may call it. After
`api/openapi.json` changes, regenerate the typed client with `npm run gen:api`, then
`npm run build` shows every screen that needs updating.

### Option B: Docker (PostgreSQL + Redis, like the server)

```bash
cp .env.example .env
docker compose up --build                     # API on :8000
docker compose exec api python -m app.cli demo-data
docker compose --profile judge up --build     # add the Judge0 sandbox (read judge/README.md first)
```

### Tests

```bash
cd backend
pytest -q                                     # SQLite, about 40 s
# Same suite on PostgreSQL and Redis (what CI runs):
AEGIS_TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/aegis_test \
AEGIS_TEST_REDIS_URL=redis://localhost:6379/0 pytest -q
```

The suite covers the full exam flow, the lock policy, device signing, replay
protection, data isolation between students and between professors, encryption at
rest, the audit chain, CSV import, the Judge0 client and the database migrations.

### Check that everything works

With the server running (`uvicorn app.main:app --reload`), open a **second** terminal in
`backend/`, activate the environment and run:

```bash
python -m app.cli simulate            # all four labs; or --lab python | sql | ml | function
```

It plays a whole exam against the real server, like the apps will: the professor opens
a fresh exam and its live monitor (which must show each change within a moment), one
student answers correctly, one is locked for switching windows and
resumed by the professor, one is caught running remote-desktop software and has their
exam ended. Then it closes the exam, waits for grading, releases results and verifies
the audit log. Every step prints `[OK]` or `[FAIL]`, ending with `ALL CHECKS PASSED`.
It creates a new exam each time, so you can run it as often as you like (it needs
`demo-data` first).

For the code itself, `pytest -q` runs the automated test suite (about 40 seconds).

### Updating an existing setup

After pulling new code, always run:

```bash
pip install -r requirements-dev.txt   # new or updated libraries
alembic upgrade head          # apply new database changes
python -m app.cli seed        # add new default languages (e.g. sql, python3-ml, html)
python -m app.cli demo-data   # add any new demo subjects (existing ones are kept)
.venv-ml/bin/pip install -r requirements-ml.txt   # ML runtime (Windows: .venv-ml\Scripts\pip ...)
cd ../portal && npm install   # portal libraries
```

Then **restart `uvicorn`** (without `--reload` it keeps running the old code, e.g. new
question types are missing from the editor).

## Question types

- **Programs (stdio)**: any language; input on stdin, output compared (exact, line,
  whitespace, case-insensitive, float tolerance or any order).
- **SQL**: SQLite. Either a SELECT whose result set is compared, or statements
  (INSERT/UPDATE/...) checked by a professor's query. Each test has its own data.
- **ML**: the student writes `solve(train_csv, test_csv)` in Python; predictions are
  scored on the server against hidden labels (accuracy, F1, precision, recall, R²,
  RMSE, MAE) with partial marks by threshold. Datasets are uploaded as CSVs.
- **Function (LeetCode style)**: the student writes a function (any name) in Python
  or JavaScript and returns the answer. Each test is a JSON list of arguments; the
  returned value is compared (exactly, with a float tolerance, or as a list in any
  order). The editor starts empty for every question type.
- **HTML**: the student writes a web page. It is parsed (never rendered) and each test
  is a list of checks, e.g. `exists form#signup`, `count ul > li = 3`,
  `text h1 contains welcome`, `attr img alt`. Partial credit per check.
- **Plot**: the student draws a chart with matplotlib or seaborn (`python3-ml`). It is
  drawn off-screen and checked, e.g. `chart bar`, `title contains sales`,
  `xlabel = Region`, `legend`, `count bars >= 4`. The question can attach a `data.csv`.

Any language the college enables can be used for program (stdio) questions: Admin →
Languages → **Add engineering language pack** adds 28 (C, C++, Java, Python, Python ML,
JavaScript, TypeScript, Go, Rust, C#, Kotlin, PHP, Ruby, Bash, Assembly, R, Octave,
Scala, Prolog, Lisp, Haskell, Swift, Perl, Lua, Pascal, Fortran, SQL, HTML). On a laptop
only Python runs (local runner); everything runs on Judge0.

A lab exam can be **locked to one language** (e.g. Python only). Students then see only
that language, and the server rejects anything else.

## How the pieces fit

- **Student app → API**: every exam request is signed with an Ed25519 key that lives
  only on that laptop (docs/CLIENT_PROTOCOL.md).
- **Portal → API**: normal bearer-token requests (docs/PORTAL_GUIDE.md), plus a
  WebSocket that pushes live monitor changes the moment they happen.
- **API → Judge0**: code runs in isolate sandboxes; the API compares outputs itself.
- **API → PostgreSQL**: all data, with student code, hidden tests and reference
  solutions encrypted (AES-256-GCM).
- **API → Redis**: rate limits, replay protection, and passing live monitor updates
  between API workers.

The two apps never talk to each other, only to the API. `api/openapi.json` is the
contract: generate the TypeScript client from it, and regenerate it whenever the backend
changes (`python -m app.cli export-openapi`; CI fails if it is out of date).

## Designed to grow without code changes

- Departments, labs, subjects, batches, professors and students are rows, added from
  the admin API or a CSV upload.
- Programming languages are rows (`/api/v1/admin/languages`); the editor mode and
  time multiplier come from the same row, so the apps need no update.
- Question types are grader plug-ins (`backend/app/grading/`). A new type is a new
  module; nothing else changes.
- Who sees what is data: per-subject or per-exam grants (owner, editor, grader, viewer).
- Exam rules (strikes, what locks, run limits, whether hidden results are shown,
  marking split) are per-exam settings, not code.
- Schema changes go through Alembic migrations; the API is versioned (`/api/v1`).

## What happens when a student breaks the rules

Minor events (focus lost, full screen exited) are strikes with a warning. At the strike
limit, or on any serious event (VM, remote desktop, screen capture, second monitor,
another laptop), the session **locks**. The server then refuses every save, run and
submit until a professor resumes it (optionally with extra time) or ends it. The timer
keeps running while locked, and if nobody acts, the frozen code is auto-submitted and
flagged at the deadline. Details in docs/SECURITY.md.

## Not built yet

- College SSO (Google or Microsoft): waiting on IT. The dev login uses the same
  session system, so SSO only adds a sign-in route.
- Input generators (random large tests from a small script).
- Large ML datasets (file storage with MinIO); datasets are limited to 2 MB per split for now.
- Excel export of marks (the portal's Results page has a disabled placeholder button).
- Code-similarity checks (planned as flags only).
- Hosting: the stack is containerised and ready for the college server.

## What's new for the team

Latest Dev 3 work (portal guide steps 9–12 and new question types). Everything below is
covered by tests: `pytest -q` passes in full (100 passed), and each portal screen was
checked in a real browser against the running backend.

### Portal (Dev 3): what exists now

- **Exams tab**: create / edit exams in sections (basics, batches, questions,
  selection rules, policy, marking, languages), readiness check with links to fixes,
  open with a full-screen access code (copy, rotate), stop exam, edit tests and
  re-grade, and **Create new exam from this** for closed exams (a closed exam is never
  reopened; the copy is a fresh draft).
- **Live monitor** (`/exams/:id/monitor`): WebSocket updates, locked students first in
  red with a sound, counters, batch filter, lock / resume / terminate, close exam;
  falls back to polling if the socket cannot connect.
- **Session, results and grading** (`/exams/:id/sessions/:sid`, `/results`, `/answers`):
  timeline, highlighted code, per-test results, run history, manual marks or override
  with a note, re-grade (manual marks kept), release results, answer-by-answer checking
  with ← / → keys.
- **Admin area** (`/admin/...`, admin account only): users, CSV import (dry run first),
  departments, batches and members, languages (+ engineering pack), app releases,
  audit log with integrity check.
- **Question editor**: every step clickable, live language list per question type,
  HTML and plot editors. **Password fields** have a 👁 Show / Hide button.

### Dev 1 (backend): please review

- **New graders**: `grading/html.py`, `grading/plot.py`, and `grading/checklist.py`
  (shared verdict logic for check-list graders). A new `Grader.needs_python` flag
  replaces the hard-coded `grader == "sql"` in `services/exams.build_target`; SQL and
  HTML set it. The stdio grader now refuses `html` answers.
- **ML runtime**: the local runner runs `python3-ml` with `backend/.venv-ml` (or
  `AEGIS_LOCAL_ML_PYTHON`), sets `MPLBACKEND=Agg`, and now normalises Windows `\r\n` to
  `\n` (expected outputs generated on Windows used to contain `\r`). The ML and plot
  harnesses limit BLAS to one thread (Judge0 allows 60 processes/threads).
- **Judge0 for ML**: `judge/Dockerfile.ml` adds NumPy, pandas, matplotlib, seaborn,
  scikit-learn and SciPy to Judge0's Python 3.8; `docker-compose.yml` builds it for both
  Judge0 services. **Not yet tested on a Docker host**: please build it and run
  `python -m app.cli bml-lab` there. ML questions use 512000 KB (= `MAX_MEMORY_LIMIT`).
- **BML lab**: `python -m app.cli bml-lab` (`app/bml_lab.py`) builds a validated
  Basics of Machine Learning subject through the API: one question per practical
  (stdio, plot, ML, function), synthetic seeded datasets, and a draft exam.
- `seed.py` adds the `html` language; `subjects.py` keeps `require_staff` (admins may
  read subject metadata, never questions); `api/openapi.json` was regenerated.
- **Small API gaps the portal works around** (worth fixing in the API):
  `GET /exams/{id}/sessions/{sid}` returns `row.batch_code = null` (the portal reads it
  from `/monitor`); `GET /admin/audit` has no filters (the portal filters loaded
  entries); `GET /admin/languages` does not return `judge0_id`.

### Dev 2 (desktop app): please review

- **New question types students will see**:
  - `html`: answers are web pages in the `html` language (Monaco mode `html`). Run and
    Submit return a check list in `stdout` (`✓ exists form`, `✗ doctype`) and a summary
    in `message`.
  - `plot`: answers are matplotlib/seaborn code in `python3-ml`; the program reads
    `data.csv` and draws charts (nothing is shown; the server checks the figure). Same
    ✓ / ✗ feedback.
  - For both, **sample** tests name the failed checks; **hidden** tests only say e.g.
    `3 of 5 checks passed`.
- **More languages**: up to 28 can be enabled. Always take the editor mode from each
  language's `monaco_id` (some are `plaintext`, e.g. Assembly, Prolog, Haskell, Octave).
- ML questions have **no starter code**; the statement explains
  `solve(train_csv, test_csv)`.
- **Build note**: if `npm run build` in `desktop/` fails with *Cannot find module
  '@tailwindcss/postcss'*, a `postcss.config.*` in a folder **above** the repo is being
  picked up by Vite. Move that file; the desktop app itself builds fine.

## Useful commands

```bash
python -m app.cli gen-key          # new key for AEGIS_DATA_KEYS / AEGIS_AUDIT_KEY
python -m app.cli create-admin --email you@college.edu
python -m app.cli verify-audit     # check nobody edited the audit log
python -m app.cli export-openapi   # refresh api/openapi.json
python -m app.cli check-judge0     # compare language ids with the Judge0 server
python -m app.cli simulate         # play full exams against the running server (ALL CHECKS PASSED)
python -m app.cli bml-lab          # create the validated Basics of ML lab subject
alembic revision --autogenerate -m "describe change"   # after changing models
```
