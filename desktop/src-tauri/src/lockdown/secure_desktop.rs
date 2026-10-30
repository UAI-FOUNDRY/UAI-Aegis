//! Windows: run the whole app on its own Win32 desktop (like Safe Exam Browser).
//!
//! A separate desktop (the same mechanism as the UAC prompt and the Ctrl+Alt+Del screen)
//! has no Explorer shell: no taskbar, no Start menu, no Alt+Tab thumbnails of other apps,
//! no touchpad gestures or virtual desktops, and no other app's windows. This is NOT a
//! virtual desktop.
//!
//! Two processes:
//! - The **launcher** (what the student starts) creates the desktop, starts a copy of the
//!   app on it, switches the screen to it once the copy is ready, waits for it to exit and
//!   then switches the screen back. It never shows a window.
//! - The **exam app** (the copy) is the normal app. It tells the launcher it is ready,
//!   watches the launcher (if the launcher is killed, it switches the screen back and
//!   exits), and pulls the screen back if the student escapes to the normal desktop.
//!
//! Fail-safe: if the desktop cannot be created, or the copy does not report ready within
//! 30 s, the launcher kills it, stays on the normal desktop and runs the app there.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;

use windows_sys::Win32::Foundation::{CloseHandle, GENERIC_ALL, HANDLE, WAIT_OBJECT_0};
use windows_sys::Win32::System::StationsAndDesktops::{
    CloseDesktop, CreateDesktopW, GetUserObjectInformationW, OpenDesktopW, OpenInputDesktop, SwitchDesktop,
    DESKTOP_READOBJECTS, DESKTOP_SWITCHDESKTOP, HDESK, UOI_NAME,
};
use windows_sys::Win32::System::Threading::{
    CreateEventW, CreateProcessW, GetExitCodeProcess, OpenEventW, OpenProcess, SetEvent, TerminateProcess,
    WaitForMultipleObjects, WaitForSingleObject, EVENT_MODIFY_STATE, INFINITE, PROCESS_INFORMATION,
    PROCESS_SYNCHRONIZE, STARTUPINFOW,
};

pub const DESKTOP_NAME: &str = "UAIAegisExam";
const CHILD_FLAG: &str = "--aegis-secure-desktop";
const PARENT_ARG: &str = "--aegis-parent=";
const READY_ARG: &str = "--aegis-ready=";
const READY_TIMEOUT_MS: u32 = 30_000;

static ON_SECURE_DESKTOP: AtomicBool = AtomicBool::new(false);

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn arg_value(prefix: &str) -> Option<String> {
    std::env::args().find_map(|a| a.strip_prefix(prefix).map(str::to_string))
}

/// True in the exam app running on the separate desktop.
pub fn on_secure_desktop() -> bool {
    ON_SECURE_DESKTOP.load(Ordering::SeqCst)
}

fn log(msg: &str) {
    if cfg!(debug_assertions) {
        eprintln!("[aegis] {msg}");
    }
}

/// Switch the screen back to the normal Windows desktop.
pub fn switch_to_default() {
    unsafe {
        let name = wide("Default");
        let desk = OpenDesktopW(name.as_ptr(), 0, 0, DESKTOP_SWITCHDESKTOP);
        if !desk.is_null() {
            SwitchDesktop(desk);
            CloseDesktop(desk);
        }
    }
}

/// Name of the desktop currently on screen; None while the Ctrl+Alt+Del / UAC screen
/// is showing (those cannot be opened).
pub fn input_desktop_name() -> Option<String> {
    unsafe {
        let desk = OpenInputDesktop(0, 0, DESKTOP_READOBJECTS);
        if desk.is_null() {
            return None;
        }
        let mut buf = [0u16; 128];
        let mut needed = 0u32;
        let ok = GetUserObjectInformationW(
            desk as HANDLE,
            UOI_NAME,
            buf.as_mut_ptr().cast(),
            (buf.len() * 2) as u32,
            &mut needed,
        );
        CloseDesktop(desk);
        if ok == 0 {
            return None;
        }
        let len = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
        Some(String::from_utf16_lossy(&buf[..len]))
    }
}

