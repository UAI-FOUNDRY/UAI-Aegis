pub mod client;
mod commands;
pub mod drafts;
mod keystore;
pub mod lockdown;
mod navguard;
pub mod signing;

use tauri::{Manager, RunEvent, WindowEvent};

pub fn run() {
    // Hash our own executable in the background now, not when the student presses Start.
    std::thread::spawn(|| {
        commands::build_sha256();
    });
    let builder = tauri::Builder::default().plugin(navguard::plugin());
    // macOS: an app menu without Quit, Hide or Minimise, so Cmd+Q / Cmd+H / Cmd+M never act
    // (the app has its own Quit button outside exams). Edit keeps copy and paste working.
    #[cfg(target_os = "macos")]
    let builder = builder.menu(exam_menu);
    builder
        .manage(commands::AppState::new())
        .setup(|app| {
            app.state::<commands::AppState>().set_app(app.handle().clone());
            lockdown::start_monitor(app.handle().clone());
            #[cfg(target_os = "macos")]
            lockdown::macos::setup(app.handle());
            #[cfg(target_os = "linux")]
            lockdown::start_session_guard(app.handle().clone());
            #[cfg(windows)]
            {
                lockdown::win32::start_keyboard_hook();
                if lockdown::secure_desktop::on_secure_desktop() {
                    // Nothing else lives on the exam desktop: the app always fills the screen
                    // and cannot be minimised (there is no taskbar to bring it back).
                    if let Some(w) = app.get_webview_window("main") {
                        let _ = w.set_minimizable(false);
                        let _ = w.set_fullscreen(true);
                        let _ = w.set_focus();
                    }
                    lockdown::start_desktop_guard(app.handle().clone());
                    lockdown::secure_desktop::child_ready();
                }
            }
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
            commands::quit_app,
        ])
        .build(tauri::generate_context!())
        .expect("error while building UAI Aegis")
        .run(|_app, event| {
            // Nothing quits the app while an exam is on screen (macOS Dock "Quit", log out...).
            if let RunEvent::ExitRequested { api, .. } = &event {
                if lockdown::engaged() {
                    api.prevent_exit();
                }
            }
        });
}

/// macOS menu bar: the app menu (About only) and Edit (needed for copy and paste in the
/// editor). No Quit, Hide, Minimise, Close or Full Screen items.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn exam_menu<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> tauri::Result<tauri::menu::Menu<R>> {
    use tauri::menu::{Menu, PredefinedMenuItem, Submenu};
    let app_menu = Submenu::with_items(app, "UAI Aegis", true, &[&PredefinedMenuItem::about(app, None, None)?])?;
    let edit = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;
    Menu::with_items(app, &[&app_menu, &edit])
}
