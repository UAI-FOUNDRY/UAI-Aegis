import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import {
  openLiveMonitor,
  type LiveMessage,
  type LiveStatus,
  type MonitorRow,
  type MonitorSnapshot,
} from '../lib/liveMonitor'

const POLL_MS = 5_000
const FLASH_MS = 4_000
// Row changes the professor must act on.
const ALERT_CHANGES = ['locked', 'device_change']

/** Same order as the API: locked first, then active, not started, finished. */
function sortRows(rows: MonitorRow[]): MonitorRow[] {
  const rank = (status: string) =>
    ({ locked: 0, active: 1, not_started: 2 } as Record<string, number>)[status] ?? 3
  return [...rows].sort(
    (a, b) =>
      rank(a.status) - rank(b.status) ||
      a.student.full_name.localeCompare(b.student.full_name),
  )
}

/** Per-status counts plus disconnected = active or locked and not connected. */
export function countRows(rows: MonitorRow[]): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const row of rows) counts[row.status] = (counts[row.status] ?? 0) + 1
  counts.disconnected = rows.filter(
    (r) => (r.status === 'active' || r.status === 'locked') && !r.connected,
  ).length
  return counts
}

type Options = {
  examId: string
  batchCode: string | null
  /** Called when a student is locked or switches laptop. */
  onAlert?: (row: MonitorRow, change: string) => void
}

export function useLiveMonitor({ examId, batchCode, onAlert }: Options) {
  const [rows, setRows] = useState<MonitorRow[]>([])
  const [examStatus, setExamStatus] = useState<MonitorSnapshot['exam_status'] | null>(null)
  // server clock minus browser clock, for remaining-time display
  const [clockOffsetMs, setClockOffsetMs] = useState(0)
  const [liveStatus, setLiveStatus] = useState<LiveStatus>('connecting')
  const [loaded, setLoaded] = useState(false)
  const [pollError, setPollError] = useState('')
  // student id -> time the flash started
  const [flashing, setFlashing] = useState<Record<string, number>>({})
  const [batchCodes, setBatchCodes] = useState<string[]>([])

  const alertRef = useRef(onAlert)
  useEffect(() => {
    alertRef.current = onAlert
  }, [onAlert])

  const applySnapshot = useCallback((snapshot: MonitorSnapshot) => {
    setRows(sortRows(snapshot.rows))
    setExamStatus(snapshot.exam_status)
    setClockOffsetMs(Date.parse(snapshot.server_now) - Date.now())
    setLoaded(true)
    setPollError('')
    // Remember every batch seen, so the filter keeps its options while filtered.
    setBatchCodes((current) => {
      const seen = new Set(current)
      for (const row of snapshot.rows) if (row.batch_code) seen.add(row.batch_code)
      return seen.size === current.length ? current : [...seen].sort()
    })
  }, [])

  /** Replace one student's row (from the socket or an action's response). */
  const applyRow = useCallback((row: MonitorRow) => {
    setRows((current) => {
      const others = current.filter((r) => r.student.id !== row.student.id)
      return sortRows([...others, row])
    })
  }, [])

  const flash = useCallback((studentId: string) => {
    const startedAt = Date.now()
    setFlashing((current) => ({ ...current, [studentId]: startedAt }))
    window.setTimeout(() => {
      setFlashing((current) => {
        if (current[studentId] !== startedAt) return current
        const next = { ...current }
        delete next[studentId]
        return next
      })
    }, FLASH_MS)
  }, [])

  // The WebSocket.
  useEffect(() => {
    const stop = openLiveMonitor({
      examId,
      batchCode,
      onStatus: setLiveStatus,
      onMessage: (message: LiveMessage) => {
        if (message.type === 'snapshot') {
          applySnapshot(message.data)
          return
        }
        if (message.type === 'row') {
          if (batchCode && message.data.batch_code !== batchCode) return
          applyRow(message.data)
          setClockOffsetMs(Date.parse(message.server_now) - Date.now())
          const alert = message.changes.find((c) => ALERT_CHANGES.includes(c))
          if (alert) {
            flash(message.data.student.id)
            alertRef.current?.(message.data, alert)
          }
        }
      },
    })
    return stop
  }, [examId, batchCode, applySnapshot, applyRow, flash])

  const refresh = useCallback(async () => {
    const result = await api.GET('/api/v1/exams/{exam_id}/monitor', {
      params: {
        path: { exam_id: examId },
        query: batchCode ? { batch_code: batchCode } : {},
      },
    })
    if (result.error) {
      setPollError(
        result.response.status === 404
          ? 'Not found or you don’t have access.'
          : `Could not load the monitor (HTTP ${result.response.status}).`,
      )
      return
    }
    applySnapshot(result.data as MonitorSnapshot)
  }, [examId, batchCode, applySnapshot])

  // Fallback: poll while the socket is not open (also gives the first rows quickly).
  const socketUp = liveStatus === 'live' || liveStatus === 'forbidden'
  useEffect(() => {
    if (socketUp) return
    const first = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => void refresh(), POLL_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
    }
  }, [socketUp, refresh])

  return {
    rows,
    counts: countRows(rows),
    examStatus,
    clockOffsetMs,
    liveStatus,
    loaded,
    pollError,
    flashing,
    batchCodes,
    applyRow,
    refresh,
  }
}
