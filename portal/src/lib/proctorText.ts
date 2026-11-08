/** Words for session statuses, lock reasons and proctoring events (monitor + session detail). */

export const STATUS_LABELS: Record<string, string> = {
  not_started: 'Not started',
  active: 'Active',
  locked: 'Locked',
  submitted: 'Submitted',
  terminated: 'Terminated',
}

export const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  not_started: { bg: '#f3f4f6', fg: '#374151' },
  active: { bg: '#dcfce7', fg: '#166534' },
  locked: { bg: '#fee2e2', fg: '#991b1b' },
  submitted: { bg: '#dbeafe', fg: '#1e40af' },
  terminated: { bg: '#e5e7eb', fg: '#111827' },
}

// Event kinds from the backend's proctoring policy (services/proctoring.py), in words.
const EVENT_TEXT: Record<string, string> = {
  too_many_violations: 'Reached the strike limit',
  focus_lost: 'Left the exam window',
  fullscreen_exited: 'Left full screen',
  app_restarted: 'Restarted the app',
  clipboard_external_paste: 'Pasted from outside the app',
  paste_blocked: 'Paste blocked',
  shortcut_blocked: 'Shortcut blocked',
  network_reconnected: 'Network reconnected',
  heartbeat_gap: 'Connection gap',
  display_added: 'Connected a second screen',
  vm_detected: 'Running in a virtual machine',
  remote_desktop_detected: 'Remote desktop detected',
  screen_capture_detected: 'Screen capture detected',
  blocked_app_detected: 'A blocked app is running',
  debugger_detected: 'A debugger is attached',
  integrity_failed: 'The app failed its integrity check',
  device_changed: 'Switched to another laptop',
  staff_lock: 'Locked by professor',
  staff_resume: 'Resumed by professor',
  staff_extend: 'Extra time from professor',
  forbidden_app_detected: 'Blocked app running',
  staff_terminate: 'Exam ended by professor',
}

export function eventText(kind: string): string {
  return EVENT_TEXT[kind] ?? kind.replace(/_/g, ' ')
}

export function lockReasonText(reason: string | null): string {
  if (!reason) return ''
  if (reason.startsWith('staff:')) {
    const why = reason.slice('staff:'.length).replace(/_/g, ' ')
    return `Locked by professor (${why})`
  }
  return eventText(reason)
}

export function finishReasonText(reason: string | null): string {
  if (!reason) return ''
  return (
    {
      submitted: 'Handed in',
      deadline: 'Time ran out',
      exam_closed: 'Exam closed',
      terminated: 'Ended by professor',
    } as Record<string, string>
  )[reason] ?? reason.replace(/_/g, ' ')
}
