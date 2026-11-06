//! Windows-only lockdown pieces.
//!
//! - A low-level keyboard hook (WH_KEYBOARD_LL) that, while an exam is on screen,
//!   swallows Alt+Tab, Alt+Esc, Alt+F4, Alt+Space, Ctrl+Esc, Ctrl+Shift+Esc, the Windows
//!   keys (so every Win+... shortcut: Win+Tab, Win+D, Win+Ctrl+Left/Right for virtual
//!   desktops, Win+R...) and Print Screen. Ctrl+Alt+Del and Win+L cannot be blocked by any
//!   app (Windows protects them): Task Manager and Lock are switched off by policy
//!   (secure_desktop.rs), and anything else is caught by the guard and pulled back.
//! - The accessibility shortcuts (Shift five times for Sticky Keys, holding right Shift
//!   for Filter Keys, holding Num Lock for Toggle Keys) are switched off during the exam:
//!   their pop-ups link to Settings, a well-known way out of kiosk apps.
//! - Forcing the exam window back to the foreground.
//! - Remote Desktop session, debugger and BIOS (VM) checks.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Mutex;

use windows_sys::Win32::Foundation::{GetLastError, LPARAM, LRESULT, WPARAM};
use windows_sys::Win32::System::Diagnostics::Debug::IsDebuggerPresent;
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, VK_CONTROL, VK_ESCAPE, VK_F4, VK_LWIN, VK_MENU, VK_Q, VK_RWIN, VK_SHIFT, VK_SNAPSHOT, VK_SPACE,
    VK_TAB,
};
use windows_sys::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId};
use windows_sys::Win32::Foundation::HWND;
use windows_sys::Win32::UI::WindowsAndMessaging::{
    BringWindowToTop, CallNextHookEx, DispatchMessageW, GetForegroundWindow, GetMessageW, GetSystemMetrics,
    GetWindowLongPtrW, GetWindowThreadProcessId, IsIconic, SetForegroundWindow, SetWindowLongPtrW, SetWindowPos,
    SetWindowsHookExW, ShowWindow, SystemParametersInfoW, TranslateMessage, GWL_EXSTYLE, KBDLLHOOKSTRUCT,
    LLKHF_ALTDOWN, MSG, SM_REMOTESESSION, SPI_GETFILTERKEYS, SPI_GETSTICKYKEYS, SPI_GETTOGGLEKEYS,
    SPI_SETFILTERKEYS, SPI_SETSTICKYKEYS, SPI_SETTOGGLEKEYS, SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_NOMOVE,
    SWP_NOSIZE, SWP_NOZORDER, SW_RESTORE, WH_KEYBOARD_LL, WM_KEYDOWN, WM_SYSKEYDOWN, WS_EX_APPWINDOW,
    WS_EX_TOOLWINDOW,
};

static BLOCKING: AtomicBool = AtomicBool::new(false);
static BLOCKED_COUNT: AtomicU32 = AtomicU32::new(0);
static DEV_ESCAPE: AtomicBool = AtomicBool::new(false);
static HOOK_STARTED: AtomicBool = AtomicBool::new(false);

pub fn set_blocking(on: bool) {
    BLOCKING.store(on, Ordering::SeqCst);
    if cfg!(debug_assertions) {
        eprintln!("[aegis] shortcut blocking {}", if on { "ON" } else { "off" });
    }
}

/// Shortcuts swallowed since the last call.
pub fn take_blocked_count() -> u32 {
    BLOCKED_COUNT.swap(0, Ordering::SeqCst)
}

/// Development builds: Ctrl+Alt+Shift+Q turns the lockdown off for the rest of the run.
pub fn dev_escape_used() -> bool {
    DEV_ESCAPE.load(Ordering::SeqCst)
}

fn key_down(vk: u16) -> bool {
    // SAFETY: plain Win32 call; high bit set = key is down.
    (unsafe { GetAsyncKeyState(vk as i32) } as u16) & 0x8000 != 0
}

