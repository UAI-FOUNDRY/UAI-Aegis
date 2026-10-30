// Hide the extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Windows: start the app on its own exam desktop (see lockdown/secure_desktop.rs).
    // Returns None in the copy running on that desktop, or if it could not be set up.
    #[cfg(windows)]
    if let Some(code) = uai_aegis_lib::lockdown::secure_desktop::run_launcher_if_needed() {
        std::process::exit(code);
    }
    uai_aegis_lib::run()
}
