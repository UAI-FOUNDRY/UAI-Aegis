import { API_URL, api } from '../api/client'

/** One student's row, as GET /exams/{id}/monitor and the live socket send it. */
export type MonitorRow = {
  student: { id: string; email: string; full_name: string; college_id: string | null }
  batch_code: string | null
  session_id: string | null
  status: 'not_started' | 'active' | 'locked' | 'submitted' | 'terminated' | string
  connected: boolean
  strikes: number
  lock_reason: string | null
  device_change_pending: boolean
  flagged: boolean
  started_at: string | null
  deadline_at: string | null
  finished_at: string | null
  finish_reason: string | null
  last_heartbeat_at: string | null
  event_count: number
  grading_status: string | null
}

export type MonitorSnapshot = {
  exam_id: string
  exam_status: 'draft' | 'live' | 'closed'
  server_now: string
  counts: Record<string, number>
  rows: MonitorRow[]
}

export type LiveMessage =
  | { type: 'snapshot'; data: MonitorSnapshot }
  | {
      type: 'row'
      change: string
      changes: string[]
      server_now: string
      data: MonitorRow
    }

/**
 * connecting / reconnecting: no socket yet (the caller should poll meanwhile)
 * live: socket open, updates are pushed
 * too_many: close code 4429, too many monitors open for this user
 * forbidden: close code 4403 or no access to the exam; stopped for good
 */
export type LiveStatus = 'connecting' | 'reconnecting' | 'live' | 'too_many' | 'forbidden'

type Options = {
  examId: string
  batchCode?: string | null
  onMessage: (message: LiveMessage) => void
  onStatus: (status: LiveStatus) => void
}

const MAX_BACKOFF_MS = 15_000
const TOO_MANY_RETRY_MS = 60_000

/**
 * Open the live monitor WebSocket (see backend/app/api/v1/live.py).
 * Browsers cannot send an Authorization header on a WebSocket, so each connection
 * first gets a one-time ticket (valid 30 s) with a normal bearer request.
 * Reconnects with back-off up to 15 s. Returns a function that stops everything.
 */
export function openLiveMonitor({ examId, batchCode, onMessage, onStatus }: Options): () => void {
  let stopped = false
  let socket: WebSocket | null = null
  let retryTimer: number | undefined
  let attempt = 0

  // http://host -> ws://host, https://host -> wss://host
  const socketBase = API_URL.replace(/^http/, 'ws').replace(/\/$/, '')

  function retry(delay?: number) {
    if (stopped) return
    attempt += 1
    const wait = delay ?? Math.min(MAX_BACKOFF_MS, 1000 * 2 ** (attempt - 1))
    retryTimer = window.setTimeout(() => void connect(), wait)
  }

  async function connect() {
    if (stopped) return
    onStatus(attempt === 0 ? 'connecting' : 'reconnecting')

    let path: string
    try {
      const result = await api.POST('/api/v1/exams/{exam_id}/live-ticket', {
        params: { path: { exam_id: examId } },
      })
      if (stopped) return
      if (result.error) {
        const status = result.response.status
        if (status === 401 || status === 403 || status === 404) {
          onStatus('forbidden')
          return
        }
        retry() // e.g. 429 (ticket rate limit) or a server error
        return
      }
      path = (result.data as { path: string }).path
    } catch {
      retry() // network down
      return
    }

    const query = batchCode ? `&batch_code=${encodeURIComponent(batchCode)}` : ''
    const ws = new WebSocket(`${socketBase}${path}${query}`)
    socket = ws

    ws.onopen = () => {
      attempt = 0
      onStatus('live')
    }
    ws.onmessage = (event) => {
      try {
        onMessage(JSON.parse(String(event.data)) as LiveMessage)
      } catch {
        // Ignore a malformed frame; the next snapshot corrects everything.
      }
    }
    ws.onclose = (event) => {
      if (socket === ws) socket = null
      if (stopped) return
      if (event.code === 4403) {
        onStatus('forbidden') // no access any more, or signed out
        return
      }
      if (event.code === 4429) {
        onStatus('too_many')
        retry(TOO_MANY_RETRY_MS)
        return
      }
      // 4401 (ticket expired/used), 1012 (server restarting), network drops: new ticket.
      onStatus('reconnecting')
      retry()
    }
  }

  void connect()

  return () => {
    stopped = true
    window.clearTimeout(retryTimer)
    socket?.close(1000)
    socket = null
  }
}