unsafe extern "system" fn keyboard_hook(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    // code == 0 (HC_ACTION): lparam points to a KBDLLHOOKSTRUCT.
    if code == 0 && BLOCKING.load(Ordering::Relaxed) {
        let info = &*(lparam as *const KBDLLHOOKSTRUCT);
        let vk = info.vkCode as u16;
        let msg = wparam as u32;
        let is_down = msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN;
        let alt = info.flags & LLKHF_ALTDOWN != 0 || key_down(VK_MENU);
        let ctrl = key_down(VK_CONTROL);
        let shift = key_down(VK_SHIFT);

        if cfg!(debug_assertions) && is_down && ctrl && alt && shift && vk == VK_Q {
            DEV_ESCAPE.store(true, Ordering::SeqCst);
            BLOCKING.store(false, Ordering::SeqCst);
            return CallNextHookEx(std::ptr::null_mut(), code, wparam, lparam);
        }

        let block = vk == VK_LWIN
            || vk == VK_RWIN
            || vk == VK_SNAPSHOT
            // Alt+Space opens the window menu (Move, Minimise, Close)
            || (alt && (vk == VK_TAB || vk == VK_ESCAPE || vk == VK_F4 || vk == VK_SPACE))
            || (ctrl && vk == VK_ESCAPE); // Ctrl+Esc (Start) and Ctrl+Shift+Esc (Task Manager)
        if block {
            if is_down {
                BLOCKED_COUNT.fetch_add(1, Ordering::SeqCst);
                if cfg!(debug_assertions) {
                    eprintln!("[aegis] blocked key 0x{vk:02x} (alt={alt} ctrl={ctrl})");
                }
            }
            return 1; // swallow the key
        }
    }
    CallNextHookEx(std::ptr::null_mut(), code, wparam, lparam)
}

/// Install the hook on its own thread with a message loop (Windows calls the hook
/// on the installing thread). Installed once at launch; it does nothing until
/// `set_blocking(true)`.
pub fn start_keyboard_hook() {
    if HOOK_STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    std::thread::spawn(|| unsafe {
        let module = GetModuleHandleW(std::ptr::null());
        let hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_hook), module, 0);
        if hook.is_null() {
            eprintln!("[aegis] keyboard hook FAILED to install (error {})", GetLastError());
            return;
        }
        eprintln!("[aegis] keyboard hook installed");
        let mut msg: MSG = std::mem::zeroed();
        while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    });
}

/// Tool windows are shown on every virtual desktop and are left out of Alt+Tab, so a
/// touchpad swipe to another desktop (which no app can block) still lands on the exam.
/// Call while the window is hidden; the shell re-reads the style when it is shown again.
pub fn show_on_all_desktops(hwnd: HWND, on: bool) {
    unsafe {
        let ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
        let tool = WS_EX_TOOLWINDOW as isize;
        let app = WS_EX_APPWINDOW as isize;
        let new = if on { (ex | tool) & !app } else { (ex & !tool) | app };
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new);
        SetWindowPos(
            hwnd,
            std::ptr::null_mut(),
            0,
            0,
            0,
            0,
            SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        );
    }
    if cfg!(debug_assertions) {
        eprintln!("[aegis] all virtual desktops {}", if on { "ON" } else { "off" });
    }
}

/// Bring the exam window to the foreground. Windows refuses `SetForegroundWindow` from an
/// app that is not in front (it flashes the taskbar button instead); attaching to the
/// input of the window that is in front lifts that restriction for this call.
pub fn force_foreground(hwnd: HWND) {
    unsafe {
        let front = GetForegroundWindow();
        if front == hwnd {
            return;
        }
        let ours = GetCurrentThreadId();
        let theirs = if front.is_null() { 0 } else { GetWindowThreadProcessId(front, std::ptr::null_mut()) };
        let attached = theirs != 0 && theirs != ours && AttachThreadInput(ours, theirs, 1) != 0;
        if IsIconic(hwnd) != 0 {
            ShowWindow(hwnd, SW_RESTORE);
        }
        BringWindowToTop(hwnd);
        SetForegroundWindow(hwnd);
        if attached {
            AttachThreadInput(ours, theirs, 0);
        }
    }
}

