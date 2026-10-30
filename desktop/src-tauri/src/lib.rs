pub mod client;
mod commands;
mod keystore;
pub mod signing;

pub fn run() {
    tauri::Builder::default()
        .manage(commands::AppState::new())
        .invoke_handler(tauri::generate_handler![
            commands::login,
            commands::change_password,
            commands::logout,
            commands::list_exams,
            commands::start_exam,
            commands::get_session,
            commands::heartbeat,
        ])
        .run(tauri::generate_context!())
        .expect("error while running UAI Aegis");
}
