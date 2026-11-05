//! Linux: exams run in their own login session.
//!
//! Linux desktops (GNOME, KDE, Xfce...; X11 or Wayland) differ too much for an app to block
//! their shortcuts, and Wayland deliberately does not let apps do it. So, like the separate
//! desktop on Windows, the exam gets a session of its own: the installer adds
//! "UAI Aegis Exam" to the login screen (`linux/uai-aegis-exam.desktop`). It runs only this
//! app inside `cage`, a minimal kiosk compositor: no panel, no app switching, no other
//! windows, and no Ctrl+Alt+F1..F12 console switching (cage disables it unless started with
//! `-s`). Quitting the app ends the session and returns to the login screen.
//!
//! Before an exam starts, the app checks it really is in that session, using the session
//! record that systemd-logind (root) keeps, which the student cannot edit. Starting `cage`
//! in a window on a normal desktop, or setting an environment variable, does not pass.
//! During the exam, if the session stops being the one on screen (another console, a
//! second login), it is reported as `focus_lost` and switched back.

use std::collections::HashMap;
use std::process::Command;
use std::sync::OnceLock;

/// `DesktopNames` / file name of the session in `linux/uai-aegis-exam.desktop`. Display
/// managers pass one of them to logind as the session's desktop.
const EXAM_DESKTOPS: [&str; 2] = ["uai-aegis-exam", "uaiaegisexam"];

pub const NOT_IN_SESSION: &str = "Exams on Linux run in the UAI Aegis exam session. Log out, choose \
    \"UAI Aegis Exam\" on the login screen (the session or gear menu next to your name), sign in, \
    and start the exam there.";
const REMOTE: &str = "This is a remote login. Sit at the laptop itself and use the UAI Aegis Exam session.";
const NO_KIOSK: &str = "The exam session is not running correctly. Log out and sign in to \
    \"UAI Aegis Exam\" again.";

/// logind session id from /proc/<pid>/cgroup, e.g. ".../session-3.scope" -> "3".
/// Apps started from a normal desktop live in user@UID.service/app-*.scope instead, so they
/// have no session id here even if they run inside that session.
pub fn session_id_from_cgroup(cgroup: &str) -> Option<String> {
    cgroup.lines().find_map(|line| {
        let path = line.rsplit(':').next()?;
        let scope = path.split('/').find(|part| part.starts_with("session-") && part.ends_with(".scope"))?;
        let id = &scope["session-".len()..scope.len() - ".scope".len()];
        (!id.is_empty() && id.chars().all(|c| c.is_ascii_alphanumeric())).then(|| id.to_string())
    })
}

/// KEY=VALUE lines (loginctl show-session output or logind's session file), keys lower-cased.
pub fn parse_props(text: &str) -> HashMap<String, String> {
    text.lines()
        .filter_map(|line| line.split_once('='))
        .map(|(k, v)| (k.trim().to_ascii_lowercase(), v.trim().to_string()))
        .collect()
}

fn truthy(value: Option<&String>) -> bool {
    matches!(value.map(|v| v.to_ascii_lowercase()).as_deref(), Some("yes") | Some("1") | Some("true"))
}

fn normalise(name: &str) -> String {
    name.chars().filter(|c| c.is_ascii_alphanumeric()).collect::<String>().to_ascii_lowercase()
}

/// Is this logind session the exam session? `props` from [`session_props`].
pub fn is_exam_desktop(props: &HashMap<String, String>) -> bool {
    let desktop = props.get("desktop").map(|d| normalise(d)).unwrap_or_default();
    EXAM_DESKTOPS.iter().any(|name| normalise(name) == desktop)
}

/// The session's properties from systemd-logind.
fn session_props(id: &str) -> Option<HashMap<String, String>> {
    let out = Command::new("loginctl")
        .args(["show-session", id, "-p", "Desktop", "-p", "Active", "-p", "Remote", "-p", "Type"])
        .output();
    if let Ok(out) = out {
        if out.status.success() {
            return Some(parse_props(&String::from_utf8_lossy(&out.stdout)));
        }
    }
    // No loginctl on PATH: the same record, as logind writes it.
    std::fs::read_to_string(format!("/run/systemd/sessions/{id}")).ok().map(|t| parse_props(&t))
}

/// Process name and parent of a process, from /proc/<pid>/stat ("pid (comm) state ppid ...").
pub fn parse_stat(stat: &str) -> Option<(String, u32)> {
    let open = stat.find('(')?;
    let close = stat.rfind(')')?;
    let comm = stat.get(open + 1..close)?.to_string();
    let ppid = stat.get(close + 1..)?.split_whitespace().nth(1)?.parse().ok()?;
    Some((comm, ppid))
}

/// Is `cage` one of this process's ancestors?
fn under_cage() -> bool {
    let mut pid = std::process::id();
    for _ in 0..32 {
        let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) else { return false };
        let Some((comm, ppid)) = parse_stat(&stat) else { return false };
        if comm == "cage" {
            return true;
        }
        if ppid <= 1 {
            return false;
        }
        pid = ppid;
    }
    false
}

