//! The only functions the web view can call (allow-listed in build.rs and
//! capabilities/default.json). Each one builds and signs its request here in Rust;
//! there is deliberately no generic "sign these bytes" or "send this event" command
//! (CLIENT_PROTOCOL.md section 8).

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex as StdMutex, OnceLock};
use std::time::Duration;

use chrono::{DateTime, SecondsFormat, Utc};
use reqwest::Method;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Manager, State, WebviewWindow};
use tokio::sync::Mutex;

use crate::client::{ApiClient, ApiError, User};
use crate::drafts::DraftStore;
use crate::keystore;

/// Server address. Override at build time: AEGIS_API_URL=https://exams.college.edu npm run tauri build
pub const API_URL: &str = match option_env!("AEGIS_API_URL") {
    Some(url) => url,
    None => "http://localhost:8000",
};
pub const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Focus must stay lost this long before it counts (a notification flicker should not).
const FOCUS_GRACE_MS: u64 = 1500;
/// Same limit as the server (64 KB).
const MAX_CODE_BYTES: usize = 64 * 1024;

pub struct AppState {
    client: Mutex<ApiClient>,
    drafts: StdMutex<Option<DraftStore>>,
    /// Session currently on screen (active or locked); focus loss is reported for it.
    active_session: StdMutex<Option<String>>,
    /// Bumped on every focus change; a pending focus_lost report checks it is still current.
    focus_gen: AtomicU64,
    /// Set once at startup (lib.rs) so session tracking can switch the lockdown on and off.
    app: OnceLock<AppHandle>,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            client: Mutex::new(ApiClient::new(API_URL)),
            drafts: StdMutex::new(None),
            active_session: StdMutex::new(None),
            focus_gen: AtomicU64::new(0),
            app: OnceLock::new(),
        }
    }

    pub fn set_app(&self, app: AppHandle) {
        let _ = self.app.set(app);
    }

    /// A session on screen (active or locked) = lockdown on; none = off.
    fn set_active(&self, session_id: Option<String>) {
        let on = session_id.is_some();
        *self.active_session.lock().unwrap() = session_id;
        if let Some(app) = self.app.get() {
            if on {
                crate::lockdown::engage(app);
            } else {
                crate::lockdown::release(app);
            }
        }
    }

    pub fn active_session(&self) -> Option<String> {
        self.active()
    }

    fn active(&self) -> Option<String> {
        self.active_session.lock().unwrap().clone()
    }

    /// Track the session from any status reply: on screen while active/locked, not after.
    fn track(&self, session_id: &str, reply: &Value) {
        match reply["status"].as_str() {
            Some("active") | Some("locked") => self.set_active(Some(session_id.to_string())),
            Some(_) => self.set_active(None),
            None => {}
        }
    }

    fn with_drafts<T>(&self, f: impl FnOnce(&DraftStore) -> T) -> Option<T> {
        self.drafts.lock().unwrap().as_ref().map(f)
    }
}

#[derive(Serialize)]
pub struct LoginResult {
    user: User,
    /// None until the password is changed (then the laptop is registered).
    device_id: Option<String>,
    needs_password_change: bool,
}

fn platform() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

fn device_label() -> String {
    let name = std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .unwrap_or_else(|_| "Student laptop".into());
    name.chars().take(120).collect()
}

/// SHA-256 of this app's own executable, sent on exam start so the server can
/// refuse unapproved builds (CLIENT_PROTOCOL.md section 7). Computed once; lib.rs
/// starts it in the background at launch so Start never waits for it.
pub fn build_sha256() -> Option<String> {
    static HASH: OnceLock<Option<String>> = OnceLock::new();
    HASH.get_or_init(|| {
        let exe = std::env::current_exe().ok()?;
        let bytes = std::fs::read(exe).ok()?;
        Some(hex::encode(Sha256::digest(bytes)))
    })
    .clone()
}

/// IDs come from the web view; accept only real UUIDs so nothing else can end up in a path.
fn uuid_arg(value: &str) -> Result<String, ApiError> {
    uuid::Uuid::parse_str(value)
        .map(|u| u.to_string())
        .map_err(|_| ApiError::local("bad_id", "Invalid id."))
}

fn check_answer(language: &str, code: &str) -> Result<(), ApiError> {
    if language.is_empty() || language.len() > 32 {
        return Err(ApiError::local("bad_language", "Pick a language."));
    }
    if code.len() > MAX_CODE_BYTES {
        return Err(ApiError::local("code_too_large", "Code must be under 64 KB."));
    }
    Ok(())
}

fn now_iso() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true)
}

