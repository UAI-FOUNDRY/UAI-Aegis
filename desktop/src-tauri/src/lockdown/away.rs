//! Trips away from the exam ("excursions").
//!
//! Whatever takes the student off the exam (a click on another window, a touchpad swipe
//! to another desktop or Space, Task Manager from Ctrl+Alt+Del, a second login on Linux),
//! the app does the same thing:
//! 1. pulls the exam back to the front at once, and keeps pulling every
//!    [`PULL_EVERY`] until it is back (focused and full screen);
//! 2. reports the trip once, with an excursion id. Every event of the same trip carries
//!    the same id, and the server counts one strike per id (services/proctoring.py), so
//!    leaving and being brought back is one strike, however many detectors noticed it.
//!
//! The tracker itself is plain logic, tested on every platform.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

/// How often the window is checked (focus + full screen).
pub const CHECK_EVERY: Duration = Duration::from_millis(50);
/// While away, how often the exam is pulled back to the front.
pub const PULL_EVERY: Duration = Duration::from_millis(100);
/// Focus that comes back by itself this quickly is a flicker (an OS popup taking focus for
/// a moment), not a trip: no strike. Anything longer is pulled back and counted.
pub const FLICKER: Duration = Duration::from_millis(150);

/// Why the student is off the exam.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reason {
    /// Another window / app got the focus (click, gesture, Space or virtual desktop switch).
    Window,
    /// The window is still focused but no longer covers the screen.
    Fullscreen,
    /// Windows: the screen left the separate exam desktop (e.g. Ctrl+Alt+Del, Task Manager).
    ExamDesktop,
    /// Linux: the exam login session is no longer the one on screen.
    ExamSession,
}

impl Reason {
    /// Event kind sent to the server (CLIENT_PROTOCOL.md section 5).
    pub fn kind(self) -> &'static str {
        match self {
            Reason::Fullscreen => "fullscreen_exited",
            _ => "focus_lost",
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Reason::Window => "window",
            Reason::Fullscreen => "fullscreen",
            Reason::ExamDesktop => "exam_desktop",
            Reason::ExamSession => "exam_session",
        }
    }

    /// Detectors that cannot be a flicker: counted at once.
    pub fn certain(self) -> bool {
        matches!(self, Reason::ExamDesktop | Reason::ExamSession)
    }
}

#[derive(Debug)]
struct Trip {
    id: u64,
    since: Instant,
    reason: Reason,
    reported: bool,
}

/// One per app; tests make their own.
#[derive(Debug, Default)]
pub struct Tracker {
    trip: Option<Trip>,
    /// When the window was first seen off the exam (for the flicker allowance).
    off_since: Option<Instant>,
    next_id: u64,
}

/// What the caller should do after an observation.
#[derive(Debug, PartialEq, Eq)]
pub enum Action {
    /// Nothing (on the exam, or a possible flicker still inside its allowance).
    None,
    /// Pull the exam back to the front.
    Pull,
    /// Pull back AND report this trip (once per trip).
    PullAndReport { id: u64, reason: Reason },
}

impl Tracker {
    /// The student is off the exam for `reason` (seen at `now`).
    pub fn off(&mut self, reason: Reason, now: Instant) -> Action {
        let off_since = *self.off_since.get_or_insert(now);
        if self.trip.is_none() {
            if !reason.certain() && now.duration_since(off_since) < FLICKER {
                return Action::None; // maybe a flicker: give it a moment to come back by itself
            }
            self.next_id += 1;
            self.trip = Some(Trip { id: self.next_id, since: off_since, reason, reported: false });
        }
        let trip = self.trip.as_mut().expect("set above");
        if trip.reported {
            return Action::Pull;
        }
        trip.reported = true;
        Action::PullAndReport { id: trip.id, reason: trip.reason }
    }

    /// The exam is on screen, focused and full screen again. Returns how long the trip
    /// lasted, if there was one.
    pub fn back(&mut self, now: Instant) -> Option<Duration> {
        self.off_since = None;
        self.trip.take().map(|t| now.duration_since(t.since))
    }

    /// The id of the trip in progress (events reported during it carry it too).
    pub fn current(&self) -> Option<u64> {
        self.trip.as_ref().map(|t| t.id)
    }
}

