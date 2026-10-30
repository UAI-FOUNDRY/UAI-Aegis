//! HTTP client for the Aegis API. All requests (and all signing) happen here in Rust,
//! so neither the token's use nor the private key is reachable from the web view.

use ed25519_dalek::SigningKey;
use reqwest::{header::CONTENT_TYPE, Client, Method, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::signing;

/// Error sent to the UI. `message` is shown to the student; the UI branches on `code`
/// (docs/CLIENT_PROTOCOL.md section 6). `status` 0 means it never reached the server.
#[derive(Debug, Clone, Serialize)]
pub struct ApiError {
    pub status: u16,
    pub code: String,
    pub message: String,
    /// The whole `detail` object when the server sent extra fields
    /// (e.g. `revision` on 409 stale_revision).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<Value>,
}

impl ApiError {
    pub fn local(code: &str, message: impl Into<String>) -> Self {
        Self { status: 0, code: code.into(), message: message.into(), details: None }
    }
}

impl From<reqwest::Error> for ApiError {
    fn from(_: reqwest::Error) -> Self {
        ApiError::local("network_error", "Can't reach the exam server. Check your Wi-Fi and try again.")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct User {
    pub id: String,
    pub email: String,
    pub full_name: String,
    pub role: String,
    #[serde(default)]
    pub must_change_password: bool,
}

#[derive(Clone)]
pub struct ApiClient {
    http: Client,
    base: String,
    token: Option<String>,
    user: Option<User>,
    key: Option<SigningKey>,
    device_id: Option<String>,
}

impl ApiClient {
    pub fn new(base: &str) -> Self {
        let http = Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .expect("HTTP client");
        Self { http, base: base.trim_end_matches('/').to_string(), token: None, user: None, key: None, device_id: None }
    }

    pub fn user(&self) -> Option<&User> {
        self.user.as_ref()
    }

    pub fn device_id(&self) -> Option<&str> {
        self.device_id.as_deref()
    }

    pub fn set_key(&mut self, key: SigningKey) {
        self.key = Some(key);
    }

    pub fn set_device_id(&mut self, device_id: Option<String>) {
        self.device_id = device_id;
    }

    pub fn clear(&mut self) {
        self.token = None;
        self.user = None;
        self.key = None;
        self.device_id = None;
    }

    /// POST /auth/login (section 1). Keeps the token in memory only.
    pub async fn login(&mut self, email: &str, password: &str) -> Result<User, ApiError> {
        self.clear();
        let body = json!({"email": email, "password": password, "client": "desktop"});
        let out = self.send(Method::POST, "/api/v1/auth/login", Some(&body), false).await?;
        let token = out["token"].as_str().ok_or_else(|| ApiError::local("bad_response", "Unexpected sign-in reply."))?;
        let user: User = serde_json::from_value(out["user"].clone())
            .map_err(|_| ApiError::local("bad_response", "Unexpected sign-in reply."))?;
        if user.role != "student" {
            return Err(ApiError::local("not_a_student", "This app is for students. Professors use the web portal."));
        }
        self.token = Some(token.to_string());
        self.user = Some(user.clone());
        Ok(user)
    }

    /// POST /auth/change-password. The current session stays signed in.
    pub async fn change_password(&mut self, current: &str, new: &str) -> Result<(), ApiError> {
        let body = json!({"current_password": current, "new_password": new});
        self.send(Method::POST, "/api/v1/auth/change-password", Some(&body), false).await?;
        if let Some(u) = self.user.as_mut() {
            u.must_change_password = false;
        }
        Ok(())
    }

    /// POST /student/devices (section 2). Bearer only, not signed.
    pub async fn register_device(&mut self, label: &str, platform: &str) -> Result<String, ApiError> {
        let key = self.key.as_ref().ok_or_else(|| ApiError::local("no_key", "Device key not loaded."))?;
        let user_id = &self.user.as_ref().ok_or_else(|| ApiError::local("not_signed_in", "Sign in first."))?.id;
        let body = json!({
            "public_key": signing::public_key_b64(key),
            "proof": signing::registration_proof(key, user_id),
            "label": label,
            "platform": platform,
        });
        let out = self.send(Method::POST, "/api/v1/student/devices", Some(&body), false).await?;
        let id = out["device_id"]
            .as_str()
            .ok_or_else(|| ApiError::local("bad_response", "Unexpected registration reply."))?
            .to_string();
        self.device_id = Some(id.clone());
        Ok(id)
    }

    /// Bearer-only GET (exam list, results).
    pub async fn get(&self, path: &str) -> Result<Value, ApiError> {
        self.send(Method::GET, path, None, false).await
    }

    /// Bearer-only POST with no body (logout).
    pub async fn post(&self, path: &str) -> Result<Value, ApiError> {
        self.send(Method::POST, path, None, false).await
    }

    /// Signed request (section 3): every route under /exams/{id}/start and /sessions/...
    pub async fn signed(&self, method: Method, path: &str, body: Option<&Value>) -> Result<Value, ApiError> {
        self.send(method, path, body, true).await
    }

    async fn send(&self, method: Method, path: &str, body: Option<&Value>, sign: bool) -> Result<Value, ApiError> {
        // Serialise ONCE; these exact bytes are hashed, signed and sent.
        let bytes: Vec<u8> = match body {
            Some(v) => serde_json::to_vec(v).map_err(|_| ApiError::local("bad_request", "Could not encode request."))?,
            None => Vec::new(),
        };

        let mut req = self.http.request(method.clone(), format!("{}{}", self.base, path));
        if let Some(token) = &self.token {
            req = req.bearer_auth(token);
        }
        if body.is_some() {
            req = req.header(CONTENT_TYPE, "application/json");
        }
        if sign {
            let key = self.key.as_ref().ok_or_else(|| ApiError::local("no_key", "Device key not loaded."))?;
            let device = self.device_id.as_deref().ok_or_else(|| ApiError::local("unknown_device", "This laptop is not registered yet."))?;
            // Fresh timestamp + nonce on every call, retries included.
            let h = signing::sign_request(key, device, method.as_str(), path, &bytes);
            req = req
                .header(signing::HEADER_DEVICE, h.device)
                .header(signing::HEADER_TIMESTAMP, h.timestamp)
                .header(signing::HEADER_NONCE, h.nonce)
                .header(signing::HEADER_SIGNATURE, h.signature);
        }

        let resp = req.body(bytes).send().await?;
        let status = resp.status();
        let raw = resp.bytes().await?;
        if status.is_success() {
            if raw.is_empty() {
                return Ok(Value::Null);
            }
            return serde_json::from_slice(&raw).map_err(|_| ApiError::local("bad_response", "Unexpected server reply."));
        }
        Err(parse_error(status, &raw))
    }
}

/// Errors look like {"detail": {"code": "...", "message": "..."}} (section 6).
/// FastAPI validation errors use a list instead; handle both.
fn parse_error(status: StatusCode, raw: &[u8]) -> ApiError {
    let fallback = || ApiError {
        status: status.as_u16(),
        code: format!("http_{}", status.as_u16()),
        message: format!("The server answered {status}."),
        details: None,
    };
    let Ok(v) = serde_json::from_slice::<Value>(raw) else { return fallback() };
    let detail = &v["detail"];
    match detail {
        Value::Object(_) => ApiError {
            status: status.as_u16(),
            code: detail["code"].as_str().unwrap_or("error").to_string(),
            message: detail["message"].as_str().unwrap_or("Something went wrong.").to_string(),
            details: Some(detail.clone()),
        },
        Value::String(s) => ApiError { status: status.as_u16(), code: "error".into(), message: s.clone(), details: None },
        Value::Array(items) => ApiError {
            status: status.as_u16(),
            code: "invalid_request".into(),
            message: items
                .first()
                .and_then(|i| i["msg"].as_str())
                .unwrap_or("Invalid request.")
                .to_string(),
            details: None,
        },
        _ => fallback(),
    }
}
