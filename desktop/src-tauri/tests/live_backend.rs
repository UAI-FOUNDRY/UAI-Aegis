//! End-to-end check of the Rust client against a running backend:
//! login -> register device -> signed start -> signed heartbeat -> signed session view.
//!
//! Needs the backend running with demo data. Skipped by default; run it with:
//!   AEGIS_TEST_EXAM_ID=<exam id> cargo test --test live_backend -- --ignored --nocapture
//! Optional: AEGIS_API_URL (default http://localhost:8000), AEGIS_TEST_STUDENT (default student2.demo@aegis.local)

use reqwest::Method;
use serde_json::{json, Value};
use uai_aegis_lib::{client::ApiClient, signing};

const PASSWORD: &str = "Aegis-demo-2026!";

fn api() -> String {
    std::env::var("AEGIS_API_URL").unwrap_or_else(|_| "http://localhost:8000".into())
}

/// Plays the professor: opens the exam (or rotates the code if it is already open).
async fn access_code(exam_id: &str) -> String {
    let http = reqwest::Client::new();
    let login: Value = http
        .post(format!("{}/api/v1/auth/login", api()))
        .json(&json!({"email": "prof.demo@aegis.local", "password": PASSWORD}))
        .send().await.unwrap().json().await.unwrap();
    let token = login["token"].as_str().expect("professor login").to_string();
    for action in ["open", "rotate-code"] {
        let r = http
            .post(format!("{}/api/v1/exams/{exam_id}/{action}", api()))
            .bearer_auth(&token)
            .json(&json!({}))
            .send().await.unwrap();
        if r.status().is_success() {
            let v: Value = r.json().await.unwrap();
            return v["access_code"].as_str().expect("access_code").to_string();
        }
    }
    panic!("could not open the exam");
}

#[tokio::test]
#[ignore]
async fn full_signed_flow() {
    let exam_id = std::env::var("AEGIS_TEST_EXAM_ID").expect("set AEGIS_TEST_EXAM_ID");
    let student = std::env::var("AEGIS_TEST_STUDENT").unwrap_or_else(|_| "student2.demo@aegis.local".into());
    let code = access_code(&exam_id).await;

    let mut c = ApiClient::new(&api());
    let user = c.login(&student, PASSWORD).await.expect("login");
    println!("1. login ok: {} ({})", user.full_name, user.id);

    // In the app this key comes from the OS keychain; here a throwaway key is enough.
    c.set_key(signing::generate_key());
    let device = c.register_device("Rust test laptop", "linux").await.expect("register");
    println!("2. device registered: {device}");

    let start = c
        .signed(Method::POST, &format!("/api/v1/student/exams/{exam_id}/start"),
                Some(&json!({"access_code": code, "client_version": "0.1.0"})))
        .await
        .expect("start");
    let sid = start["session_id"].as_str().unwrap().to_string();
    println!("3. start ok: status={} seconds_left={}", start["status"], start["seconds_left"]);
    assert!(start["status"] == "active" || start["status"] == "locked");

    let hb = c
        .signed(Method::POST, &format!("/api/v1/student/sessions/{sid}/heartbeat"),
                Some(&json!({"focused": true, "fullscreen": true, "displays": 1})))
        .await
        .expect("heartbeat");
    println!("4. heartbeat ok: {hb}");

    // GET with an empty body: hash of nothing.
    let view = c.signed(Method::GET, &format!("/api/v1/student/sessions/{sid}"), None).await.expect("session");
    println!("5. session view ok: {} question(s)", view["questions"].as_array().map(|q| q.len()).unwrap_or(0));

    // Same student and device id, but a different private key: the server must refuse it.
    let mut thief = ApiClient::new(&api());
    thief.login(&student, PASSWORD).await.expect("second login");
    thief.set_key(signing::generate_key());
    thief.set_device_id(Some(device.clone()));
    let err = thief
        .signed(Method::POST, &format!("/api/v1/student/sessions/{sid}/heartbeat"), Some(&json!({})))
        .await
        .unwrap_err();
    println!("6. wrong key refused by server: {} {}", err.status, err.code);
    assert_eq!(err.status, 401);
}
