//! Windows-only lockdown pieces.
//!
//! - A low-level keyboard hook (WH_KEYBOARD_LL) that, while an exam is on screen,
//!   swallows Alt+Tab, Alt+Esc, Alt+F4, Ctrl+Esc, Ctrl+Shift+Esc, the Windows keys and
//!   Print Screen. Ctrl+Alt+Del and Win+L cannot be blocked by any app (Windows
//!   protects them); leaving the window that way is still caught as focus loss.
//! - Remote Desktop session, debugger and BIOS (VM) checks.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use windows_sys::Win32::Foundation::{GetLastError, LPARAM, LRESULT, WPARAM};
use windows_sys::Win32::System::Diagnostics::Debug::IsDebuggerPresent;
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, VK_CONTROL, VK_ESCAPE, VK_F4, VK_LWIN, VK_MENU, VK_Q, VK_RWIN, VK_SHIFT, VK_SNAPSHOT, VK_TAB,
};
use windows_sys::Win32::Foundation::HWND;
use windows_sys::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, DispatchMessageW, GetMessageW, GetSystemMetrics, GetWindowLongPtrW, SetWindowLongPtrW,
    SetWindowPos, SetWindowsHookExW, TranslateMessage, GWL_EXSTYLE, KBDLLHOOKSTRUCT, LLKHF_ALTDOWN, MSG,
    SM_REMOTESESSION, SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, WH_KEYBOARD_LL,
    WM_KEYDOWN, WM_SYSKEYDOWN, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW,
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
            || (alt && (vk == VK_TAB || vk == VK_ESCAPE || vk == VK_F4))
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
