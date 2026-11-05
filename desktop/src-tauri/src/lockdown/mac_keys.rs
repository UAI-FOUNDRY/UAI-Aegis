//! Which key presses the macOS key filter swallows during an exam (see macos.rs).
//! Plain logic, compiled and tested on every platform.

// Modifier bits of CGEventFlags.
pub const SHIFT: u64 = 0x0002_0000;
pub const CONTROL: u64 = 0x0004_0000;
pub const OPTION: u64 = 0x0008_0000;
pub const COMMAND: u64 = 0x0010_0000;
/// The fn / globe key. macOS also sets it on arrow and F-keys by itself, so it only counts
/// together with letter keys below.
pub const FN: u64 = 0x0080_0000;

// Virtual key codes (Carbon kVK_*, the same on every keyboard layout).
mod key {
    pub const A: u16 = 0x00;
    pub const H: u16 = 0x04;
    pub const C: u16 = 0x08;
    pub const D: u16 = 0x02;
    pub const F: u16 = 0x03;
    pub const Q: u16 = 0x0C;
    pub const M: u16 = 0x2E;
    pub const N: u16 = 0x2D;
    pub const DIGITS: [u16; 10] = [0x12, 0x13, 0x14, 0x15, 0x17, 0x16, 0x1A, 0x1C, 0x19, 0x1D];
    pub const THREE: u16 = 0x14;
    pub const FOUR: u16 = 0x15;
    pub const FIVE: u16 = 0x17;
    pub const SIX: u16 = 0x16;
    pub const TAB: u16 = 0x30;
    pub const SPACE: u16 = 0x31;
    pub const ESCAPE: u16 = 0x35;
    pub const ARROWS: [u16; 4] = [0x7B, 0x7C, 0x7D, 0x7E];
    pub const F11: u16 = 0x67;
    pub const LAUNCHPAD: u16 = 0x83; // the Launchpad key on Apple keyboards
    pub const MISSION_CONTROL: u16 = 0xA0; // the Mission Control key on Apple keyboards
}

/// True if this key press must not reach macOS while an exam is on screen.
/// Copy, paste, undo, select all, typing and normal editing are never blocked.
pub fn should_block(keycode: u16, flags: u64) -> bool {
    let cmd = flags & COMMAND != 0;
    let ctrl = flags & CONTROL != 0;
    let opt = flags & OPTION != 0;
    let shift = flags & SHIFT != 0;
    let fn_key = flags & FN != 0;

    match keycode {
        // Mission Control, Launchpad and Show Desktop keys
        key::MISSION_CONTROL | key::LAUNCHPAD | key::F11 => true,
        // Cmd+Space: Spotlight (also Siri when held); Cmd+Opt+Space: Finder search
        key::SPACE => cmd,
        // Cmd+Tab: app switcher
        key::TAB => cmd,
        // Cmd+Q quit, Ctrl+Cmd+Q lock screen; fn+Q Quick Note
        key::Q => cmd || fn_key,
        // Cmd+H hide (Cmd+Opt+H hide others); fn+H show desktop
        key::H => cmd || fn_key,
        // Cmd+M minimise; fn+M menu bar focus
        key::M => cmd || fn_key,
        // Cmd+Opt+Esc: Force Quit window
        key::ESCAPE => cmd && opt,
        // Cmd+Opt+D: show/hide the Dock; fn+D: Dictation
        key::D => (cmd && opt) || fn_key,
        // Ctrl+Cmd+F: native full screen (would leave the exam's own full screen); fn+F the same
        key::F => (cmd && ctrl) || fn_key,
        // fn+A Dock focus, fn+C Control Centre, fn+N Notification Centre
        key::A | key::C | key::N => fn_key,
        // Cmd+Shift+3/4/5/6: screenshots and screen recording
        key::THREE | key::FOUR | key::FIVE | key::SIX if cmd && shift => true,
        // Ctrl+1..9: switch to desktop N
        k if key::DIGITS.contains(&k) => ctrl && !cmd,
        // Ctrl+arrows: Mission Control, App windows, switch desktop
        k if key::ARROWS.contains(&k) => ctrl,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const C: u16 = 0x08;
    const V: u16 = 0x09;
    const Z: u16 = 0x06;
    const LEFT: u16 = 0x7B;
    const UP: u16 = 0x7E;

    #[test]
    fn escapes_are_blocked() {
        assert!(should_block(0x31, COMMAND)); // Spotlight
        assert!(should_block(0x30, COMMAND)); // Cmd+Tab
        assert!(should_block(0x30, COMMAND | SHIFT));
        assert!(should_block(0x0C, COMMAND)); // Cmd+Q
        assert!(should_block(0x0C, COMMAND | CONTROL)); // lock screen
        assert!(should_block(0x04, COMMAND)); // Cmd+H
        assert!(should_block(0x2E, COMMAND)); // Cmd+M
        assert!(should_block(0x35, COMMAND | OPTION)); // Force Quit
        assert!(should_block(0x15, COMMAND | SHIFT)); // screenshot
        assert!(should_block(UP, CONTROL | FN)); // Mission Control (arrows carry fn by themselves)
        assert!(should_block(LEFT, CONTROL | FN)); // previous desktop
        assert!(should_block(0x12, CONTROL)); // Ctrl+1: desktop 1
        assert!(should_block(0xA0, 0)); // Mission Control key
        assert!(should_block(0x83, 0)); // Launchpad key
        assert!(should_block(0x2D, FN)); // Notification Centre
        assert!(should_block(0x03, COMMAND | CONTROL)); // native full screen
    }

    #[test]
    fn editing_is_never_blocked() {
        for (code, flags) in [(C, COMMAND), (V, COMMAND), (Z, COMMAND), (Z, COMMAND | SHIFT), (0x00, COMMAND),
                              (LEFT, FN), (LEFT, COMMAND | FN), (LEFT, OPTION | FN), (LEFT, SHIFT | FN),
                              (0x31, 0), (0x31, CONTROL), (0x30, 0), (0x30, SHIFT), (0x0C, 0), (0x0C, SHIFT),
                              (0x12, 0), (0x12, SHIFT), (0x14, COMMAND), (0x35, 0), (0x04, OPTION)] {
            assert!(!should_block(code, flags), "code {code:#x} flags {flags:#x}");
        }
    }
}
