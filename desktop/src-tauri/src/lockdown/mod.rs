//! Exam lockdown (CLIENT_PROTOCOL.md section 7).
//!
//! While a session is on screen (active or locked) the window becomes a kiosk:
//! full screen, always on top, not closable or minimisable, hidden from screen
//! capture. Per platform:
//! - Windows: the app runs on its own Win32 desktop and a keyboard hook blocks Alt+Tab,
//!   the Windows key and friends (secure_desktop.rs, win32.rs).
//! - macOS: kiosk presentation options (no Dock, menu bar, app switching, Force Quit or
//!   log out) and a key filter for Spotlight, Spaces and the rest (macos.rs, mac_keys.rs).
//! - Linux: exams run only in the "UAI Aegis Exam" login session, a kiosk compositor with
//!   nothing else in it (linux.rs).
//!
//! Anything that still gets the student off the exam (a touchpad swipe no app can block,
//! a click on another window, Task Manager from Ctrl+Alt+Del...) is caught by a guard that
//! checks the window 20 times a second: the exam is pulled back to the front within a
//! fraction of a second and the trip is reported once, as one strike (away.rs).
//!
//! A slower loop watches for extra displays, VMs, remote sessions, remote-control /
//! recording tools and debuggers, and reports each new finding once.
//!
//! The server decides what each event means (strike or lock); the app only reports.

pub mod away;
pub mod detect;
#[cfg(target_os = "linux")]
pub mod linux;
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub mod mac_keys;
#[cfg(target_os = "macos")]
pub mod macos;
#[cfg(windows)]
pub mod secure_desktop;
#[cfg(windows)]
pub mod win32;

use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::{AppHandle, Manager};

use away::{Action, Reason};

static ENGAGED: AtomicBool = AtomicBool::new(false);
/// Milliseconds since app start when lockdown was engaged (for the full-screen grace period).
static ENGAGED_AT_MS: AtomicU64 = AtomicU64::new(0);
static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();

const SCAN_EVERY: Duration = Duration::from_secs(3);
/// Window managers need a moment to apply full screen after we ask for it.
const FULLSCREEN_GRACE_MS: u64 = 4000;
/// The window needs a moment to come up and take focus when an exam starts.
const SETTLE_MS: u64 = 1000;
/// A full-screen request is re-sent at most this often (window managers animate it).
const FULLSCREEN_RETRY_MS: u64 = 1000;
static LAST_PULL_MS: AtomicU64 = AtomicU64::new(0);
static LAST_FULLSCREEN_FIX_MS: AtomicU64 = AtomicU64::new(0);
/// `shortcut_blocked` is info only; send at most one summary per this many seconds.
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
        enter_fullscreen(app, &w);
        let _ = w.set_focus();
    }
    #[cfg(windows)]
    {
        win32::set_blocking(true);
        win32::disable_accessibility_hotkeys();
    }
    #[cfg(target_os = "macos")]
    {
        macos::set_blocking(true);
        if let Some(w) = app.get_webview_window("main") {
            macos::set_all_spaces(&w, true);
        }
    }
}

/// Full screen for the exam. macOS: "simple" full screen (no separate Space) plus the kiosk
/// options; elsewhere the normal full screen.
fn enter_fullscreen(app: &AppHandle, w: &tauri::WebviewWindow) {
    #[cfg(target_os = "macos")]
    {
        let _ = w.set_simple_fullscreen(true);
        macos::set_kiosk(app, true);
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
        let _ = w.set_fullscreen(true);
    }
}

/// Shortcuts the platform's key blocker swallowed since the last call.
fn take_blocked_shortcuts() -> u32 {
    #[cfg(windows)]
    return win32::take_blocked_count();
    #[cfg(target_os = "macos")]
    return macos::take_blocked_count();
    #[allow(unreachable_code)]
    0
}

