fn main() {
    // Declaring the app's commands here makes Tauri deny them unless a capability
    // allows them (capabilities/default.json). Add new commands to both lists.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "login",
            "change_password",
            "logout",
            "list_exams",
            "start_exam",
            "get_session",
            "save_answer",
            "run_code",
            "submit_code",
            "finish_exam",
            "heartbeat",
            "report_blocked_paste",
        ]),
    ))
    .expect("failed to run tauri-build");
}
