//! macOS lockdown.
//!
//! 1. Kiosk presentation options (what Safe Exam Browser uses without Apple's assessment
//!    entitlement): Dock and menu bar hidden, app switching (Cmd+Tab) and Mission Control
//!    off, Force Quit window off, log out / restart / shut down from the Apple menu off,
//!    Hide off. Set on NSApplication; they apply while the app is in front.
//! 2. "Simple" full screen (covers the screen without a separate Space; native full screen
//!    is switched off for the window, because a full-screen Space and the kiosk options
//!    cannot be combined).
//! 3. A key filter (Quartz event tap) for the shortcuts the kiosk options leave on:
//!    Spotlight, Spaces, Notification Centre, Quick Note, screenshots... (`mac_keys.rs`).
//!    It needs the Accessibility permission (System Settings > Privacy & Security >
//!    Accessibility); the pre-exam check asks for it and refuses to start without it.
//! 4. The app menu has no Quit, Hide or Minimise items (lib.rs), so Cmd+Q never quits.
//!
//! What no app can block (lock screen from Touch ID, the power button, closing the lid)
//! is still detected as focus loss, and the window is pulled back.
//!
//! Plain C calls into the Objective-C runtime, Quartz and Core Foundation: no extra crates.

use std::ffi::{c_char, c_void, CStr};
use std::ptr::{null, null_mut};
use std::sync::atomic::{AtomicBool, AtomicPtr, AtomicU32, Ordering};
use std::time::Duration;

use tauri::{AppHandle, Manager};

use super::mac_keys;

type Id = *mut c_void;
type Sel = *mut c_void;

#[repr(C)]
struct Opaque {
    _private: [u8; 0],
}

#[link(name = "objc", kind = "dylib")]
extern "C" {
    fn objc_getClass(name: *const c_char) -> Id;
    fn sel_registerName(name: *const c_char) -> Sel;
    fn objc_msgSend();
}

#[link(name = "CoreGraphics", kind = "framework")]
extern "C" {
    fn CGEventTapCreate(
        tap: u32,
        place: u32,
        options: u32,
        events_of_interest: u64,
        callback: extern "C" fn(*mut c_void, u32, *mut c_void, *mut c_void) -> *mut c_void,
        user_info: *mut c_void,
    ) -> *mut c_void;
    fn CGEventTapEnable(tap: *mut c_void, enable: bool);
    fn CGEventGetIntegerValueField(event: *mut c_void, field: u32) -> i64;
    fn CGEventGetFlags(event: *mut c_void) -> u64;
}

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFMachPortCreateRunLoopSource(allocator: *const c_void, port: *mut c_void, order: isize) -> *mut c_void;
    fn CFRunLoopGetCurrent() -> *mut c_void;
    fn CFRunLoopAddSource(run_loop: *mut c_void, source: *mut c_void, mode: *const c_void);
    fn CFRunLoopRun();
    fn CFDictionaryCreate(
        allocator: *const c_void,
        keys: *const *const c_void,
        values: *const *const c_void,
        count: isize,
        key_callbacks: *const Opaque,
        value_callbacks: *const Opaque,
    ) -> *const c_void;
    fn CFRelease(cf: *const c_void);
    static kCFRunLoopCommonModes: *const c_void;
    static kCFBooleanTrue: *const c_void;
    static kCFTypeDictionaryKeyCallBacks: Opaque;
    static kCFTypeDictionaryValueCallBacks: Opaque;
}

#[link(name = "ApplicationServices", kind = "framework")]
extern "C" {
    fn AXIsProcessTrusted() -> bool;
    fn AXIsProcessTrustedWithOptions(options: *const c_void) -> bool;
    static kAXTrustedCheckOptionPrompt: *const c_void;
}

// ------------------------------------------------------------------ Objective-C helpers

unsafe fn sel(name: &CStr) -> Sel {
    sel_registerName(name.as_ptr())
}

unsafe fn shared_application() -> Id {
    let send: extern "C" fn(Id, Sel) -> Id = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
    send(objc_getClass(c"NSApplication".as_ptr()), sel(c"sharedApplication"))
}

unsafe fn send_usize(target: Id, selector: &CStr, value: usize) {
    let send: extern "C" fn(Id, Sel, usize) = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
    send(target, sel(selector), value)
}

unsafe fn get_usize(target: Id, selector: &CStr) -> usize {
    let send: extern "C" fn(Id, Sel) -> usize = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
    send(target, sel(selector))
}

unsafe fn send_bool(target: Id, selector: &CStr, value: bool) {
    // BOOL is a signed char on Intel and a bool on Apple silicon: 1 means YES on both.
    let send: extern "C" fn(Id, Sel, i8) = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
    send(target, sel(selector), value as i8)
}

