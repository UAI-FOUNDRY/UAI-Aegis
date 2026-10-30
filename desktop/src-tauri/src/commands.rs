//! The only functions the web view can call (allow-listed in build.rs and
//! capabilities/default.json). Each one builds and signs its request here in Rust;
//! there is deliberately no generic "sign these bytes" command (CLIENT_PROTOCOL.md section 8).

use std::sync::OnceLock;

use reqwest::Method;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{State, WebviewWindow};
use tokio::sync::Mutex;

use crate::client::{ApiClient, ApiError, User};
use crate::keystore;

/// Server address. Override at build time: AEGIS_API_URL=https://exams.college.edu npm run tauri build
pub const API_URL: &str = match option_env!("AEGIS_API_URL") {
    Some(url) => url,
    None => "http://localhost:8000",
};
pub const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct AppState {
    client: Mutex<ApiClient>,
}

impl AppState {
    pub fn new() -> Self {
        Self { client: Mutex::new(ApiClient::new(API_URL)) }
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
/// refuse unapproved builds (CLIENT_PROTOCOL.md section 7).
fn build_sha256() -> Option<String> {
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

#[tauri::command]
pub async fn login(state: State<'_, AppState>, email: String, password: String) -> Result<LoginResult, ApiError> {
    let mut client = state.client.lock().await;
    let user = client.login(email.trim(), &password).await?;
    if user.must_change_password {
        return Ok(LoginResult { user, device_id: None, needs_password_change: true });
    }
    let device_id = setup_device(&mut client).await?;
    Ok(LoginResult { user, device_id: Some(device_id), needs_password_change: false })
}

#[tauri::command]
pub async fn change_password(
    state: State<'_, AppState>,
    current_password: String,
    new_password: String,
) -> Result<LoginResult, ApiError> {
    let mut client = state.client.lock().await;
    client.change_password(&current_password, &new_password).await?;
    let device_id = setup_device(&mut client).await?;
    let user = client.user().cloned().ok_or_else(|| ApiError::local("not_signed_in", "Sign in first."))?;
    Ok(LoginResult { user, device_id: Some(device_id), needs_password_change: false })
}

#[tauri::command]
pub async fn logout(state: State<'_, AppState>) -> Result<(), ApiError> {
    let mut client = state.client.lock().await;
    let _ = client.post("/api/v1/auth/logout").await; // best effort; forget locally anyway
    client.clear();
    Ok(())
}

/// GET /student/exams (bearer only).
#[tauri::command]
pub async fn list_exams(state: State<'_, AppState>) -> Result<Value, ApiError> {
    let client = state.client.lock().await.clone();
    client.get("/api/v1/student/exams").await
}

/// POST /student/exams/{id}/start (signed). Calling it again after a crash resumes.
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
    signed(&state, Method::POST, &format!("/api/v1/student/exams/{exam_id}/start"), Some(body)).await
}

/// GET /student/sessions/{id} (signed): questions, samples, drafts, time.
#[tauri::command]
pub async fn get_session(state: State<'_, AppState>, session_id: String) -> Result<Value, ApiError> {
    let sid = uuid_arg(&session_id)?;
    signed(&state, Method::GET, &format!("/api/v1/student/sessions/{sid}"), None).await
}

/// POST /student/sessions/{id}/heartbeat (signed), every 10 s. Focus, full screen and
/// display count are read from the real window here, not trusted from JavaScript.
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
    signed(&state, Method::POST, &format!("/api/v1/student/sessions/{sid}/heartbeat"), Some(body)).await
}
