//! Exam lockdown (CLIENT_PROTOCOL.md section 7).
//!
//! While a session is on screen (active or locked) the window becomes a kiosk:
//! full screen, always on top, not closable or minimisable, hidden from screen
//! capture. On Windows a keyboard hook also blocks Alt+Tab, the Windows key and
//! friends. A background loop watches for extra displays, VMs, remote sessions,
//! remote-control / recording tools and debuggers, and reports each new finding once.
//!
//! The server decides what each event means (strike or lock); the app only reports.

pub mod detect;
#[cfg(windows)]
pub mod secure_desktop;
#[cfg(windows)]
pub mod win32;

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

use serde_json::json;
use tauri::{AppHandle, Manager};

static ENGAGED: AtomicBool = AtomicBool::new(false);
/// Milliseconds since app start when lockdown was engaged (for the full-screen grace period).
static ENGAGED_AT_MS: AtomicU64 = AtomicU64::new(0);
static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();

const SCAN_EVERY: Duration = Duration::from_secs(3);
/// Window managers need a moment to apply full screen after we ask for it.
const FULLSCREEN_GRACE_MS: u64 = 4000;
/// `shortcut_blocked` is info only; send at most one summary per this many seconds.
#[cfg_attr(not(windows), allow(dead_code))]
const SHORTCUT_REPORT_EVERY: Duration = Duration::from_secs(15);

fn now_ms() -> u64 {
    START.get_or_init(Instant::now).elapsed().as_millis() as u64
}

pub fn engaged() -> bool {
    ENGAGED.load(Ordering::SeqCst)
}

/// Development builds only: `AEGIS_LOCKDOWN=off` skips the kiosk, keyboard hook and
/// detection, so a hung dev build can never trap you. Release builds ignore it.
pub fn disabled_for_dev() -> bool {
    if !cfg!(debug_assertions) {
        return false;
    }
    #[cfg(windows)]
    if win32::dev_escape_used() {
        return true;
    }
    std::env::var("AEGIS_LOCKDOWN").map(|v| v.eq_ignore_ascii_case("off")).unwrap_or(false)
}

/// Turn the exam window into a kiosk. Safe to call repeatedly.
pub fn engage(app: &AppHandle) {
    if disabled_for_dev() || ENGAGED.swap(true, Ordering::SeqCst) {
        return;
    }
    ENGAGED_AT_MS.store(now_ms(), Ordering::SeqCst);
    if let Some(w) = app.get_webview_window("main") {
        // On the normal desktop (fallback): show the window on every virtual desktop.
        // On the separate exam desktop there are no virtual desktops, so skip it.
        #[cfg(windows)]
        if !secure_desktop::on_secure_desktop() {
            if let Ok(hwnd) = w.hwnd() {
                let _ = w.hide();
                win32::show_on_all_desktops(hwnd.0, true);
                let _ = w.show();
            }
        }
        let _ = w.set_content_protected(true); // Windows: excluded from capture; macOS: sharing type none
        let _ = w.set_minimizable(false);
        let _ = w.set_closable(false);
        // (no set_resizable(false): some systems then refuse to grow the window to full screen)
        let _ = w.set_always_on_top(true);
        let _ = w.set_fullscreen(true);
        let _ = w.set_focus();
    }
    #[cfg(windows)]
    win32::set_blocking(true);
}

/// Bring the exam window back to the front (after a gesture, Win+D, a click elsewhere).
/// On Windows this also switches back from another virtual desktop.
pub fn pull_back(app: &AppHandle) {
    if !engaged() {
        return;
    }
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_always_on_top(true);
        if !w.is_fullscreen().unwrap_or(false) {
            let _ = w.set_fullscreen(true);
        }
        let _ = w.set_focus();
    }
}

/// Back to a normal window (exam finished, signed out).
pub fn release(app: &AppHandle) {
    if !ENGAGED.swap(false, Ordering::SeqCst) {
        return;
    }
    #[cfg(windows)]
    win32::set_blocking(false);
    if let Some(w) = app.get_webview_window("main") {
        #[cfg(windows)]
        if secure_desktop::on_secure_desktop() {
            // Nothing else is on this desktop (no taskbar to bring a minimised window
            // back), so stay full screen; only the exam-time restrictions are lifted.
            let _ = w.set_always_on_top(false);
            let _ = w.set_closable(true);
            let _ = w.set_content_protected(false);
            return;
        }
        #[cfg(windows)]
        if let Ok(hwnd) = w.hwnd() {
            let _ = w.hide();
            win32::show_on_all_desktops(hwnd.0, false);
            let _ = w.show();
        }
        let _ = w.set_fullscreen(false);
        let _ = w.set_always_on_top(false);
        let _ = w.set_closable(true);
        let _ = w.set_minimizable(true);
        let _ = w.set_content_protected(false);
    }
}