// NSApplicationPresentationOptions
const HIDE_DOCK: usize = 1 << 1;
const HIDE_MENU_BAR: usize = 1 << 3;
const DISABLE_APPLE_MENU: usize = 1 << 4;
const DISABLE_PROCESS_SWITCHING: usize = 1 << 5; // Cmd+Tab and Mission Control / Exposé
const DISABLE_FORCE_QUIT: usize = 1 << 6;
const DISABLE_SESSION_TERMINATION: usize = 1 << 7;
const DISABLE_HIDE_APPLICATION: usize = 1 << 8;
const DISABLE_MENU_BAR_TRANSPARENCY: usize = 1 << 9;
/// Valid together: process switching needs the Dock hidden, the Apple menu needs the menu
/// bar hidden (AppKit raises an exception for invalid combinations).
const KIOSK: usize = HIDE_DOCK
    | HIDE_MENU_BAR
    | DISABLE_APPLE_MENU
    | DISABLE_PROCESS_SWITCHING
    | DISABLE_FORCE_QUIT
    | DISABLE_SESSION_TERMINATION
    | DISABLE_HIDE_APPLICATION
    | DISABLE_MENU_BAR_TRANSPARENCY;

// NSWindowCollectionBehavior
const CAN_JOIN_ALL_SPACES: usize = 1 << 0;
const MOVE_TO_ACTIVE_SPACE: usize = 1 << 1;
const STATIONARY: usize = 1 << 4;
const FULL_SCREEN_PRIMARY: usize = 1 << 7;
const FULL_SCREEN_AUXILIARY: usize = 1 << 8;
const FULL_SCREEN_NONE: usize = 1 << 9;

/// Turn the kiosk options on or off (on the main thread, as AppKit requires).
pub fn set_kiosk(app: &AppHandle, on: bool) {
    let _ = app.run_on_main_thread(move || unsafe {
        let ns_app = shared_application();
        send_usize(ns_app, c"setPresentationOptions:", if on { KIOSK } else { 0 });
        if on {
            send_bool(ns_app, c"activateIgnoringOtherApps:", true);
        }
    });
}

/// Bring the app back to the front (after a gesture or a click on another app's window).
pub fn activate(app: &AppHandle) {
    let _ = app.run_on_main_thread(|| unsafe {
        send_bool(shared_application(), c"activateIgnoringOtherApps:", true);
    });
}

/// The window can never enter a native full-screen Space (green button, Ctrl+Cmd+F): the
/// exam uses simple full screen, and a Space cannot carry the kiosk options.
pub fn disable_native_fullscreen(window: &tauri::WebviewWindow) {
    let Ok(ns_window) = window.ns_window() else { return };
    let ns_window = ns_window as usize;
    let _ = window.run_on_main_thread(move || unsafe {
        let ns_window = ns_window as Id;
        let behavior = get_usize(ns_window, c"collectionBehavior");
        let behavior = (behavior & !(FULL_SCREEN_PRIMARY | FULL_SCREEN_AUXILIARY)) | FULL_SCREEN_NONE;
        send_usize(ns_window, c"setCollectionBehavior:", behavior);
    });
}

/// During an exam the window is on every Space and stays put in Mission Control, so a
/// swipe to another Space still shows the exam (like "all virtual desktops" on Windows);
/// the guard then gives it the focus back at once.
pub fn set_all_spaces(window: &tauri::WebviewWindow, on: bool) {
    let Ok(ns_window) = window.ns_window() else { return };
    let ns_window = ns_window as usize;
    let _ = window.run_on_main_thread(move || unsafe {
        let ns_window = ns_window as Id;
        let behavior = get_usize(ns_window, c"collectionBehavior") & !MOVE_TO_ACTIVE_SPACE;
        let behavior = if on {
            behavior | CAN_JOIN_ALL_SPACES | STATIONARY
        } else {
            behavior & !(CAN_JOIN_ALL_SPACES | STATIONARY)
        };
        send_usize(ns_window, c"setCollectionBehavior:", behavior);
    });
}

// ------------------------------------------------------------------------- key filter

static BLOCKING: AtomicBool = AtomicBool::new(false);
static BLOCKED_COUNT: AtomicU32 = AtomicU32::new(0);
static TAP: AtomicPtr<c_void> = AtomicPtr::new(null_mut());
static TAP_RUNNING: AtomicBool = AtomicBool::new(false);
static TAP_STARTING: AtomicBool = AtomicBool::new(false);

