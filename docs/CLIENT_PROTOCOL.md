# Student app ↔ API protocol (for the desktop app, Dev 2)

Everything here is checked by the backend tests; `backend/app/security/signing.py`
(`DeviceSigner`) is the reference implementation of the signing. When in doubt, make
your requests byte-for-byte identical to what it produces.

## 1. Sign in

```
POST /api/v1/auth/login
{"email": "...", "password": "...", "client": "desktop"}
→ {"token": "aegis_...", "expires_at": "...", "user": {"id": "...", "role": "student", ...}}
```

Send `Authorization: Bearer <token>` on every later request. Keep the token in memory
or the OS keychain, never in a plain file. On `401 session_expired`, sign in again.
On `403 password_change_required`, show the change-password screen
(`POST /api/v1/auth/change-password`).

College SSO will replace the password form later; the token you get back is the same.

## 2. Register the laptop (once per install)

1. Generate an **Ed25519** key pair. Store the private key in the OS keychain
   (Windows Credential Manager, macOS Keychain, Linux Secret Service), never on disk in
   plain form.
2. `public_key` = the raw 32-byte public key, **base64url without padding**.
3. `proof` = signature (base64url) of this exact UTF-8 text, using the user id from the
   login response:

   ```
   AEGIS-REGISTER-V1\n<user id>\n<public_key>
   ```

```
POST /api/v1/student/devices          (bearer token only, not signed)
{"public_key": "...", "proof": "...", "label": "Asha's HP laptop", "platform": "windows"}
→ 201 {"device_id": "..."}
```

`platform` is `windows`, `macos` or `linux`. Registering the same key again returns the
same device id. A student can have up to 5 laptops.

## 3. Sign every exam request

Every route under `/api/v1/student/exams/{id}/start` and `/api/v1/student/sessions/...`
needs four extra headers:

```
X-Aegis-Device:     <device_id>
X-Aegis-Timestamp:  <unix seconds, e.g. 1798000000>
X-Aegis-Nonce:      <16+ random bytes, base64url; never reuse>
X-Aegis-Signature:  <base64url Ed25519 signature of the canonical message>
```

Canonical message (UTF-8, joined with `\n`, no trailing newline):

```
AEGIS-SIG-V1
<METHOD upper case>
<path + "?" + query if there is one, exactly as requested, e.g. /api/v1/student/sessions/<id>/heartbeat>
<timestamp, same string as the header>
<nonce, same string as the header>
<lower-case hex SHA-256 of the raw body bytes; for an empty body, SHA-256 of nothing>
```

Rules that trip people up:

- Serialise the JSON body **once**, sign those bytes, and send those same bytes.
  Re-serialising (different spacing or key order) changes the hash.
- Use a fresh nonce for every request, including retries.
- The laptop clock must be within 2 minutes of the server (`401 stale_signature`
  otherwise; tell the student to fix their date and time).

Rust sketch (crates: `ed25519-dalek` 2 with `rand_core`, `rand`, `sha2`, `hex`,
`base64`, and `keyring` for storage):

```rust
use base64::{engine::general_purpose::URL_SAFE_NO_PAD as B64, Engine};
use ed25519_dalek::{Signer, SigningKey};
use sha2::{Digest, Sha256};

fn canonical(method: &str, path: &str, ts: &str, nonce: &str, body: &[u8]) -> Vec<u8> {
    let body_hash = hex::encode(Sha256::digest(body));
    format!("AEGIS-SIG-V1\n{}\n{}\n{}\n{}\n{}", method.to_uppercase(), path, ts, nonce, body_hash).into_bytes()
}

// First launch: let key = SigningKey::generate(&mut rand::rngs::OsRng);
// store key.to_bytes() in the keychain; public key = B64.encode(key.verifying_key().to_bytes())
fn sign(key: &SigningKey, method: &str, path: &str, ts: &str, nonce: &str, body: &[u8]) -> String {
    B64.encode(key.sign(&canonical(method, path, ts, nonce, body)).to_bytes())
}
```

Do the signing in Rust (a Tauri command), so the private key never enters the web view.

## 4. Exam flow

```
GET  /api/v1/student/exams                                   bearer only: list, status, session if any
POST /api/v1/student/exams/{exam_id}/start                   signed: {"access_code", "client_version", "client_build_sha256"}
GET  /api/v1/student/sessions/{sid}                          signed: full view (questions, samples, drafts, time)
PUT  /api/v1/student/sessions/{sid}/answers/{qid}            signed: autosave {"language", "code", "base_revision"}
POST /api/v1/student/sessions/{sid}/answers/{qid}/run        signed: sample tests, with outputs
POST /api/v1/student/sessions/{sid}/answers/{qid}/submit     signed: all tests (hidden results per exam policy)
POST /api/v1/student/sessions/{sid}/heartbeat                signed: every 10 s
POST /api/v1/student/sessions/{sid}/events                   signed: up to 20 events per call
POST /api/v1/student/sessions/{sid}/finish                   signed: hand in, get receipts
GET  /api/v1/student/results                                 bearer only: released marks
```

- **Start** needs the access code the professor shows in the lab. Calling start again
  after a crash on the same laptop simply resumes (no code needed, clock unchanged).
- **Show the server's countdown**: use `deadline_at` and `server_now` from the view or
  heartbeat (compute the offset once), never the laptop clock alone.
