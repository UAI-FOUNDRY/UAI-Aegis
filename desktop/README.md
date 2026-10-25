# Student desktop app (Dev 2)

Tauri (Rust + React) app for Windows, macOS and Linux, with the Monaco editor and the
exam lockdown.

Start here:
- `docs/CLIENT_PROTOCOL.md`: sign-in, device registration, request signing, the exam
  flow, events to report, and every error code to handle.
- `backend/app/security/signing.py`: reference signer (match it byte for byte).
- `api/openapi.json`: request and response shapes.

Build order: Windows lockdown first, then macOS, then Linux.