/// Bring our desktop back on screen (the student got out, e.g. via Ctrl+Alt+Del).
pub fn switch_to_exam_desktop() -> bool {
    unsafe {
        let name = wide(DESKTOP_NAME);
        let desk = OpenDesktopW(name.as_ptr(), 0, 0, DESKTOP_SWITCHDESKTOP);
        if desk.is_null() {
            return false;
        }
        let ok = SwitchDesktop(desk) != 0;
        CloseDesktop(desk);
        ok
    }
}

// ---------------------------------------------------------------- Ctrl+Alt+Del screen
//
// Task Manager opened from Ctrl+Alt+Del appears ON the exam desktop and runs with higher
// privileges than us, so the app could not close it. Instead, while the app runs, the
// per-user policies Windows itself offers are switched on (no admin rights needed; Safe
// Exam Browser does the same): Task Manager, Lock and Change password are disabled.
// The student's previous values are saved first and put back on exit, and again on the
// next start if the app ever crashed before restoring them.

const POLICY_KEY: &str = "Software\\Microsoft\\Windows\\CurrentVersion\\Policies\\System";
const SAVED_KEY: &str = "Software\\UAIAegis\\SavedPolicies";
const POLICIES: [&str; 3] = ["DisableTaskMgr", "DisableLockWorkstation", "DisableChangePassword"];

/// Put back whatever the student had before (no-op if nothing was saved).
pub fn restore_policies() {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let Ok(saved) = hkcu.open_subkey(SAVED_KEY) else { return };
    let Ok((policy, _)) = hkcu.create_subkey(POLICY_KEY) else { return };
    for name in POLICIES {
        let absent: u32 = saved.get_value(format!("{name}_absent")).unwrap_or(0);
        if absent == 1 {
            let _ = policy.delete_value(name);
        } else if let Ok(old) = saved.get_value::<u32, _>(name) {
            let _ = policy.set_value(name, &old);
        }
    }
    drop(saved);
    let _ = hkcu.delete_subkey_all(SAVED_KEY);
    log("Ctrl+Alt+Del options restored");
}

fn apply_policies() {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let Ok((policy, _)) = hkcu.create_subkey(POLICY_KEY) else { return };
    let Ok((saved, _)) = hkcu.create_subkey(SAVED_KEY) else { return };
    for name in POLICIES {
        match policy.get_value::<u32, _>(name) {
            Ok(old) => {
                let _ = saved.set_value(name, &old);
            }
            Err(_) => {
                let _ = saved.set_value(format!("{name}_absent"), &1u32);
            }
        }
        let _ = policy.set_value(name, &1u32);
    }
    log("Task Manager, Lock and Change password disabled for the exam");
}

/// Call first thing in `main()`.
/// - In the launcher: runs the whole exam app on the separate desktop and returns its exit
///   code (main should exit with it), or `None` to fall back to running normally here.
/// - In the exam app: returns `None` (carry on starting the app).
pub fn run_launcher_if_needed() -> Option<i32> {
    if std::env::args().any(|a| a == CHILD_FLAG) {
        ON_SECURE_DESKTOP.store(true, Ordering::SeqCst);
        return None;
    }
    if crate::lockdown::disabled_for_dev()
        || (cfg!(debug_assertions) && std::env::var("AEGIS_SECURE_DESKTOP").map(|v| v == "off").unwrap_or(false))
    {
        return None;
    }
    unsafe { launch() }
}