/// The audit session id the kernel gives every process of a login (it cannot be changed
/// without special privileges); logind uses the same number as the session id.
/// 4294967295 means "not set".
pub fn session_id_from_audit(text: &str) -> Option<String> {
    let id = text.trim();
    (!id.is_empty() && id != "4294967295" && id.chars().all(|c| c.is_ascii_digit())).then(|| id.to_string())
}

fn own_session_id() -> Option<String> {
    let from_cgroup = std::fs::read_to_string("/proc/self/cgroup").ok().and_then(|t| session_id_from_cgroup(&t));
    from_cgroup.or_else(|| session_id_from_audit(&std::fs::read_to_string("/proc/self/sessionid").ok()?))
}

/// Ok(session id) when the app runs in the exam session on this laptop's own screen,
/// otherwise the message to show the student.
pub fn exam_session() -> Result<String, &'static str> {
    let id = own_session_id().ok_or(NOT_IN_SESSION)?;
    let props = session_props(&id).ok_or(NOT_IN_SESSION)?;
    if !is_exam_desktop(&props) {
        return Err(NOT_IN_SESSION);
    }
    if truthy(props.get("remote")) {
        return Err(REMOTE);
    }
    if !under_cage() {
        return Err(NO_KIOSK);
    }
    Ok(id)
}

/// [`exam_session`], remembered once it has passed (the session cannot change while the
/// app runs).
pub fn exam_session_id() -> Result<String, &'static str> {
    static ID: OnceLock<String> = OnceLock::new();
    if let Some(id) = ID.get() {
        return Ok(id.clone());
    }
    let id = exam_session()?;
    let _ = ID.set(id.clone());
    Ok(id)
}

/// Is the session the one on screen? None if it cannot be told.
pub fn session_active(id: &str) -> Option<bool> {
    session_props(id).map(|p| truthy(p.get("active")))
}

/// Switch the screen back to the exam session (best effort; logind lets a user activate
/// their own session on their own seat).
pub fn reactivate(id: &str) {
    let _ = Command::new("loginctl").args(["activate", id]).status();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_id_comes_from_the_session_scope_only() {
        let in_session = "0::/user.slice/user-1000.slice/session-3.scope\n";
        assert_eq!(session_id_from_cgroup(in_session).as_deref(), Some("3"));
        let v1 = "12:pids:/user.slice/user-1000.slice/session-c2.scope\n1:name=systemd:/user.slice/user-1000.slice/session-c2.scope\n";
        assert_eq!(session_id_from_cgroup(v1).as_deref(), Some("c2"));
        // Started from a normal desktop (GNOME/KDE launch apps as user-service scopes)
        let app = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-uai\\x2daegis-4242.scope\n";
        assert_eq!(session_id_from_cgroup(app), None);
        assert_eq!(session_id_from_cgroup("0::/system.slice/cron.service\n"), None);
        assert_eq!(session_id_from_cgroup("0::/user.slice/session-.scope\n"), None);
    }

    #[test]
    fn audit_session_id_is_used_only_when_set() {
        assert_eq!(session_id_from_audit("7\n").as_deref(), Some("7"));
        assert_eq!(session_id_from_audit("4294967295"), None);
        assert_eq!(session_id_from_audit(""), None);
    }

    #[test]
    fn exam_desktop_is_recognised_from_loginctl_or_the_logind_file() {
        let loginctl = parse_props("Desktop=uai-aegis-exam\nActive=yes\nRemote=no\nType=wayland\n");
        assert!(is_exam_desktop(&loginctl) && truthy(loginctl.get("active")) && !truthy(loginctl.get("remote")));
        let file = parse_props("# This is private data. Do not parse.\nUID=1000\nACTIVE=0\nREMOTE=0\nDESKTOP=UAIAegisExam\n");
        assert!(is_exam_desktop(&file) && !truthy(file.get("active")));
        for other in ["Desktop=GNOME\n", "Desktop=KDE\n", "Desktop=\n", "Type=wayland\n", "Desktop=uai-aegis-exam-x\n"] {
            assert!(!is_exam_desktop(&parse_props(other)), "{other}");
        }
    }

    /// On a real machine: `cage -- cargo test --lib live_session -- --ignored --nocapture`
    /// (or from the exam session's terminal-less app) shows what the checks see.
    #[test]
    #[ignore]
    fn live_session() {
        println!("under cage: {}", under_cage());
        println!("session id: {:?}", own_session_id());
        println!("exam session: {:?}", exam_session());
    }

    #[test]
    fn stat_lines_with_odd_process_names_parse() {
        assert_eq!(parse_stat("4242 (cage) S 4100 4242 4242 0 -1 4194560"), Some(("cage".into(), 4100)));
        assert_eq!(parse_stat("77 (Web Content (x)) S 12 77"), Some(("Web Content (x)".into(), 12)));
        assert_eq!(parse_stat("garbage"), None);
    }
}