- **Autosave** every ~5 s when the code changed. Send the last `revision` you got as
  `base_revision`; `409 stale_revision` means reload the draft from `GET session`.
  Also keep a local encrypted copy so a Wi-Fi drop loses nothing; resend when back.
- **Run** also saves. Final marks always use the latest saved code when the exam ends.
- **Monaco**: each question lists `languages` with `monaco_id` and `starter_code` per
  language key; use them directly, no hard-coded language list in the app. When the
  list has one language (a locked exam, e.g. Python only), show no language picker.
- **SQL questions** use the `sql` language (Monaco `sql`). Sample inputs are the test's
  data (INSERT statements); outputs are JSON `{"columns": [...], "rows": [[...]]}`; show
  them as small tables.
- **Function questions** (LeetCode style): `starter_code` already contains the empty
  function (e.g. `def two_sum(nums, target):`). A sample's `input` is the arguments as
  a JSON list (`[[2, 7, 11, 15], 9]`) and `expected_output` is the returned value as
  JSON (`[0, 1]`); label them "Arguments" and "Returns". In run results, `stdout` is the
  value the function returned and `stderr` holds anything the student printed.
- **ML questions** use `python3-ml`. A sample's `input` is JSON `{"train": csv, "test":
  csv}` and its `expected_output` is the list of labels: offer them as downloadable
  CSV files rather than printing them. Results carry a `message` with the score.
- **Finish** returns a receipt (SHA-256) per answer. Show them to the student.

## 5. Report events

```
POST .../events
{"events": [{"kind": "focus_lost", "client_ts": "2026-11-02T10:15:04Z", "details": {"seconds": 3}}]}
→ {"status": "active", "strikes": 1, "strikes_left": 2, ...}
```

Send what you **detect** and what you **block**. Default meaning (the professor can
change it per exam):

- Recorded only: `paste_blocked`, `shortcut_blocked`, `network_reconnected`
- Strike: `focus_lost`, `fullscreen_exited`, `app_restarted` (relaunched mid-exam),
  `clipboard_external_paste`
- Lock at once: `display_added`, `vm_detected`, `remote_desktop_detected`,
  `screen_capture_detected`, `blocked_app_detected`, `debugger_detected`,
  `integrity_failed`

Kinds are `lower_snake_case`, max 48 characters; unknown kinds count as a strike.
`details` is free JSON up to 2 KB (e.g. the offending process name). Never put the
student's code or personal data in it.

## 6. Error codes the app must handle

Errors look like `{"detail": {"code": "...", "message": "...", ...}}`. Show `message` to
the student; branch on `code`:

- `423 session_locked`: show the "waiting for your professor" screen. Keep sending
  heartbeats; when one returns `status: active`, reload the session.
- `409 time_over` / `session_finished`: show the submitted screen and receipts.
- `409 device_mismatch`: the exam is bound to another laptop.
- Start returns `status: locked` with `lock_reason: device_changed` and
  `device_change_pending: true` when the student opened the exam on a new laptop: show
  "ask your professor to approve this laptop" and poll heartbeat.
- `401 stale_signature`: laptop clock is wrong.
- `401 replayed_request` / `bad_signature`: a bug in signing (new nonce per request, sign
  exact bytes).
- `401 unknown_device`: register this laptop again.
- `403 client_not_allowed`: this app build is not approved; update.
- `403` "Wrong exam code", `409 join_window_closed`, `409 exam_not_open`.
- `429 rate_limited`: slow down (Run is limited per question per minute).

## 7. Lockdown checklist (app side)

Windows first, then macOS, then Linux:

- Full-screen, always-on-top kiosk window that cannot be minimised or closed during
  the exam.
- Block Alt+Tab, Win key, Cmd+Tab and similar (low-level keyboard hook in Rust).
- Exclude the window from screen capture (Windows `SetWindowDisplayAffinity`, macOS
  window sharing type none).
- Refuse outside clipboard content in the editor (allow copy/paste inside the editor).
- Before start and during the exam: detect extra displays, virtual machines, remote
  desktop / screen sharing / recording tools and blocked apps; report events.
- Send heartbeat every 10 s with `focused`, `fullscreen`, `displays`.
- On relaunch during an exam, report `app_restarted`.
- Sign releases; send `client_version` and the build's SHA-256 on start.

## 8. Keep the web view safe

Question statements are Markdown written by professors, and event details or names can
contain anything. In a Tauri app, script injected into the web view could call your
Rust commands, including the signing command. So:

- Render Markdown with raw HTML disabled, then sanitise the output (e.g. DOMPurify).
- Set a strict Content-Security-Policy in `tauri.conf.json` (no remote scripts, no
  `unsafe-eval`), and allow-list only the Tauri commands the UI needs.
- Never expose a generic "sign these bytes" command to JavaScript; expose
  "make this API request", and build and sign the request in Rust.

## 9. Develop against the real backend

```bash
cd backend && alembic upgrade head && python -m app.cli demo-data && uvicorn app.main:app --reload
```

Sign in as `student1.demo@aegis.local` / `Aegis-demo-2026!`. As the demo professor,
`POST /api/v1/exams/{exam_id}/open` returns the access code. `backend/tests/` shows every
call above in use.
