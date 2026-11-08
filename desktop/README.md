# Student desktop app (Dev 2)

Tauri 2 (Rust + React) app for Windows, macOS and Linux.

Specs: `docs/CLIENT_PROTOCOL.md` (the protocol), `backend/app/security/signing.py`
(reference signer, matched byte for byte), `api/openapi.json` (request/response shapes).

## What works now

- Sign in (+ forced password change), laptop registration with an Ed25519 key kept in the
  OS keychain, exam list, start/resume with the access code.
- Exam room laid out like a coding site: question on the left, a large Monaco editor
  (bundled, no CDN) on the right with Run / Submit and a results panel under it; the divider
  can be dragged and the results panel hidden. Question tabs in the top bar. No language
  picker when the question allows one language. Markdown: raw HTML off + DOMPurify, links stripped.
- Each kind of question shows its samples and results its own way (`src/testdata.tsx`):
  normal programs (input / expected / your output), function questions ("Arguments" /
  "Returns"; your return value, and what your code printed), SQL (the INSERT data and the
  expected / your result as small tables), ML (training and test data as scrollable read-only
  tables, first 300 rows with a row count, expected labels as the last column; your score and
  predictions), HTML and chart questions (the checklist, then a tick or cross per check).
  The server does not send the question kind yet, so it is worked out from the language and
  the samples; a `kind` or `grader` field on the question will be used as soon as it exists.
- Data files (`src/DataFiles.tsx`): a question's datasets are listed under the statement
  with their size and shape and how to open them (`pd.read_csv("marks.csv")`, typed, not
  copied). Clicking one shows the first 50 rows or a column summary (type, missing,
  distinct, min, max, mean or examples), fetched with the `file_preview` command. The
  file itself never comes to the laptop: the code runs on the server, next to it.
- Autosave every 5 s with `base_revision`; an AES-256-GCM encrypted local copy is written
  before every save/run/submit, so a Wi-Fi drop or crash loses nothing (resent on resume).
- Run samples / Submit with per-test results; Finish (saves everything first) with receipts.
- Server countdown, heartbeat every 10 s (never two at once), locked / waiting screen,
  submitted screen. When the professor adds time, the next heartbeat brings the new end
  time and the student sees "Your professor gave you N more minutes".
- Moving to another laptop: on the new laptop the exam opens paused (locked down) until
  the professor approves; then it reloads with the saved code. On the old laptop (or a
  rejected new one), a heartbeat answered with `device_mismatch` ends the lockdown in Rust
  (`released_elsewhere` in `commands.rs`) and the room shows "This exam is on another
  laptop" with a way back to the exam list.
- Leaving the exam (`lockdown/away.rs`, `start_guard` in `lockdown/mod.rs`): the window is
  checked 20 times a second from Rust (not from JavaScript). If it loses the focus or full
  screen for more than 150 ms (shorter is a popup flicker), it is pulled straight back and
  the trip is reported once (`focus_lost` or `fullscreen_exited`) with an `excursion` id;
  the server counts one strike per trip, however many detectors saw it. Measured on Linux/X11
  with another window grabbing the focus: back in 160-215 ms, each trip reported once.
  Windows also forces the window to the foreground (`AttachThreadInput`), macOS re-activates
  the app.
- Other events: `app_restarted` (resume more than 20 s after start), `paste_blocked` (text copied
  outside the editor is refused; copy/paste inside the editor works).
- All HTTP and signing in Rust; the web view can call only the commands in
  `src-tauri/build.rs` + `capabilities/default.json`. Strict CSP in `tauri.conf.json`.
  The web view cannot navigate anywhere except the app's own pages (`src-tauri/src/navguard.rs`).

- Lockdown while a session is on screen (`src-tauri/src/lockdown/`): full screen, always on
  top, cannot be closed or minimised, hidden from screen capture; leaving full screen is
  reported (`fullscreen_exited`) and undone. Windows: keyboard hook blocks Alt+Tab, Alt+Esc,
  Alt+F4, Alt+Space, Ctrl+Esc, Ctrl+Shift+Esc, the Win keys (so every Win+ shortcut, including
  Win+Tab and Win+Ctrl+arrows for virtual desktops) and Print Screen (`shortcut_blocked`,
  summarised). The accessibility shortcuts (Shift five times, holding right Shift or Num Lock)
  are switched off during the exam, because their pop-ups link to Settings; the student's own
  settings are kept and restored.
- Detection before Start (the student must fix it) and every 3 s during the exam, each
  reported once: extra displays, VMs (CPUID + firmware strings), Remote Desktop session,
  remote-control / screen-sharing / recording apps, debugger (release builds, Windows),
  and the admins' blocked apps (chat, AI assistants...): the list comes from the server
  with every exam view and before Start (`ServerApp` in `lockdown/detect.rs`), reported as
  `forbidden_app_detected` (a strike) or `blocked_app_detected` (pauses the exam).

- Windows: the whole app runs on its own Win32 desktop (`lockdown/secure_desktop.rs`, like Safe
  Exam Browser): no taskbar, Start menu, Alt+Tab thumbnails, gestures or other apps. The
  launcher process switches the screen there and back; the app on that desktop switches the
  screen straight back (checked 10 times a second) if the student leaves it (e.g.
  Ctrl+Alt+Del then Task Manager) and reports `focus_lost`. Touchpad and touchscreen gestures
  do nothing on this desktop (they belong to Explorer, which is not there). On the fallback
  normal desktop the window is on every virtual desktop, so a swipe still lands on the exam. If the desktop cannot be set up, it falls back to the normal desktop.
  The "Quit" button (outside exams) returns to normal Windows. While the app runs, the
  per-user policies DisableTaskMgr, DisableLockWorkstation and DisableChangePassword are on
  (Task Manager would otherwise open on the exam desktop); the student's previous values are
  saved and restored on exit, or on the next start after a crash.

