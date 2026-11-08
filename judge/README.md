# Judge0 (code sandbox)

UAI Aegis never runs student code itself in production. It sends code to
[Judge0](https://github.com/judge0/judge0), which runs every test in a fresh
[isolate](https://github.com/ioi/isolate) box with no network, a CPU-time limit,
a memory limit and a process limit, then throws the box away.

## Start it

```bash
# from the repo root, after editing judge/judge0.conf (passwords + token)
docker compose --profile judge up --build
```

Then point the API at it in `.env`:

```
AEGIS_EXECUTOR=judge0
AEGIS_JUDGE0_URL=http://judge0:2358
AEGIS_JUDGE0_TOKEN=<same value as AUTHN_TOKEN in judge0.conf>
```

Check that the language ids Aegis uses exist on this Judge0 server:

```bash
docker compose exec api python -m app.cli check-judge0
```

Fix any mismatch from the admin API (`PATCH /api/v1/admin/languages/{id}`); languages
are data, not code.

## Machine Learning / Data Science labs (`python3-ml`)

`docker compose` builds Judge0 from `judge/Dockerfile.ml`, which adds NumPy, pandas,
matplotlib, seaborn, scikit-learn and SciPy to Judge0's Python 3.8.1. The `python3-ml`
language uses that Python (language id 71). After starting Judge0, check everything with
the ready-made BML lab (one question per practical, each validated):

```bash
docker compose exec api python -m app.cli bml-lab --url http://localhost:8000
```

Notes:
- ML answers need memory: the BML questions ask for 512000 KB, which is Judge0's
  `MAX_MEMORY_LIMIT` in `judge0.conf`. Raise both if a lab needs more.
- The ML and plot graders limit NumPy/BLAS to one thread, because Judge0 allows at most
  `MAX_PROCESSES_AND_OR_THREADS` (60) per run.
- On a laptop without Judge0, `AEGIS_EXECUTOR=local_dev` runs `python3-ml` answers with
  `backend/.venv-ml` (see the README there).

## Question data files (datasets)

Professors can attach files to a question (`marks.csv`, a training set...); the
student's code opens them by name. On Judge0:

- **Small files** (up to `AEGIS_JUDGE0_INLINE_FILES_MAX_BYTES`, 1 MB by default) travel
  inside each submission (`additional_files`), so they work in every language.
- **Bigger files** (up to 200 MB) are not sent at all: the API keeps them in
  `AEGIS_DATA_FILES_DIR`, the Judge0 workers mount the same folder read-only at
  `/usr/share/aegis-data` (`docker-compose.yml`, volume `data-files`), and a Python
  answer gets a link to the file next to the program. Python only; other languages get
  a clear message if a file is too big for them.
- The folder can be entered but not listed (`rwx--x--x`) and files have random names, so
  a program can only open the files of its own question.

Check both paths after starting Judge0 (part of `check-judge0`):

```bash
docker compose exec api python -m app.cli check-judge0
```

`FAIL big data files` means the sandboxes cannot see the shared folder. isolate binds
`/usr` into every box; older isolate versions do not include mounts below it. Then
either upgrade the Judge0 image, or raise `AEGIS_JUDGE0_INLINE_FILES_MAX_BYTES` so
files travel with each submission (simple, but every run then carries the file and
Judge0's own database grows; clear it after exams with
`docker compose exec judge0-db psql -U judge0 -c 'TRUNCATE submissions'`).

Back up the `data-files` volume together with the database: questions refer to the
files by name.

## Known setup issue

Judge0 1.13 needs **cgroup v1** on the host. Many current Linux distributions (and
Docker Desktop / WSL2) default to cgroup v2, where submissions fail with an internal
error. Judge0's deployment guide describes switching the host to cgroup v1
(`systemd.unified_cgroup_hierarchy=0` on the kernel command line). Do this on the
college server; on a laptop, use the local dev runner instead (`AEGIS_EXECUTOR=local_dev`,
Python only, development only).

## Security notes

- Judge0 is not published on any host port in `docker-compose.yml`; only the API can
  reach it. Keep it that way on the server.
- `ENABLE_NETWORK=false` and `ALLOW_ENABLE_NETWORK=false`: student code cannot reach
  the network even if a request asks for it.
- The containers are `privileged` because isolate needs to create sandboxes. Run
  Judge0 on a host (or VM) that holds nothing else sensitive.

## Language versions (read before the first real exam)

Judge0 1.13.1 ships old compilers and interpreters: Python 3.8.1 (id 71), Node.js
12.14.0 (id 63), OpenJDK 13 (id 62), GCC 9.2 (C id 50, C++ id 54), SQLite 3.27 (id 82).

- Students used to newer Python will hit errors on 3.8 (e.g. `def f(nums: list[int])`
  fails, `match` statements do not exist). Add a newer Python to the Judge0 image (a
  custom image that installs it, plus a row in Judge0's languages table) and point the
  `python3` language row in Aegis at its id: `PATCH /api/v1/admin/languages/{id}`.
- Judge0's Python has no numpy, pandas or scikit-learn, so **ML questions need a
  custom Python** with those libraries installed; point the `python3-ml` language row
  at it (it currently uses id 71 as a placeholder).
- After any change, run `python -m app.cli check-judge0` to compare ids, then run the
  simulator against the server.