fn tracker() -> &'static Mutex<Tracker> {
    static T: OnceLock<Mutex<Tracker>> = OnceLock::new();
    T.get_or_init(|| Mutex::new(Tracker::default()))
}

pub fn off(reason: Reason) -> Action {
    tracker().lock().map(|mut t| t.off(reason, Instant::now())).unwrap_or(Action::Pull)
}

pub fn back() -> Option<Duration> {
    tracker().lock().ok().and_then(|mut t| t.back(Instant::now()))
}

pub fn current() -> Option<u64> {
    tracker().lock().ok().and_then(|t| t.current())
}

/// Excursion ids must not repeat across app restarts (the server remembers the last one
/// per session), so each run gets its own random prefix.
pub fn excursion_tag(id: u64) -> String {
    static RUN: AtomicU64 = AtomicU64::new(0);
    let run = match RUN.load(Ordering::Relaxed) {
        0 => {
            let fresh = rand::random::<u64>() | 1; // never 0 ("not set yet")
            match RUN.compare_exchange(0, fresh, Ordering::Relaxed, Ordering::Relaxed) {
                Ok(_) => fresh,
                Err(already) => already,
            }
        }
        run => run,
    };
    format!("{run:016x}-{id}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(start: Instant, ms: u64) -> Instant {
        start + Duration::from_millis(ms)
    }

    #[test]
    fn a_flicker_is_not_a_trip() {
        let t0 = Instant::now();
        let mut t = Tracker::default();
        assert_eq!(t.off(Reason::Window, at(t0, 0)), Action::None);
        assert_eq!(t.off(Reason::Window, at(t0, 100)), Action::None);
        assert_eq!(t.back(at(t0, 120)), None);
        assert_eq!(t.current(), None);
    }

    #[test]
    fn leaving_and_being_pulled_back_is_reported_once() {
        let t0 = Instant::now();
        let mut t = Tracker::default();
        assert_eq!(t.off(Reason::Window, at(t0, 0)), Action::None);
        assert_eq!(t.off(Reason::Window, at(t0, 150)), Action::PullAndReport { id: 1, reason: Reason::Window });
        // Still away: keep pulling, do not report again, whatever else is noticed
        assert_eq!(t.off(Reason::Window, at(t0, 250)), Action::Pull);
        assert_eq!(t.off(Reason::Fullscreen, at(t0, 300)), Action::Pull);
        assert_eq!(t.off(Reason::ExamDesktop, at(t0, 350)), Action::Pull);
        assert_eq!(t.current(), Some(1));
        assert_eq!(t.back(at(t0, 400)), Some(Duration::from_millis(400)));
        // The next trip is a new one
        assert_eq!(t.off(Reason::Fullscreen, at(t0, 1000)), Action::None);
        assert_eq!(t.off(Reason::Fullscreen, at(t0, 1200)), Action::PullAndReport { id: 2, reason: Reason::Fullscreen });
    }

    #[test]
    fn leaving_the_exam_desktop_or_session_counts_at_once() {
        let t0 = Instant::now();
        let mut t = Tracker::default();
        assert_eq!(t.off(Reason::ExamDesktop, at(t0, 0)), Action::PullAndReport { id: 1, reason: Reason::ExamDesktop });
        t.back(at(t0, 100));
        assert_eq!(t.off(Reason::ExamSession, at(t0, 200)), Action::PullAndReport { id: 2, reason: Reason::ExamSession });
    }

    #[test]
    fn a_window_flicker_then_a_certain_departure_is_one_trip() {
        let t0 = Instant::now();
        let mut t = Tracker::default();
        assert_eq!(t.off(Reason::Window, at(t0, 0)), Action::None);
        assert_eq!(t.off(Reason::ExamDesktop, at(t0, 50)), Action::PullAndReport { id: 1, reason: Reason::ExamDesktop });
        assert_eq!(t.off(Reason::Window, at(t0, 200)), Action::Pull);
    }

    #[test]
    fn excursion_tags_are_unique_per_trip_and_fit_the_server_limit() {
        let a = excursion_tag(1);
        let b = excursion_tag(2);
        assert_ne!(a, b);
        assert_eq!(a.split('-').next(), b.split('-').next()); // same run prefix
        assert!(a.len() <= 64);
    }
}
