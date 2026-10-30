# Student desktop app (Dev 2)

Tauri 2 (Rust + React) app for Windows, macOS and Linux.

Specs: `docs/CLIENT_PROTOCOL.md` (the protocol), `backend/app/security/signing.py`
(reference signer, matched byte for byte), `api/openapi.json` (request/response shapes).

## What works now

- Sign in (+ forced password change), laptop registration with an Ed25519 key kept in the
  OS keychain, exam list, start/resume with the access code.
- Exam room: Markdown statements (raw HTML off + DOMPurify, links stripped), samples,
  Monaco editor (bundled, no CDN) with the server's language list and starter code.
- Autosave every 5 s with `base_revision`; an AES-256-GCM encrypted local copy is written
  before every save/run/submit, so a Wi-Fi drop or crash loses nothing (resent on resume).
- Run samples / Submit with per-test results; Finish (saves everything first) with receipts.
- Server countdown, heartbeat every 10 s (never two at once), locked / waiting screen,
  submitted screen.
- Events: `focus_lost` (window unfocused for 2 s, detected in Rust from the OS window),
  `app_restarted` (resume more than 20 s after start), `paste_blocked` (text copied
  outside the editor is refused; copy/paste inside the editor works).
- All HTTP and signing in Rust; the web view can call only the commands in
  `src-tauri/build.rs` + `capabilities/default.json`. Strict CSP in `tauri.conf.json`.

Next: Windows lockdown (kiosk full screen, keyboard hook, capture exclusion, display/VM/
remote-desktop detection), then macOS and Linux.

## Folder map

```
desktop/
  src/
    App.tsx            sign in, password change, exam list
    ExamRoom.tsx       questions, editor, autosave, run/submit/finish, heartbeat, lock screen
    CodeEditor.tsx     Monaco + paste guard
    markdown.ts        safe Markdown (markdown-it, html off, DOMPurify)
    api.ts             typed wrappers for the Rust commands
  src-tauri/
    src/signing.rs     canonical message + Ed25519 signing (tests vs the Python vectors)
    src/keystore.rs    device key + draft key in Windows Credential Manager / macOS Keychain / Secret Service
    src/drafts.rs      encrypted local copy of answers (tests: round trip, tamper, wrong session)
    src/client.rs      HTTP client: serialise once, sign, send the same bytes; error parsing
    src/commands.rs    the commands the UI may call + focus-loss reporting
    tests/live_backend.rs   full signed flow against a running backend
    build.rs, capabilities/default.json   command allow-list
    tauri.conf.json    window, CSP, bundle
```

## One-time setup (Windows)

1. Rust: https://rustup.rs (default options).
2. Visual Studio Build Tools with **Desktop development with C++**.
3. Node.js LTS.
4. WebView2 is already part of Windows 10/11.

## Run it

Terminal 1, the backend (see the root README):

```
cd backend
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

Terminal 2, the app:

```
cd desktop
npm install          # first time only
npm run tauri dev    # first build takes a few minutes
```

Open the exam as the professor (Swagger at http://localhost:8000/docs, `POST /api/v1/exams/{id}/open`;
for testing, `PATCH /api/v1/exams/{id}` with `{"join_window_minutes": 0}` removes the 15-minute start limit),
then sign in to the app as `student1.demo@aegis.local` / `Aegis-demo-2026!` and type the code.

The server address defaults to `http://localhost:8000`. For another server, set it when building:
`$env:AEGIS_API_URL="https://exams.example.edu"; npm run tauri build`.

## Tests

```
cd desktop/src-tauri
cargo test --lib                                   # signing matches the Python reference
$env:AEGIS_TEST_EXAM_ID="<exam id>"; cargo test --test live_backend -- --ignored --nocapture
```

The live test plays the professor (opens the exam or rotates its code), then signs in as
`student2.demo` (`AEGIS_TEST_STUDENT` to change), registers a throwaway key, starts, sends a
heartbeat, reads the session and checks that a wrong key gets `401 bad_signature`.

## Adding a command

1. Write it in `src-tauri/src/commands.rs` and add it to `generate_handler!` in `lib.rs`.
2. Add its name to `build.rs` and `allow-<name-with-dashes>` to `capabilities/default.json`.
3. Add a typed wrapper in `src/api.ts`.