/// Bring the exam window back to the front (after a gesture, Win+D, a click elsewhere).
/// On Windows this also switches back from another virtual desktop.
pub fn pull_back(app: &AppHandle) {
    if !engaged() {
        return;
    }
    LAST_PULL_MS.store(now_ms(), Ordering::SeqCst);
    #[cfg(target_os = "macos")]
    macos::activate(app);
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_always_on_top(true);
        if !really_fullscreen(&w) {
            let now = now_ms();
            if now.saturating_sub(LAST_FULLSCREEN_FIX_MS.load(Ordering::SeqCst)) >= FULLSCREEN_RETRY_MS {
                LAST_FULLSCREEN_FIX_MS.store(now, Ordering::SeqCst);
                // Toggle, so the request is not ignored as "already full screen".
                #[cfg(target_os = "macos")]
                let _ = w.set_simple_fullscreen(false);
                #[cfg(not(target_os = "macos"))]
                let _ = w.set_fullscreen(false);
                enter_fullscreen(app, &w);
            }
        }
        let _ = w.set_focus();
        // Windows may refuse to hand the focus to a background app; force it.
        #[cfg(windows)]
        if let Ok(hwnd) = w.hwnd() {
            win32::force_foreground(hwnd.0);
        }
    }
}

/// The student is off the exam: pull back (at most every `away::PULL_EVERY`) and report
/// the trip once, as one strike, with its excursion id.
pub fn left_exam(app: &AppHandle, reason: Reason, details: Value) {
    if !engaged() {
        return;
    }
    match away::off(reason) {
        Action::None => {}
        Action::Pull => {
            if now_ms().saturating_sub(LAST_PULL_MS.load(Ordering::SeqCst)) >= away::PULL_EVERY.as_millis() as u64 {
                pull_back(app);
            }
        }
        Action::PullAndReport { id, reason } => {
            pull_back(app);
            let mut details = match details {
                Value::Object(map) => map,
                _ => serde_json::Map::new(),
            };
            details.insert("excursion".into(), json!(away::excursion_tag(id)));
            details.insert("reason".into(), json!(reason.name()));
            if cfg!(debug_assertions) {
                eprintln!("[aegis] left the exam ({}), pulling back", reason.name());
            }
            let state = app.state::<crate::commands::AppState>();
            if let Some(session_id) = state.active_session() {
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    let state = app.state::<crate::commands::AppState>();
                    let _ = crate::commands::report_event(&state, &session_id, reason.kind(), Value::Object(details))
                        .await;
                });
            }
        }
    }
}

/// The exam is on screen again (focused, full screen, on the exam desktop / session).
fn back_on_exam() {
    if let Some(took) = away::back() {
        if cfg!(debug_assertions) {
            eprintln!("[aegis] back on the exam after {} ms", took.as_millis());
        }
    }
}

/// Checks the exam window 20 times a second while an exam is on screen: if it is not
/// focused or no longer covers the screen, the student is pulled straight back.
pub fn start_guard(app: AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(away::CHECK_EVERY);
        if !engaged() {
            away::back();
            continue;
        }
        let since = now_ms().saturating_sub(ENGAGED_AT_MS.load(Ordering::SeqCst));
        if since < SETTLE_MS {
            continue;
        }
        let Some(w) = app.get_webview_window("main") else { continue };
        let focused = w.is_focused().unwrap_or(true) && !w.is_minimized().unwrap_or(false);
        let full = since < FULLSCREEN_GRACE_MS || really_fullscreen(&w);
        if focused && full {
            // On Windows/Linux the desktop / session guards also have a say.
            if !away_elsewhere() {
                back_on_exam();
            }
            continue;
        }
        let reason = if focused { Reason::Fullscreen } else { Reason::Window };
        left_exam(&app, reason, json!({}));
    });
}

/// Is the screen on another desktop (Windows) or session (Linux) right now?
fn away_elsewhere() -> bool {
    #[cfg(windows)]
    if secure_desktop::on_secure_desktop() {
        if let Some(name) = secure_desktop::input_desktop_name() {
            return name != secure_desktop::DESKTOP_NAME;
        }
    }
    #[cfg(target_os = "linux")]
    if let Ok(session) = linux::exam_session_id() {
        return linux::session_active(&session) == Some(false);
    }
    false
}

