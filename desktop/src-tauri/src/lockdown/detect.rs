//! What the app checks before Start and every 3 s during an exam.
//! Each finding maps to an event kind the server knows (backend proctoring.py).

use std::sync::OnceLock;

use serde::Serialize;
use serde_json::{json, Value};
use sysinfo::{ProcessRefreshKind, ProcessesToUpdate, System};
use tauri::AppHandle;

#[derive(Debug, Clone, Serialize)]
pub struct Finding {
    /// Event kind sent to the server, e.g. `blocked_app_detected`.
    pub kind: &'static str,
    /// Identity of this finding, so it is reported once (e.g. `app:anydesk`).
    #[serde(skip)]
    pub key: String,
    /// Shown to the student before Start ("Close AnyDesk").
    pub message: String,
    #[serde(skip)]
    pub details: Value,
}

/// Tools that let someone else see or control the screen, or record it.
/// Process names, lower case, without `.exe`. A starting list: it should later come
/// from the server so admins can change it without an app update.
const REMOTE_CONTROL: &[(&str, &str)] = &[
    ("anydesk", "AnyDesk"),
    ("teamviewer", "TeamViewer"),
    ("rustdesk", "RustDesk"),
    ("ultraviewer_desktop", "UltraViewer"),
    ("parsecd", "Parsec"),
    ("remoting_host", "Chrome Remote Desktop"),
    ("vncviewer", "VNC Viewer"),
    ("winvnc", "VNC Server"),
    ("tvnserver", "TightVNC"),
    ("srmanager", "Splashtop"),
];
const SCREEN_SHARING: &[(&str, &str)] = &[
    ("discord", "Discord"),
    ("zoom", "Zoom"),
    ("ms-teams", "Microsoft Teams"),
    ("teams", "Microsoft Teams"),
    ("skype", "Skype"),
    ("chatgpt", "ChatGPT desktop app"),
];
const RECORDERS: &[(&str, &str)] = &[
    ("obs64", "OBS Studio"),
    ("obs32", "OBS Studio"),
    ("obs", "OBS Studio"),
    ("bandicam", "Bandicam"),
    ("camtasiastudio", "Camtasia"),
    ("camrecorder", "Camtasia Recorder"),
    ("sharex", "ShareX"),
    ("screenrec", "ScreenRec"),
    ("loom", "Loom"),
];

pub struct Scanner {
    sys: System,
}

impl Scanner {
    pub fn new() -> Self {
        Self { sys: System::new() }
    }

    pub fn scan(&mut self, app: &AppHandle) -> Vec<Finding> {
        let mut out = Vec::new();

        // Extra displays: a second screen can show the exam to someone else.
        let displays = app.available_monitors().map(|m| m.len()).unwrap_or(1);
        if displays > 1 {
            out.push(Finding {
                kind: "display_added",
                key: "displays".into(),
                message: format!("Disconnect extra screens ({displays} detected). Only one screen is allowed."),
                details: json!({"displays": displays}),
            });
        }

        if let Some(vm) = vm_name() {
            out.push(Finding {
                kind: "vm_detected",
                key: "vm".into(),
                message: format!("This app can't run inside a virtual machine ({vm})."),
                details: json!({"vm": vm}),
            });
        }

        #[cfg(windows)]
        if super::win32::remote_session() {
            out.push(Finding {
                kind: "remote_desktop_detected",
                key: "remote_session".into(),
                message: "This is a Remote Desktop session. Sit at the laptop itself.".into(),
                details: json!({"source": "rdp"}),
            });
        }

        #[cfg(all(windows, not(debug_assertions)))]
        if super::win32::debugger_present() {
            out.push(Finding {
                kind: "debugger_detected",
                key: "debugger".into(),
                message: "Close the debugger attached to this app.".into(),
                details: json!({}),
            });
        }

        // Running tools (names only; nothing else about the processes is read).
        self.sys.refresh_processes_specifics(ProcessesToUpdate::All, true, ProcessRefreshKind::nothing());
        let mut seen = std::collections::BTreeSet::new();
        for p in self.sys.processes().values() {
            let name = p.name().to_string_lossy().to_lowercase();
            let name = name.strip_suffix(".exe").unwrap_or(&name).to_string();
            seen.insert(name);
        }
        for (list, kind) in [
            (REMOTE_CONTROL, "remote_desktop_detected"),
            (SCREEN_SHARING, "blocked_app_detected"),
            (RECORDERS, "screen_capture_detected"),
        ] {
            for (proc_name, label) in list {
                if seen.contains(*proc_name) {
                    out.push(Finding {
                        kind,
                        key: format!("app:{proc_name}"),
                        message: format!("Close {label} (check the system tray too)."),
                        details: json!({"app": label, "process": proc_name}),
                    });
                }
            }
        }
        // The same app can match twice (e.g. "teams" and "ms-teams"): keep one message each.
        out.dedup_by(|a, b| a.message == b.message);
        out
    }
}