- macOS (`lockdown/macos.rs`, `lockdown/mac_keys.rs`): during an exam the app uses kiosk
  presentation options (Dock and menu bar hidden; Cmd+Tab / Mission Control, Force Quit,
  Hide and log out / restart / shut down off) and "simple" full screen (native full-screen
  Spaces are switched off for the window). A Quartz key filter swallows what those leave on:
  Spotlight (Cmd+Space), Ctrl+arrows / Ctrl+1..9 (Spaces), Mission Control / Launchpad /
  Show Desktop keys, Cmd+Q / Cmd+H / Cmd+M, Cmd+Opt+Esc, Cmd+Opt+D, screenshots, fn+Q /
  fn+N / fn+C ... (`shortcut_blocked`, summarised). It needs **Accessibility** permission:
  the pre-exam check shows macOS's prompt and refuses to start until it is given. The app
  menu has no Quit / Hide / Minimise, so Cmd+Q never quits (use the app's Quit button).
  Trackpad: the window is on every Space and stays put in Mission Control, and the key filter
  also swallows the Dock's gesture events (swipe between Spaces, Mission Control, App Exposé,
  Launchpad pinch, Show Desktop spread). If a macOS version delivers them differently, the
  guard still pulls the student back. Hot corners: pulled back the same way.
  Not blockable (Touch ID lock, power button, lid): reported as focus loss, window pulled back.
- Linux (`lockdown/linux.rs`): exams run only in the **"UAI Aegis Exam"** login session that
  the .deb / .rpm installs (`src-tauri/linux/uai-aegis-exam.desktop`): the app alone inside
  `cage`, a kiosk compositor with no panel, no app switching and console switching
  (Ctrl+Alt+F1..F12) off. Before Start, release builds check the session in systemd-logind's
  own record (not an environment variable: running `cage` in a window on a normal desktop
  does not pass) and refuse otherwise, telling the student to log out and pick the session.
  If the session stops being the one on screen (checked 4 times a second), it is switched
  back and `focus_lost` is reported. Quit ends the session (back to the login screen).

Trying the pull-back without an exam (development builds only): `AEGIS_TEST_LOCKDOWN=1`
switches the lockdown on 2 s after start; click another window and watch the log
(`left the exam ... pulling back`, `back on the exam after N ms`). Quit with
Ctrl+Alt+Shift+Q on Windows, or close it from a terminal (`AEGIS_LOCKDOWN=off` is the safer
default while developing).

Development builds only: `$env:AEGIS_SECURE_DESKTOP="off"` keeps the app on the normal desktop;
`$env:AEGIS_LOCKDOWN="off"` before `npm run tauri dev` disables the
lockdown; Ctrl+Alt+Shift+Q turns it off mid-run (Windows); `AEGIS_ALLOW_VM=1` skips the VM
check. Release builds ignore all three. Linux development builds run on the normal desktop
(the exam-session check is for release builds). macOS development builds need the
Accessibility permission too (for the terminal or the dev binary), or `AEGIS_LOCKDOWN=off`.

Trying the Linux exam session: `npm run tauri build -- --bundles deb`, then
`sudo apt install ./src-tauri/target/release/bundle/deb/*.deb` (pulls in `cage`), log out,
pick "UAI Aegis Exam" on the login screen and sign in.

Next: "My results" screen, `network_reconnected`, installer + code signing (Windows
certificate, Apple Developer ID + notarisation), Apple's assessment mode once Apple grants
the entitlement.

## Folder map

```
desktop/
  src/
    App.tsx            sign in, password change, exam list
    ExamRoom.tsx       question | editor layout, autosave, run/submit/finish, heartbeat, lock screen
    testdata.tsx       samples and results per question kind (tables, arguments, checklists)
    CodeEditor.tsx     Monaco (+ its language workers) + paste guard
    markdown.ts        safe Markdown (markdown-it, html off, DOMPurify)
    api.ts             typed wrappers for the Rust commands
  src-tauri/
    src/signing.rs     canonical message + Ed25519 signing (tests vs the Python vectors)
    src/keystore.rs    device key + draft key in Windows Credential Manager / macOS Keychain / Secret Service
    src/drafts.rs      encrypted local copy of answers (tests: round trip, tamper, wrong session)
    src/client.rs      HTTP client: serialise once, sign, send the same bytes; error parsing
    src/navguard.rs    blocks navigation away from the app's own pages
    src/commands.rs    the commands the UI may call + focus-loss reporting
    src/lockdown/      kiosk + guard + detection loop (mod.rs), trips away / one strike each (away.rs), checks (detect.rs), Windows hook/RDP/BIOS (win32.rs), exam desktop (secure_desktop.rs)
                       macos.rs + mac_keys.rs (kiosk options, key filter), linux.rs (exam-session checks)
    linux/uai-aegis-exam.desktop   the Linux "UAI Aegis Exam" login session (installed by the .deb/.rpm)
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
