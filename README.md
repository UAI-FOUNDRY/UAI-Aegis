# UAI Aegis

Proctored practical exams for Universal AI University. Students code in a locked-down
desktop app on their own laptops; hidden test cases grade the work on the college
server; professors run everything from a web portal.

This repository holds the whole project. Right now the **backend is built and tested**;
the desktop app and portal folders hold the specs their owners build against.

```
uai-aegis/
  backend/      FastAPI API, database, security, grading, exam engine   (Dev 1)
  desktop/      Tauri student app                                        (Dev 2)
  portal/       React professor portal                                   (Dev 3)
  shared/       UI components + API client generated from the contract  (Dev 3)
  api/          openapi.json: the API contract all three apps share
  judge/        Judge0 sandbox config
  docs/         SECURITY.md, CLIENT_PROTOCOL.md (Dev 2), PORTAL_GUIDE.md (Dev 3)
```

## Run the backend

### Option A: Python only (fastest for development)

Needs **Python 3.12 or 3.13** (not 3.14 yet: two libraries have no ready-made Windows
installs for it, and pip would try to compile them). On Windows with several Pythons
installed, create the environment with `py -3.13 -m venv .venv`.

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
`demo-data` (all use the password `Aegis-demo-2026!`).

Without Judge0, code runs through the **local dev runner**: Python only, and not a
sandbox. That is fine on your own laptop and refused in production.

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
python -m app.cli seed        # add new default languages (e.g. sql, python3-ml)
python -m app.cli demo-data   # add any new demo subjects (existing ones are kept)
```

## Question types

- **Programs (stdio)**: any language; input on stdin, output compared (exact, line,
  whitespace, case-insensitive, float tolerance or any order).
- **SQL**: SQLite. Either a SELECT whose result set is compared, or statements
  (INSERT/UPDATE/...) checked by a professor's query. Each test has its own data.
- **ML**: the student writes `solve(train_csv, test_csv)` in Python; predictions are
  scored on the server against hidden labels (accuracy, F1, precision, recall, R²,
  RMSE, MAE) with partial marks by threshold. Datasets are uploaded as CSVs.
- **Function (LeetCode style)**: the student writes only a function, e.g.
  `def two_sum(nums, target)`, in Python or JavaScript, and returns the answer. Each
  test is a JSON list of arguments; the returned value is compared (exactly, with a
  float tolerance, or as a list in any order).

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
- Excel export of marks.
- Code-similarity checks (planned as flags only).
- Hosting: the stack is containerised and ready for the college server.

## Useful commands

```bash
python -m app.cli gen-key          # new key for AEGIS_DATA_KEYS / AEGIS_AUDIT_KEY
python -m app.cli create-admin --email you@college.edu
python -m app.cli verify-audit     # check nobody edited the audit log
python -m app.cli export-openapi   # refresh api/openapi.json
python -m app.cli check-judge0     # compare language ids with the Judge0 server
alembic revision --autogenerate -m "describe change"   # after changing models
```