// --------------------------------------------------------------- accessibility shortcuts
//
// Only the *shortcuts* are switched off: a student who already has Sticky Keys or Filter
// Keys turned on keeps them. The change is not written to the profile (fWinIni = 0), so
// even if the app crashed it would be gone at the next sign-in; normally the saved
// values are put back when the exam ends.

/// STICKYKEYS and TOGGLEKEYS: size + flags.
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct FlagsOnly {
    cb_size: u32,
    flags: u32,
}

/// FILTERKEYS.
#[repr(C)]
#[derive(Clone, Copy, Default)]
struct FilterKeys {
    cb_size: u32,
    flags: u32,
    wait_ms: u32,
    delay_ms: u32,
    repeat_ms: u32,
    bounce_ms: u32,
}

/// SKF_/FKF_/TKF_HOTKEYACTIVE and ..._CONFIRMHOTKEY (the same bits in all three).
const HOTKEY_ACTIVE: u32 = 0x4;
const CONFIRM_HOTKEY: u32 = 0x8;

static SAVED_ACCESSIBILITY: Mutex<Option<(FlagsOnly, FilterKeys, FlagsOnly)>> = Mutex::new(None);

unsafe fn spi<T>(action: u32, value: &mut T) -> bool {
    SystemParametersInfoW(action, std::mem::size_of::<T>() as u32, (value as *mut T).cast(), 0) != 0
}

pub fn disable_accessibility_hotkeys() {
    unsafe {
        let mut sticky = FlagsOnly { cb_size: std::mem::size_of::<FlagsOnly>() as u32, ..Default::default() };
        let mut filter = FilterKeys { cb_size: std::mem::size_of::<FilterKeys>() as u32, ..Default::default() };
        let mut toggle = FlagsOnly { cb_size: std::mem::size_of::<FlagsOnly>() as u32, ..Default::default() };
        if !(spi(SPI_GETSTICKYKEYS, &mut sticky) && spi(SPI_GETFILTERKEYS, &mut filter) && spi(SPI_GETTOGGLEKEYS, &mut toggle)) {
            return;
        }
        let Ok(mut saved) = SAVED_ACCESSIBILITY.lock() else { return };
        if saved.is_none() {
            *saved = Some((sticky, filter, toggle));
        }
        sticky.flags &= !(HOTKEY_ACTIVE | CONFIRM_HOTKEY);
        filter.flags &= !(HOTKEY_ACTIVE | CONFIRM_HOTKEY);
        toggle.flags &= !(HOTKEY_ACTIVE | CONFIRM_HOTKEY);
        spi(SPI_SETSTICKYKEYS, &mut sticky);
        spi(SPI_SETFILTERKEYS, &mut filter);
        spi(SPI_SETTOGGLEKEYS, &mut toggle);
    }
    if cfg!(debug_assertions) {
        eprintln!("[aegis] accessibility shortcuts off");
    }
}

pub fn restore_accessibility_hotkeys() {
    let Some((mut sticky, mut filter, mut toggle)) = SAVED_ACCESSIBILITY.lock().ok().and_then(|mut s| s.take()) else {
        return;
    };
    unsafe {
        spi(SPI_SETSTICKYKEYS, &mut sticky);
        spi(SPI_SETFILTERKEYS, &mut filter);
        spi(SPI_SETTOGGLEKEYS, &mut toggle);
    }
}

/// Running inside a Remote Desktop (RDP) session.
pub fn remote_session() -> bool {
    unsafe { GetSystemMetrics(SM_REMOTESESSION) != 0 }
}

pub fn debugger_present() -> bool {
    unsafe { IsDebuggerPresent() != 0 }
}

/// BIOS manufacturer and product name, e.g. "Dell Inc. Latitude 5420" or
/// "innotek GmbH VirtualBox".
pub fn bios_strings() -> Option<String> {
    use winreg::enums::HKEY_LOCAL_MACHINE;
    use winreg::RegKey;
    let bios = RegKey::predef(HKEY_LOCAL_MACHINE)
        .open_subkey("HARDWARE\\DESCRIPTION\\System\\BIOS")
        .ok()?;
    let maker: String = bios.get_value("SystemManufacturer").unwrap_or_default();
    let product: String = bios.get_value("SystemProductName").unwrap_or_default();
    Some(format!("{maker} {product}"))
}