unsafe fn launch() -> Option<i32> {
    restore_policies(); // left over from a crash, if any
    let desk_name = wide(DESKTOP_NAME);
    let desk: HDESK = CreateDesktopW(desk_name.as_ptr(), std::ptr::null(), std::ptr::null(), 0, GENERIC_ALL, std::ptr::null());
    if desk.is_null() {
        log("could not create the exam desktop; running on the normal desktop");
        return None;
    }

    // A fresh "ready" event per launch, so an old one can never count.
    let ready_name = format!("Local\\UAIAegisReady-{}", std::process::id());
    let ready_w = wide(&ready_name);
    let ready = CreateEventW(std::ptr::null(), 1, 0, ready_w.as_ptr());
    if ready.is_null() {
        CloseDesktop(desk);
        return None;
    }

    let exe = match std::env::current_exe() {
        Ok(p) => p,
        Err(_) => {
            CloseHandle(ready);
            CloseDesktop(desk);
            return None;
        }
    };
    let mut cmd = format!("\"{}\"", exe.display());
    for a in std::env::args().skip(1) {
        cmd.push_str(&format!(" \"{}\"", a.replace('"', "")));
    }
    cmd.push_str(&format!(" {CHILD_FLAG} {PARENT_ARG}{} {READY_ARG}{ready_name}", std::process::id()));
    let mut cmd_w = wide(&cmd);

    let mut desktop_path = wide(&format!("WinSta0\\{DESKTOP_NAME}"));
    let mut si: STARTUPINFOW = std::mem::zeroed();
    si.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
    si.lpDesktop = desktop_path.as_mut_ptr();
    let mut pi: PROCESS_INFORMATION = std::mem::zeroed();

    // Inherit handles so the copy writes to the same console in development.
    let started = CreateProcessW(
        std::ptr::null(),
        cmd_w.as_mut_ptr(),
        std::ptr::null(),
        std::ptr::null(),
        1,
        0,
        std::ptr::null(),
        std::ptr::null(),
        &si,
        &mut pi,
    );
    if started == 0 {
        log("could not start the app on the exam desktop; running on the normal desktop");
        CloseHandle(ready);
        CloseDesktop(desk);
        return None;
    }
    CloseHandle(pi.hThread);

    // Switch the screen only once the copy has its window, so nobody sees a blank screen.
    let handles = [ready, pi.hProcess];
    let waited = WaitForMultipleObjects(2, handles.as_ptr(), 0, READY_TIMEOUT_MS);
    if waited != WAIT_OBJECT_0 {
        log("exam desktop app did not start in time; running on the normal desktop");
        TerminateProcess(pi.hProcess, 1);
        CloseHandle(pi.hProcess);
        CloseHandle(ready);
        CloseDesktop(desk);
        return None;
    }
    apply_policies();
    log("switching to the exam desktop");
    SwitchDesktop(desk);

    WaitForSingleObject(pi.hProcess, INFINITE);
    switch_to_default();
    restore_policies();
    log("back on the normal desktop");

    let mut code = 0u32;
    GetExitCodeProcess(pi.hProcess, &mut code);
    CloseHandle(pi.hProcess);
    CloseHandle(ready);
    CloseDesktop(desk);
    Some(code as i32)
}

/// In the exam app, once its window exists: tell the launcher to switch the screen, and
/// make sure the screen comes back if the launcher disappears or the app panics.
pub fn child_ready() {
    if !on_secure_desktop() {
        return;
    }
    static DONE: OnceLock<()> = OnceLock::new();
    if DONE.set(()).is_err() {
        return;
    }

    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        switch_to_default();
        restore_policies();
        previous(info);
    }));

    if let Some(name) = arg_value(READY_ARG) {
        let name_w = wide(&name);
        unsafe {
            let ev = OpenEventW(EVENT_MODIFY_STATE, 0, name_w.as_ptr());
            if !ev.is_null() {
                SetEvent(ev);
                CloseHandle(ev);
            }
        }
    }

    if let Some(pid) = arg_value(PARENT_ARG).and_then(|p| p.parse::<u32>().ok()) {
        std::thread::spawn(move || unsafe {
            let parent = OpenProcess(PROCESS_SYNCHRONIZE, 0, pid);
            if parent.is_null() {
                return;
            }
            WaitForSingleObject(parent, INFINITE);
            CloseHandle(parent);
            // Launcher gone (closed, killed, dev rebuild): never leave the screen stuck here,
            // and give the student their Ctrl+Alt+Del options back.
            switch_to_default();
            restore_policies();
            std::process::exit(0);
        });
    }
}

/// Leave the exam desktop for good (development escape hatch).
pub fn leave_and_exit() -> ! {
    switch_to_default();
    restore_policies();
    std::process::exit(0);
}
