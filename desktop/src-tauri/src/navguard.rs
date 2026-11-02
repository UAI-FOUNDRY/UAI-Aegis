//! Navigation guard (CLIENT_PROTOCOL.md section 7): the web view may only show the app's
//! own pages. Any attempt to load another URL (a link, a form, `location.href = ...`) is
//! refused, so the exam window can never become a browser.

use tauri::plugin::{Builder, TauriPlugin};
use tauri::{Runtime, Url};

/// True for the app's own pages: the bundled files in release builds, and the Vite dev
/// server (`devUrl` in tauri.conf.json) in development builds only.
pub fn allowed(url: &Url) -> bool {
    match url.scheme() {
        // macOS / Linux serve the bundle as tauri://localhost
        "tauri" => url.host_str() == Some("localhost"),
        // Windows serves it as http(s)://tauri.localhost
        "http" | "https" if url.host_str() == Some("tauri.localhost") => true,
        "http" if cfg!(debug_assertions) => {
            matches!(url.host_str(), Some("localhost") | Some("127.0.0.1")) && url.port() == Some(1420)
        }
        // about:blank (Monaco and the web view itself use it internally); never data:,
        // file:, javascript: or anything on the internet.
        "about" => url.as_str() == "about:blank" || url.as_str() == "about:srcdoc",
        _ => false,
    }
}

pub fn plugin<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("aegis-navguard")
        .on_navigation(|_webview, url| {
            let ok = allowed(url);
            if !ok {
                eprintln!("[aegis] navigation blocked: {url}");
            }
            ok
        })
        .build()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok(s: &str) -> bool {
        allowed(&Url::parse(s).unwrap())
    }

    #[test]
    fn app_pages_allowed() {
        assert!(ok("tauri://localhost/"));
        assert!(ok("tauri://localhost/index.html"));
        assert!(ok("http://tauri.localhost/"));
        assert!(ok("https://tauri.localhost/assets/x.js"));
        assert!(ok("about:blank"));
    }

    #[test]
    fn everything_else_blocked() {
        assert!(!ok("https://google.com/"));
        assert!(!ok("http://tauri.localhost.evil.com/"));
        assert!(!ok("tauri://evil/"));
        assert!(!ok("file:///C:/Windows/System32/"));
        assert!(!ok("data:text/html,<h1>hi</h1>"));
        assert!(!ok("javascript:alert(1)"));
        assert!(!ok("http://localhost:8000/docs")); // the API server is not a page
    }

    #[test]
    fn dev_server_only_in_debug() {
        assert_eq!(ok("http://localhost:1420/"), cfg!(debug_assertions));
    }
}
