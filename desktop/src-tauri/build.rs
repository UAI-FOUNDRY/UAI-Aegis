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
            "heartbeat",
        ]),
    ))
    .expect("failed to run tauri-build");
}
