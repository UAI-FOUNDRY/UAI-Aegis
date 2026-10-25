# UAI Aegis security design

## The honest starting point

Students take exams on **their own laptops**, where they have full admin rights. No
software running on such a machine is truly uncrackable. So Aegis treats the student
app as the first wall, not the last one:

1. **The app** blocks and detects (full screen, Alt+Tab, outside paste, VMs, remote
   desktop, screen capture, second monitors).
2. **The server** enforces everything that decides marks. Even a cracked app cannot
   read another student's work, fake a test result, write after time is up, or keep
   working while locked.
3. **People** stay in the loop: the professor in the room sees the live monitor and
   the full event timeline, and decides what happens after a lock.

## What the server enforces

- **Server-side timer.** The deadline lives on the server. Every write checks it, and a
  background sweeper auto-submits sessions when time is up.
- **Hidden tests never leave the server.** Students only ever receive sample tests.
  Code always runs on the server (Judge0), never on the laptop, so a "passed" result
  cannot be faked.
- **Device binding.** Each laptop registers an Ed25519 public key; the private key stays
  in the OS keychain. Every exam request is signed (method, path, timestamp, nonce, body
  hash). A copied login token is useless on another machine.
- **Replay protection.** Each nonce is accepted once; timestamps older than 2 minutes
  are rejected.
- **One session per student per exam, one laptop per session.** Enforced by a unique
  database constraint. Opening the exam on another laptop locks the session until a
  professor approves the move.
- **Lock state.** While locked, saves, runs, submits and finishing are refused (HTTP 423)
  no matter what the app sends.
- **Rate limits.** Sign-in attempts (per email and per IP), wrong exam codes, device
  registration, autosaves, and Run/Submit per question.
- **Approved app builds.** With `AEGIS_REQUIRE_KNOWN_CLIENT=true`, exams start only from
  app versions and build hashes an admin has registered. This stops old and naively
  modified builds; a determined attacker can fake the hash, which is why everything
  above is enforced server-side anyway.

## Violation policy (lock, not auto-submit)

- **Info** events (blocked paste, blocked shortcut, Wi-Fi reconnect, heartbeat gap) are
  recorded only.
- **Minor** events (focus lost, full screen exited, app restarted) add a strike and a
  warning. At `max_strikes` (default 3) the session locks.
- **Severe** events (VM, remote desktop, screen capture, blocked app, debugger, second
  monitor, failed integrity check, another laptop) lock at once.
- A locked student sees "waiting for your professor". Their code is frozen and safe.
- The professor can **resume** (optionally adding minutes after a false alarm, and
  approving a laptop switch) or **end** the exam (the frozen code is submitted).
- **The timer keeps running while locked**, so a lock never buys extra time.
- If no professor acts before the deadline, the frozen code is auto-submitted and the
  session is flagged `locked_at_end` for review.

Why not auto-submit on the first violation: it cannot be undone, and false alarms
(antivirus popups, OS update notices, a bumped touchpad) would end honest students'
exams. A lock stops cheating just as fast and keeps the decision with a human.

Every severity, the strike limit and whether severe events lock are per-exam settings.
Penalties for misconduct are not automatic; the professor decides.

## Data protection

- **Passwords:** Argon2id (RFC 9106 parameters). Unknown emails take the same time as
  wrong passwords, and return the same error, so accounts cannot be enumerated.
- **Login tokens:** 256-bit random, stored only as SHA-256 hashes, revocable instantly.
  Logging out, deactivating a user, changing a role or changing a password revokes
  sessions.
- **Encryption at rest:** student code (drafts, final answers, run history), hidden test
  inputs and outputs, reference solutions and exam access codes are encrypted with
  AES-256-GCM. A database dump or backup does not reveal them.
- **Answers cannot be mixed up.** Each ciphertext is bound to its row through GCM
  associated data (for an answer: its session id and question id). If ciphertexts are
  swapped or copied between rows, decryption fails with an integrity error instead of
  showing one student's code as another's. Unique constraints make it impossible for
  two students to share a session or an answer row.
- **Key rotation:** keys carry ids; add a new key as active and old rows still decrypt.
- **Receipts:** when a student finishes, each answer gets a SHA-256 receipt bound to the
  session and question, as proof of exactly what was submitted.
- **Random IDs:** every ID is a random UUID, so IDs cannot be guessed or enumerated.

## Who can see what

- **Students** see only their own sessions. Anyone else's session returns 404, exactly
  like one that does not exist.
- **Professors** see a subject only through a grant: owner, editor, grader or viewer,
  for the whole subject or for one exam (e.g. a co-evaluator). No grant means 404.
- **Admins** manage accounts, subjects and grants but cannot read questions, tests or
  answers, and grants can only be given to professors.
- A subject always keeps at least one owner.

## Tamper-evident audit log

Sensitive actions (account and role changes, grants, question and test edits, exam
open/close, locks, resumes, terminations, auto-submits, mark changes with before/after
values) are written in the same database transaction as the change. Each entry stores
HMAC-SHA256(audit key, previous hash | entry). Editing, deleting or reordering any past
entry breaks the chain, and without the audit key (kept off the database server) the
chain cannot be rebuilt to hide it. Check it any time with
`python -m app.cli verify-audit` or `GET /api/v1/admin/audit/verify`.

## Hardening

- Security headers on every response; API responses are `Cache-Control: no-store`.
- Request size cap; code and test size limits.
- CORS limited to the portal's origin; no cookies, so no CSRF surface.
- Interactive docs and the OpenAPI file are disabled in production.
- Production refuses to start with development keys, SQLite, the local code runner,
  no Redis, or unregistered app builds allowed.
- Judge0 is not exposed on any host port and runs with network access disabled.

## Operational checklist for the server

- Generate `AEGIS_DATA_KEYS` and `AEGIS_AUDIT_KEY` with `python -m app.cli gen-key`. Store
  them outside the repository and the database server, and back them up: losing the
  data key means losing every encrypted answer.
- Put the API behind HTTPS (nginx or Caddy) and enable HSTS (automatic in production).
  Set the proxy's request body limit (e.g. nginx `client_max_body_size 8m`); the API also
  checks `Content-Length`, but only the proxy can cap chunked uploads.
- Set `AEGIS_ALLOWED_EMAIL_DOMAINS` to the college domain.
- Register each released desktop build (`/api/v1/admin/client-releases`) and set
  `AEGIS_REQUIRE_KNOWN_CLIENT=true`.
- Run `verify-audit` on a schedule and after every exam.
- Back up PostgreSQL nightly; backups contain only encrypted code and tests.