/// Back to a normal window (exam finished, signed out).
pub fn release(app: &AppHandle) {
    if !ENGAGED.swap(false, Ordering::SeqCst) {
        return;
    }
    away::back();
    #[cfg(windows)]
    {
        win32::set_blocking(false);
        win32::restore_accessibility_hotkeys();
    }
    #[cfg(target_os = "macos")]
    {
        macos::set_blocking(false);
        macos::set_kiosk(app, false);
        if let Some(w) = app.get_webview_window("main") {
            macos::set_all_spaces(&w, false);
        }
    }
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
        #[cfg(target_os = "macos")]
        let _ = w.set_simple_fullscreen(false);
        #[cfg(not(target_os = "macos"))]
        let _ = w.set_fullscreen(false);
        let _ = w.set_always_on_top(false);
        let _ = w.set_closable(true);
        let _ = w.set_minimizable(true);
        let _ = w.set_content_protected(false);
    }
}

/// Linux exam session: if it stops being the session on screen (a second login, another
/// console), switch straight back and report the trip once. Checked 4 times a second.
#[cfg(target_os = "linux")]
pub fn start_session_guard(app: AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(250));
        if !engaged() {
            continue;
        }
        let Ok(session) = linux::exam_session_id() else { continue };
        if linux::session_active(&session) == Some(false) {
            linux::reactivate(&session);
            left_exam(&app, Reason::ExamSession, json!({"left_exam_session": true}));
        }
    });
}

/// Windows, separate exam desktop: if the screen leaves our desktop (Ctrl+Alt+Del then
/// Task Manager, a fast user switch...), switch it straight back and, during an exam,
/// report the trip once. Checked 10 times a second.
#[cfg(windows)]
pub fn start_desktop_guard(app: AppHandle) {
    if !secure_desktop::on_secure_desktop() {
        return;
    }
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(100));
        match secure_desktop::input_desktop_name() {
            Some(name) if name != secure_desktop::DESKTOP_NAME => {
                secure_desktop::switch_to_exam_desktop();
                left_exam(&app, Reason::ExamDesktop, json!({"left_exam_desktop_for": name}));
            }
            _ => {} // on our desktop, or the Ctrl+Alt+Del / UAC screen (cannot switch away from it)
        }
    });
}

/// Is the window actually covering the whole screen? The window library only remembers
/// that we *asked* for full screen, so also compare the real size with the monitor and
/// check it is not minimised.
fn really_fullscreen(w: &tauri::WebviewWindow) -> bool {
    // macOS simple full screen is not reported by is_fullscreen(): judge by size only.
    let flag_ok = cfg!(target_os = "macos") || w.is_fullscreen().unwrap_or(true);
    if !flag_ok || w.is_minimized().unwrap_or(false) {
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
                current_session = session.clone();
            }
            let Some(session_id) = session else { continue };
            if !engaged() {
                continue;
            }
            // macOS: keep the kiosk options on (toggling full screen resets them).
            #[cfg(target_os = "macos")]
            macos::set_kiosk(&app, true);

            // 1. Environment checks (displays, VM, remote session, tools, debugger)
            let findings = scanner.scan(&app);
            let keys: HashSet<String> = findings.iter().map(|f| f.key.clone()).collect();
            for f in &findings {
                if !reported.contains(&f.key) {
                    let _ = crate::commands::report_event(&state, &session_id, f.kind, f.details.clone()).await;
                }
            }
            reported = keys; // forget what went away, so it is reported again if it comes back

            // 2. Shortcuts the keyboard hook / key filter swallowed (info only, summarised)
            if last_shortcut_report.elapsed() >= SHORTCUT_REPORT_EVERY {
                let count = take_blocked_shortcuts();
                if count > 0 {
                    last_shortcut_report = Instant::now();
                    let _ = crate::commands::report_event(&state, &session_id, "shortcut_blocked", json!({"count": count})).await;
                }
            }
        }
    });
}