/// Load the key from the keychain and make sure this laptop is registered (section 2).
async fn setup_device(client: &mut ApiClient) -> Result<String, ApiError> {
    let user_id = client
        .user()
        .ok_or_else(|| ApiError::local("not_signed_in", "Sign in first."))?
        .id
        .clone();
    let key = keystore::load_or_create_key(&user_id).map_err(|m| ApiError::local("keychain_error", m))?;
    client.set_key(key);
    if let Some(id) = keystore::load_device_id(&user_id) {
        client.set_device_id(Some(id.clone()));
        return Ok(id);
    }
    let id = client.register_device(&device_label(), platform()).await?;
    keystore::save_device_id(&user_id, &id);
    Ok(id)
}

/// Open this user's local encrypted draft store.
fn setup_drafts(app: &AppHandle, state: &AppState, user_id: &str) -> Result<(), ApiError> {
    let key = keystore::load_or_create_draft_key(user_id).map_err(|m| ApiError::local("keychain_error", m))?;
    let dir = app
        .path()
        .app_local_data_dir()
        .map_err(|_| ApiError::local("storage_error", "Can't find the app data folder."))?
        .join("drafts")
        .join(user_id);
    let store = DraftStore::new(dir, &key).map_err(|m| ApiError::local("storage_error", m))?;
    *state.drafts.lock().unwrap() = Some(store);
    Ok(())
}

/// Signed call. On `unknown_device` (e.g. the server's database was reset) register
/// again once and retry; the retry is signed again with a new nonce.
async fn signed(state: &AppState, method: Method, path: &str, body: Option<Value>) -> Result<Value, ApiError> {
    let client = state.client.lock().await.clone();
    match client.signed(method.clone(), path, body.as_ref()).await {
        Err(e) if e.code == "unknown_device" => {
            let mut guard = state.client.lock().await;
            if let Some(user) = guard.user() {
                keystore::forget_device_id(&user.id.clone());
            }
            guard.set_device_id(None);
            setup_device(&mut guard).await?;
            let client = guard.clone();
            drop(guard);
            client.signed(method, path, body.as_ref()).await
        }
        other => other,
    }
}

/// POST .../events (section 5). Kinds are chosen here in Rust, never passed in from JS.
pub(crate) async fn report_event(state: &AppState, session_id: &str, kind: &str, details: Value) -> Result<Value, ApiError> {
    let body = json!({"events": [{"kind": kind, "client_ts": now_iso(), "details": details}]});
    let reply = signed(state, Method::POST, &format!("/api/v1/student/sessions/{session_id}/events"), Some(body)).await?;
    state.track(session_id, &reply);
    Ok(reply)
}

/// Called from the window event handler in lib.rs. Reports `focus_lost` if the window
/// stays unfocused for FOCUS_GRACE_MS during an exam. Read from the OS window,
/// not from JavaScript.
pub fn on_focus_changed(app: &AppHandle, focused: bool) {
    let state = app.state::<AppState>();
    let generation = state.focus_gen.fetch_add(1, Ordering::SeqCst) + 1;
    if cfg!(debug_assertions) {
        eprintln!("[aegis] window {}", if focused { "focused" } else { "lost focus" });
    }
    if focused {
        return;
    }
    let Some(session_id) = state.active() else { return };
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(FOCUS_GRACE_MS)).await;
        let state = app.state::<AppState>();
        let still_away = |state: &AppState| {
            state.focus_gen.load(Ordering::SeqCst) == generation && state.active().as_deref() == Some(session_id.as_str())
        };
        if !still_away(&state) {
            return; // focus came back in time (a notification flicker), or the exam ended
        }
        // A real departure (touchpad gesture to another desktop, Win+D, a click on another
        // window...). Gestures cannot be blocked by any app, so: count it, then pull the
        // student back to the exam until the window has focus again.
        let _ = report_event(&state, &session_id, "focus_lost", json!({"grace_ms": FOCUS_GRACE_MS})).await;
        for _ in 0..600 {
            if !still_away(&state) || !crate::lockdown::engaged() {
                break;
            }
            crate::lockdown::pull_back(&app);
            tokio::time::sleep(Duration::from_millis(1000)).await;
        }
    });
}

// ------------------------------------------------------------------- sign in (sections 1, 2)