/// Windows, separate exam desktop: if the screen leaves our desktop (Ctrl+Alt+Del then
/// Task Manager, a fast user switch...), switch it straight back and, during an exam,
/// report it once per departure. Checked twice a second.
#[cfg(windows)]
pub fn start_desktop_guard(app: AppHandle) {
    if !secure_desktop::on_secure_desktop() {
        return;
    }
    std::thread::spawn(move || {
        let mut away = false;
        loop {
            std::thread::sleep(Duration::from_millis(500));
            match secure_desktop::input_desktop_name() {
                Some(name) if name != secure_desktop::DESKTOP_NAME => {
                    secure_desktop::switch_to_exam_desktop();
                    if !away {
                        away = true;
                        let state = app.state::<crate::commands::AppState>();
                        if let (true, Some(session_id)) = (engaged(), state.active_session()) {
                            let app = app.clone();
                            tauri::async_runtime::spawn(async move {
                                let state = app.state::<crate::commands::AppState>();
                                let _ = crate::commands::report_event(
                                    &state,
                                    &session_id,
                                    "focus_lost",
                                    json!({"left_exam_desktop_for": name}),
                                )
                                .await;
                            });
                        }
                    }
                }
                Some(_) => away = false,
                None => {} // Ctrl+Alt+Del / UAC screen: cannot switch away from it
            }
        }
    });
}

/// Is the window actually covering the whole screen? The window library only remembers
/// that we *asked* for full screen, so also compare the real size with the monitor and
/// check it is not minimised.
fn really_fullscreen(w: &tauri::WebviewWindow) -> bool {
    if !w.is_fullscreen().unwrap_or(true) || w.is_minimized().unwrap_or(false) {
        return false;
    }
    match (w.outer_size(), w.current_monitor()) {
        (Ok(size), Ok(Some(monitor))) => {
            let m = monitor.size();
            size.width + 2 >= m.width && size.height + 2 >= m.height
        }
        _ => true, // cannot tell: do not accuse
    }
}

/// Background loop, started once at launch. Reports each new finding once per
/// occurrence: if a second monitor is unplugged and plugged in again, that is reported
/// again; if it simply stays plugged in, it is not repeated every 3 seconds.
pub fn start_monitor(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        let mut scanner = detect::Scanner::new();
        let mut reported: HashSet<String> = HashSet::new();
        let mut current_session: Option<String> = None;
        let mut fullscreen_reported = false;
        let mut last_shortcut_report = Instant::now();

        loop {
            tokio::time::sleep(SCAN_EVERY).await;
            let state = app.state::<crate::commands::AppState>();

            #[cfg(windows)]
            if win32::dev_escape_used() {
                if secure_desktop::on_secure_desktop() {
                    secure_desktop::leave_and_exit(); // Ctrl+Alt+Shift+Q in a development build
                }
                if engaged() {
                    release(&app);
                }
            }

            let session = state.active_session();
            if session != current_session {
                reported.clear();
                fullscreen_reported = false;
                current_session = session.clone();
            }
            let Some(session_id) = session else { continue };
            if !engaged() {
                continue;
            }

            // 1. Environment checks (displays, VM, remote session, tools, debugger)
            let findings = scanner.scan(&app);
            let keys: HashSet<String> = findings.iter().map(|f| f.key.clone()).collect();
            for f in &findings {
                if !reported.contains(&f.key) {
                    let _ = crate::commands::report_event(&state, &session_id, f.kind, f.details.clone()).await;
                }
            }
            reported = keys; // forget what went away, so it is reported again if it comes back

            // 2. Full screen left (e.g. a window manager shortcut we could not block)
            if now_ms().saturating_sub(ENGAGED_AT_MS.load(Ordering::SeqCst)) > FULLSCREEN_GRACE_MS {
                if let Some(w) = app.get_webview_window("main") {
                    let full = really_fullscreen(&w);
                    if !full && !fullscreen_reported {
                        fullscreen_reported = true;
                        let _ = crate::commands::report_event(&state, &session_id, "fullscreen_exited", json!({})).await;
                    }
                    if full {
                        fullscreen_reported = false;
                    } else {
                        // Put it back: toggle so the request is not ignored as "already full screen".
                        let _ = w.unminimize();
                        let _ = w.set_fullscreen(false);
                        let _ = w.set_fullscreen(true);
                        let _ = w.set_focus();
                    }
                }
            }

            // 3. Shortcuts the keyboard hook swallowed (info only, summarised)
            #[cfg(windows)]
            if last_shortcut_report.elapsed() >= SHORTCUT_REPORT_EVERY {
                let count = win32::take_blocked_count();
                if count > 0 {
                    last_shortcut_report = Instant::now();
                    let _ = crate::commands::report_event(&state, &session_id, "shortcut_blocked", json!({"count": count})).await;
                }
            }
            #[cfg(not(windows))]
            let _ = &mut last_shortcut_report;
        }
    });
}
