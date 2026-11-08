# Developer setup on Windows (all three parts)

One pass from a fresh clone to everything running and tested: backend, professor portal
and student app. PowerShell, from the repo root (e.g. `C:\Projects\UAI aegis\uai-aegis`).
The root README and `desktop/README.md` have more detail on each part.

## 1. Install once

- Python 3.14, 3.13 or 3.12 (from python.org), with "Add to PATH".
- Node.js 22 LTS.
- Git.
- For the student app: Rust (https://rustup.rs, default options) and Visual Studio Build
  Tools with **Desktop development with C++**. WebView2 is already in Windows 10/11.
- Optional, to test like the server: Docker Desktop (PostgreSQL + Redis + Judge0).

## 2. Backend

```powershell
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1                 # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
                                           # the prompt now starts with (.venv)
python -m pip install -r requirements-dev.txt
Copy-Item ..\.env.example .env             # settings are read from backend\.env
alembic upgrade head                       # creates aegis-dev.db (SQLite)
python -m app.cli seed
python -m app.cli demo-data                # demo accounts + Python, SQL, ML and DSA lab exams
```

`backend\.env` already contains `AEGIS_CORS_ORIGINS=http://localhost:5173`, which the
portal needs. Without it, portal sign-in fails with a CORS error in the browser console.

ML / plot questions need their own environment (the local runner finds it by itself):

```powershell
py -m venv .venv-ml
.venv-ml\Scripts\pip install -r requirements-ml.txt
```

Start the server (keep this terminal open):

```powershell
uvicorn app.main:app --reload
```

http://localhost:8000/docs is the interactive API.

## 3. Professor portal (second terminal)

```powershell
cd portal
npm install
npm run dev                                # http://localhost:5173
```

Sign in as `prof.demo@aegis.local` / `Aegis-demo-2026!` (admin: `admin.demo@aegis.local`).

## 4. Student app (third terminal)

```powershell
cd desktop
npm install
$env:AEGIS_LOCKDOWN="off"                  # while developing: no kiosk, no keyboard hook
npm run tauri dev                          # first build takes a few minutes
```

Open an exam in the portal (Exams → Open), then sign in to the app as
`student1.demo@aegis.local` / `Aegis-demo-2026!` and type the access code.

To try the real lockdown, leave `AEGIS_LOCKDOWN` unset (open a new terminal). The app then
runs on its own exam desktop with shortcuts blocked. In development builds,
**Ctrl+Alt+Shift+Q** switches the lockdown off mid-run, so a bug can never trap you.

To try the pull-back without an exam: `$env:AEGIS_TEST_LOCKDOWN="1"; $env:AEGIS_SECURE_DESKTOP="off"; npm run tauri dev`.
Two seconds after start the window locks; click another window or swipe to another
desktop and watch it come back (the terminal prints `left the exam (window), pulling back`
and `back on the exam after N ms`). Ctrl+Alt+Shift+Q to get out.

## 5. Run the tests

```powershell
# backend\ (venv active): the automated suite, about a minute
cd backend
pytest -q

# a whole simulated exam against the running server (second terminal, venv active)
python -m app.cli simulate                 # ends with ALL CHECKS PASSED

# portal\: type check + production build
cd ..\portal
npm run build

# desktop\src-tauri\: Rust tests (signing, encrypted drafts, key rules, trips away)
cd ..\desktop\src-tauri
cargo test --lib
```

Expected: `pytest` all passed (a few ML tests are skipped if `.venv-ml` is missing),
`simulate` ends with `ALL CHECKS PASSED`, the portal builds, and `cargo test` passes.

## 6. After every `git pull`

```powershell
cd backend
pip install -r requirements-dev.txt
alembic upgrade head                       # new tables/columns (0004 in v0.11: data files, blocked apps)
python -m app.cli seed
python -m app.cli demo-data
cd ..\portal;  npm install
cd ..\desktop; npm install
```

Then restart `uvicorn`.

## Problems we have hit

- **Portal says "Failed to fetch" / CORS error**: `AEGIS_CORS_ORIGINS` missing. Put it in
  `backend\.env` (step 2) and restart `uvicorn`.
- **"Wrong email or password" for a demo account**: the password is `Aegis-demo-2026!`
  (capital A, ends with `!`); run `python -m app.cli demo-data` again if the database is new.
- **New question types missing**: `uvicorn` was started without `--reload` and still runs
  old code; restart it.
- **pip says "Defaulting to user installation" or "Microsoft Visual C++ 14.0 or greater is
  required"**: the virtual environment is not active (the prompt does not start with
  `(.venv)`), or the requirements are older than v0.10.3. Run `.venv\Scripts\Activate.ps1`
  and install again. Anything that already went into your user folder does no harm to the
  project; leave it (other projects of yours may use it).
- **`.ps1` cannot be loaded**: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- **ML question says numpy is missing**: create `.venv-ml` (step 2).