#[tauri::command]
pub async fn login(app: AppHandle, state: State<'_, AppState>, email: String, password: String)
    -> Result<LoginResult, ApiError> {
    let mut client = state.client.lock().await;
    state.set_active(None);
    let user = client.login(email.trim(), &password).await?;
    if user.must_change_password {
        return Ok(LoginResult { user, device_id: None, needs_password_change: true });
    }
    let device_id = setup_device(&mut client).await?;
    setup_drafts(&app, &state, &user.id)?;
    Ok(LoginResult { user, device_id: Some(device_id), needs_password_change: false })
}

#[tauri::command]
pub async fn change_password(
    app: AppHandle,
    state: State<'_, AppState>,
    current_password: String,
    new_password: String,
) -> Result<LoginResult, ApiError> {
    let mut client = state.client.lock().await;
    client.change_password(&current_password, &new_password).await?;
    let device_id = setup_device(&mut client).await?;
    let user = client.user().cloned().ok_or_else(|| ApiError::local("not_signed_in", "Sign in first."))?;
    setup_drafts(&app, &state, &user.id)?;
    Ok(LoginResult { user, device_id: Some(device_id), needs_password_change: false })
}

#[tauri::command]
pub async fn logout(state: State<'_, AppState>) -> Result<(), ApiError> {
    let mut client = state.client.lock().await;
    let _ = client.post("/api/v1/auth/logout").await; // best effort; forget locally anyway
    client.clear();
    state.set_active(None);
    *state.drafts.lock().unwrap() = None;
    Ok(())
}

// ------------------------------------------------------------------- exam flow (section 4)

/// GET /student/exams (bearer only).
#[tauri::command]
pub async fn list_exams(state: State<'_, AppState>) -> Result<Value, ApiError> {
    let client = state.client.lock().await.clone();
    client.get("/api/v1/student/exams").await
}

/// Put locally saved code that never reached the server back into the view
/// (after a crash or a Wi-Fi drop). Marks it `unsynced` so the UI sends it again.
fn merge_local_drafts(state: &AppState, view: &mut Value) {
    let Some(session_id) = view["session_id"].as_str().map(str::to_string) else { return };
    let Some(local) = state.with_drafts(|d| d.load(&session_id)) else { return };
    if let Some(questions) = view["questions"].as_array_mut() {
        for q in questions {
            let Some(qid) = q["id"].as_str() else { continue };
            if let Some(d) = local.get(qid).filter(|d| !d.synced) {
                let server_revision = q["draft"]["revision"].as_i64();
                q["draft"] = json!({
                    "language": d.language,
                    "code": d.code,
                    "revision": server_revision.or(d.revision),
                    "updated_at": now_iso(),
                    "unsynced": true,
                });
            }
        }
    }
}

/// POST /student/exams/{id}/start (signed). Calling it again after a crash resumes;
/// a resume counts as `app_restarted` (section 5).
#[tauri::command]
pub async fn start_exam(
    state: State<'_, AppState>,
    exam_id: String,
    access_code: Option<String>,
) -> Result<Value, ApiError> {
    let exam_id = uuid_arg(&exam_id)?;
    let code = access_code.map(|c| c.trim().to_uppercase()).filter(|c| !c.is_empty());
    let body = json!({
        "access_code": code,
        "client_version": CLIENT_VERSION,
        "client_build_sha256": build_sha256(),
    });
    let mut view = signed(&state, Method::POST, &format!("/api/v1/student/exams/{exam_id}/start"), Some(body)).await?;
    let session_id = view["session_id"].as_str().unwrap_or_default().to_string();
    state.track(&session_id, &view);

    // Started more than 20 s ago by the server's clock = this is a relaunch, not a new start.
    let started = view["started_at"].as_str().and_then(|s| DateTime::parse_from_rfc3339(s).ok());
    let now = view["server_now"].as_str().and_then(|s| DateTime::parse_from_rfc3339(s).ok());
    if let (Some(started), Some(now)) = (started, now) {
        let seconds = (now - started).num_seconds();
        if seconds > 20 && view["status"] == "active" {
            if let Ok(status) = report_event(&state, &session_id, "app_restarted", json!({"seconds_since_start": seconds})).await {
                for key in ["status", "strikes", "lock_reason"] {
                    view[key] = status[key].clone();
                }
            }
        }
    }
    merge_local_drafts(&state, &mut view);
    Ok(view)
}

/// GET /student/sessions/{id} (signed): questions, samples, drafts, time.
#[tauri::command]
pub async fn get_session(state: State<'_, AppState>, session_id: String) -> Result<Value, ApiError> {
    let sid = uuid_arg(&session_id)?;
    let mut view = signed(&state, Method::GET, &format!("/api/v1/student/sessions/{sid}"), None).await?;
    state.track(&sid, &view);
    merge_local_drafts(&state, &mut view);
    Ok(view)
}

/// PUT /student/sessions/{id}/answers/{qid} (signed). Autosave: the local encrypted copy
/// is written first, so the code is safe even if this request fails.
#[tauri::command]
pub async fn save_answer(
    state: State<'_, AppState>,
    session_id: String,
    question_id: String,
    language: String,
    code: String,
    base_revision: Option<i64>,
) -> Result<Value, ApiError> {
    let (sid, qid) = (uuid_arg(&session_id)?, uuid_arg(&question_id)?);
    check_answer(&language, &code)?;
    state.with_drafts(|d| d.put_unsynced(&sid, &qid, &language, &code, base_revision));
    let body = json!({"language": language, "code": code, "base_revision": base_revision});
    let reply = signed(&state, Method::PUT, &format!("/api/v1/student/sessions/{sid}/answers/{qid}"), Some(body)).await?;
    let revision = reply["revision"].as_i64();
    state.with_drafts(|d| d.mark_synced(&sid, &qid, &code, revision));
    Ok(reply)
}

async fn run_or_submit(state: &AppState, session_id: &str, question_id: &str, language: String, code: String,
                       action: &str) -> Result<Value, ApiError> {
    let (sid, qid) = (uuid_arg(session_id)?, uuid_arg(question_id)?);
    check_answer(&language, &code)?;
    state.with_drafts(|d| d.put_unsynced(&sid, &qid, &language, &code, None));
    let body = json!({"language": language, "code": code});
    let reply = signed(state, Method::POST, &format!("/api/v1/student/sessions/{sid}/answers/{qid}/{action}"),
                       Some(body)).await?;
    // Running also saves on the server (revision not returned).
    state.with_drafts(|d| d.mark_synced(&sid, &qid, &code, None));
    Ok(reply)
}

/// POST .../run (signed): sample tests, with outputs.
#[tauri::command]
pub async fn run_code(state: State<'_, AppState>, session_id: String, question_id: String, language: String,
                      code: String) -> Result<Value, ApiError> {
    run_or_submit(&state, &session_id, &question_id, language, code, "run").await
}

/// POST .../submit (signed): all tests (hidden results per exam policy).
#[tauri::command]
pub async fn submit_code(state: State<'_, AppState>, session_id: String, question_id: String, language: String,
                         code: String) -> Result<Value, ApiError> {
    run_or_submit(&state, &session_id, &question_id, language, code, "submit").await
}

/// POST .../finish (signed): hand in, get receipts.
#[tauri::command]
pub async fn finish_exam(state: State<'_, AppState>, session_id: String) -> Result<Value, ApiError> {
    let sid = uuid_arg(&session_id)?;
    let reply = signed(&state, Method::POST, &format!("/api/v1/student/sessions/{sid}/finish"), None).await?;
    state.set_active(None);
    state.with_drafts(|d| d.remove_session(&sid));
    Ok(reply)
}

/// POST .../heartbeat (signed), every 10 s. Focus, full screen and display count are
/// read from the real window here, not trusted from JavaScript.
#[tauri::command]
pub async fn heartbeat(
    state: State<'_, AppState>,
    window: WebviewWindow,
    session_id: String,
) -> Result<Value, ApiError> {
    let sid = uuid_arg(&session_id)?;
    let displays = window.available_monitors().map(|m| m.len()).unwrap_or(1).min(16);
    let body = json!({
        "focused": window.is_focused().unwrap_or(false),
        "fullscreen": window.is_fullscreen().unwrap_or(false),
        "displays": displays,
        "client_version": CLIENT_VERSION,
    });
    let reply = signed(&state, Method::POST, &format!("/api/v1/student/sessions/{sid}/heartbeat"), Some(body)).await?;
    state.track(&sid, &reply);
    Ok(reply)
}

/// The editor refused clipboard content copied from outside it (section 7).
/// Only the size is reported, never the text.
#[tauri::command]
pub async fn report_blocked_paste(state: State<'_, AppState>, session_id: String, chars: u32)
    -> Result<Value, ApiError> {
    let sid = uuid_arg(&session_id)?;
    report_event(&state, &sid, "paste_blocked", json!({"chars": chars})).await
}

#[derive(Serialize)]
pub struct PrecheckResult {
    ok: bool,
    problems: Vec<crate::lockdown::detect::Finding>,
}

/// Before Start: the same checks the exam loop runs, so the student can fix things
/// (unplug a screen, close AnyDesk) before anything is reported.
#[tauri::command]
pub async fn precheck(app: AppHandle) -> Result<PrecheckResult, ApiError> {
    if crate::lockdown::disabled_for_dev() {
        return Ok(PrecheckResult { ok: true, problems: vec![] });
    }
    // macOS: show the system's "allow in Accessibility" prompt the first time.
    #[cfg(target_os = "macos")]
    if !crate::lockdown::macos::accessibility_allowed() {
        crate::lockdown::macos::request_accessibility();
    }
    let problems = tauri::async_runtime::spawn_blocking(move || crate::lockdown::detect::Scanner::new().scan(&app))
        .await
        .map_err(|_| ApiError::local("precheck_failed", "Could not check this laptop."))?;
    Ok(PrecheckResult { ok: problems.is_empty(), problems })
}

/// "Quit" button: close the app (and, on Windows, return to the normal desktop).
/// Refused while an exam is on screen.
#[tauri::command]
pub fn quit_app(app: AppHandle) -> Result<(), ApiError> {
    if crate::lockdown::engaged() {
        return Err(ApiError::local("exam_running", "Finish the exam first."));
    }
    app.exit(0);
    Ok(())
}