/// Name of the virtual machine we are running in, if any. Checked once.
pub fn vm_name() -> Option<String> {
    static VM: OnceLock<Option<String>> = OnceLock::new();
    if cfg!(debug_assertions) && std::env::var("AEGIS_ALLOW_VM").is_ok() {
        return None; // development builds only: for testing inside a VM
    }
    VM.get_or_init(|| hypervisor_vendor().or_else(firmware_vm)).clone()
}

/// CPUID: the hypervisor bit plus the vendor string of known VM products.
/// "Microsoft Hv" is ignored here: Windows 11 runs on Hyper-V itself when memory
/// integrity / VBS is on, so it is checked through the firmware strings instead.
#[allow(unused_unsafe)] // __cpuid is unsafe on older Rust versions, safe on newer ones
fn hypervisor_vendor() -> Option<String> {
    #[cfg(target_arch = "x86_64")]
    {
        use std::arch::x86_64::__cpuid;
        // SAFETY: CPUID is always available on x86_64.
        let leaf1 = unsafe { __cpuid(1) };
        if leaf1.ecx & (1 << 31) == 0 {
            return None;
        }
        let l = unsafe { __cpuid(0x4000_0000) };
        let mut bytes = Vec::with_capacity(12);
        for reg in [l.ebx, l.ecx, l.edx] {
            bytes.extend_from_slice(&reg.to_le_bytes());
        }
        let vendor = String::from_utf8_lossy(&bytes).trim_matches(char::from(0)).trim().to_string();
        let known = [
            ("VMwareVMware", "VMware"),
            ("VBoxVBoxVBox", "VirtualBox"),
            ("KVMKVMKVM", "KVM"),
            ("XenVMMXenVMM", "Xen"),
            ("prl hyperv", "Parallels"),
            ("lrpepyh  vr", "Parallels"),
            ("TCGTCGTCGTCG", "QEMU"),
            ("bhyve bhyve", "bhyve"),
            ("ACRNACRNACRN", "ACRN"),
            ("QNXQVMBSQG", "QNX"),
        ];
        return known.iter().find(|(sig, _)| vendor.starts_with(sig)).map(|(_, name)| name.to_string());
    }
    #[allow(unreachable_code)]
    None
}

/// Firmware / hardware strings that VM products put in place of a real manufacturer.
fn firmware_vm() -> Option<String> {
    let text = firmware_strings()?.to_lowercase();
    let known = [
        ("vmware", "VMware"),
        ("virtualbox", "VirtualBox"),
        ("innotek", "VirtualBox"),
        ("qemu", "QEMU"),
        ("kvm", "KVM"),
        ("parallels", "Parallels"),
        ("xen", "Xen"),
        ("bochs", "Bochs"),
        ("virtual machine", "Hyper-V"), // Hyper-V guest: "Microsoft Corporation / Virtual Machine"
    ];
    known.iter().find(|(needle, _)| text.contains(needle)).map(|(_, name)| name.to_string())
}

#[cfg(windows)]
fn firmware_strings() -> Option<String> {
    super::win32::bios_strings()
}

#[cfg(target_os = "linux")]
fn firmware_strings() -> Option<String> {
    let read = |p: &str| std::fs::read_to_string(p).unwrap_or_default();
    let s = format!("{} {}", read("/sys/class/dmi/id/sys_vendor"), read("/sys/class/dmi/id/product_name"));
    Some(s)
}

#[cfg(target_os = "macos")]
fn firmware_strings() -> Option<String> {
    // kern.hv_vmm_present = 1 inside a VM on both Intel and Apple silicon.
    let out = std::process::Command::new("sysctl").args(["-n", "kern.hv_vmm_present"]).output().ok()?;
    if String::from_utf8_lossy(&out.stdout).trim() == "1" {
        Some("virtual machine".into())
    } else {
        None
    }
}

#[cfg(not(any(windows, target_os = "linux", target_os = "macos")))]
fn firmware_strings() -> Option<String> {
    None
}