const SESSION_EVENT_TAP: u32 = 1; // kCGSessionEventTap
const HEAD_INSERT: u32 = 0; // kCGHeadInsertEventTap
const TAP_DEFAULT: u32 = 0; // kCGEventTapOptionDefault: may drop events
const KEY_DOWN: u32 = 10;
const KEY_UP: u32 = 11;
/// Trackpad swipes and pinches that the Dock turns into Mission Control, App Exposé,
/// switching Spaces, Launchpad and Show Desktop arrive as "dock control" events
/// (kCGSEventDockControl, not in the public headers). Swallowing them while an exam is on
/// screen stops those gestures; scrolling, clicking and pointing are other event types and
/// are not touched. If a macOS version sends them differently, the guard still pulls the
/// student straight back.
const DOCK_CONTROL: u32 = 30;
const TAP_DISABLED_BY_TIMEOUT: u32 = 0xFFFF_FFFE;
const TAP_DISABLED_BY_USER_INPUT: u32 = 0xFFFF_FFFF;
const KEYBOARD_EVENT_KEYCODE: u32 = 9; // kCGKeyboardEventKeycode

pub fn set_blocking(on: bool) {
    BLOCKING.store(on, Ordering::SeqCst);
}

/// Shortcuts swallowed since the last call.
pub fn take_blocked_count() -> u32 {
    BLOCKED_COUNT.swap(0, Ordering::SeqCst)
}

extern "C" fn on_key(_proxy: *mut c_void, event_type: u32, event: *mut c_void, _user: *mut c_void) -> *mut c_void {
    if event_type == TAP_DISABLED_BY_TIMEOUT || event_type == TAP_DISABLED_BY_USER_INPUT {
        // macOS switches a slow tap off; switch it straight back on.
        let tap = TAP.load(Ordering::SeqCst);
        if !tap.is_null() {
            unsafe { CGEventTapEnable(tap, true) };
        }
        return event;
    }
    if !BLOCKING.load(Ordering::Relaxed) {
        return event;
    }
    if event_type == DOCK_CONTROL {
        return null_mut();
    }
    if event_type != KEY_DOWN && event_type != KEY_UP {
        return event;
    }
    let (keycode, flags) = unsafe {
        (CGEventGetIntegerValueField(event, KEYBOARD_EVENT_KEYCODE) as u16, CGEventGetFlags(event))
    };
    if mac_keys::should_block(keycode, flags) {
        if event_type == KEY_DOWN {
            BLOCKED_COUNT.fetch_add(1, Ordering::SeqCst);
        }
        return null_mut(); // swallow it
    }
    event
}

/// Has the student allowed UAI Aegis under Accessibility?
pub fn accessibility_allowed() -> bool {
    unsafe { AXIsProcessTrusted() }
}

/// Show macOS's own "allow in Accessibility" prompt (adds the app to the list).
pub fn request_accessibility() {
    unsafe {
        let keys = [kAXTrustedCheckOptionPrompt];
        let values = [kCFBooleanTrue];
        let options = CFDictionaryCreate(
            null(),
            keys.as_ptr(),
            values.as_ptr(),
            1,
            &kCFTypeDictionaryKeyCallBacks,
            &kCFTypeDictionaryValueCallBacks,
        );
        if !options.is_null() {
            AXIsProcessTrustedWithOptions(options);
            CFRelease(options);
        }
    }
}

/// Start the key filter (once). False if macOS refused (no Accessibility permission yet,
/// or the permission was just given and the app must be restarted).
pub fn start_key_filter() -> bool {
    if TAP_RUNNING.load(Ordering::SeqCst) {
        return true;
    }
    if !accessibility_allowed() || TAP_STARTING.swap(true, Ordering::SeqCst) {
        return false;
    }
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || unsafe {
        let mask = (1u64 << KEY_DOWN) | (1u64 << KEY_UP) | (1u64 << DOCK_CONTROL);
        let tap = CGEventTapCreate(SESSION_EVENT_TAP, HEAD_INSERT, TAP_DEFAULT, mask, on_key, null_mut());
        if tap.is_null() {
            let _ = tx.send(false);
            return;
        }
        TAP.store(tap, Ordering::SeqCst);
        let source = CFMachPortCreateRunLoopSource(null(), tap, 0);
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes);
        CGEventTapEnable(tap, true);
        TAP_RUNNING.store(true, Ordering::SeqCst);
        let _ = tx.send(true);
        CFRunLoopRun(); // runs for the life of the app
    });
    let started = rx.recv_timeout(Duration::from_secs(3)).unwrap_or(false);
    TAP_STARTING.store(false, Ordering::SeqCst);
    started
}

/// The key filter is running and still allowed (the permission can be revoked later).
pub fn key_filter_active() -> bool {
    TAP_RUNNING.load(Ordering::SeqCst) && accessibility_allowed()
}

/// Called once at launch: lock native full screen out, start the filter if already allowed.
pub fn setup(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        disable_native_fullscreen(&w);
    }
    if !super::disabled_for_dev() {
        start_key_filter();
    }
}
