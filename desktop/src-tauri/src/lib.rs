pub mod client;
mod commands;
pub mod drafts;
mod keystore;
pub mod lockdown;
pub mod signing;

use tauri::{Manager, WindowEvent};

pub fn run() {
    // Hash our own executable in the background now, not when the student presses Start.
    std::thread::spawn(|| {
        commands::build_sha256();
    });
    tauri::Builder::default()
        .manage(commands::AppState::new())
        .setup(|app| {
            app.state::<commands::AppState>().set_app(app.handle().clone());
            lockdown::start_monitor(app.handle().clone());
            #[cfg(windows)]
            lockdown::win32::start_keyboard_hook();
            Ok(())
        })
        .on_window_event(|window, event| match event {
            WindowEvent::Focused(focused) => commands::on_focus_changed(window.app_handle(), *focused),
            // The exam window cannot be closed while an exam is on screen.
            WindowEvent::CloseRequested { api, .. } if lockdown::engaged() => api.prevent_close(),
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            commands::login,
            commands::change_password,
            commands::logout,
            commands::list_exams,
            commands::start_exam,
            commands::get_session,
            commands::save_answer,
            commands::run_code,
            commands::submit_code,
            commands::finish_exam,
            commands::heartbeat,
            commands::report_blocked_paste,
            commands::precheck,
        ])
        .run(tauri::generate_context!())
        .expect("error while running UAI Aegis");
}
