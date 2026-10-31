import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { errorText } from '../apiErrors'
import { errorStyle, sectionStyle } from './adminShared'

type AuditEntry = {
  seq: number
  at: string
  actor_label: string
  action: string
  target_type: string
  target_id: string
  details: Record<string, unknown>
}

type Verify = { ok: boolean; checked: number; problem_seq?: number; head_hash?: string; [k: string]: unknown }

const PAGE = 200

/**
 * Who did what and when (GET /admin/audit, newest first). The API pages by offset
 * only, so filtering is done here on the loaded entries; "Load older" fetches more.
 */
export default function AuditAdmin() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null)
  const [error, setError] = useState('')
  const [more, setMore] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [text, setText] = useState('')
  const [action, setAction] = useState('')
  const [verify, setVerify] = useState<Verify | null>(null)
  const [verifying, setVerifying] = useState(false)

  const fetchPage = useCallback(async (offset: number) => {
    const result = await api.GET('/api/v1/admin/audit', { params: { query: { limit: PAGE, offset } } })
    if (result.error) throw new Error(errorText(result.error, 'Could not load the audit log.'))
    return result.data as AuditEntry[]
  }, [])

  useEffect(() => {
    let cancelled = false
    void fetchPage(0)
      .then((page) => {
        if (cancelled) return
        setEntries(page)
        setMore(page.length === PAGE)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the audit log.')
      })
    return () => {
      cancelled = true
    }
  }, [fetchPage])

  async function loadOlder() {
    if (!entries) return
    setLoadingMore(true)
    try {
      const page = await fetchPage(entries.length)
      setEntries([...entries, ...page])
      setMore(page.length === PAGE)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load more.')
    } finally {
      setLoadingMore(false)
    }
  }

  async function runVerify() {
    setVerifying(true)
    setVerify(null)
    const result = await api.GET('/api/v1/admin/audit/verify')
    setVerifying(false)
    if (result.error) {
      setError(errorText(result.error, 'Could not verify the audit log.'))
      return
    }
    setVerify(result.data as Verify)
  }

  const actions = useMemo(() => [...new Set((entries ?? []).map((e) => e.action))].sort(), [entries])
  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase()
    return (entries ?? []).filter((e) => {
      if (action && e.action !== action) return false
      if (!needle) return true
      return [e.actor_label, e.action, e.target_type, e.target_id, JSON.stringify(e.details)].some((v) =>
        v.toLowerCase().includes(needle),
      )
    })
  }, [entries, text, action])

  return (
    <section>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0 }}>Audit log</h2>
        <button type="button" onClick={() => void runVerify()} disabled={verifying} style={{ fontWeight: 'bold' }}>
          {verifying ? 'Verifying…' : 'Verify integrity'}
        </button>
      </div>

      {verify && (
        <div
          role="status"
          data-testid="verify-result"
          style={{
            ...sectionStyle,
            background: verify.ok ? '#dcfce7' : '#fee2e2',
            color: verify.ok ? '#166534' : '#991b1b',
            fontWeight: 'bold',
          }}
        >
          {verify.ok
            ? `✓ PASS: the audit chain is intact (${verify.checked} entries checked).`
            : `✖ FAIL: entry #${verify.problem_seq ?? '?'} was changed or removed (${verify.checked} checked before the break).`}
        </div>
      )}

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', margin: '12px 0' }}>
        <label>Search <input value={text} onChange={(e) => setText(e.target.value)} placeholder="who, what, target…" style={{ padding: '6px', minWidth: '220px' }} /></label>
        <label>Action{' '}
          <select value={action} onChange={(e) => setAction(e.target.value)} style={{ padding: '6px' }}>
            <option value="">All actions</option>
            {actions.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </label>
        {entries && <span style={{ color: '#6b7280', alignSelf: 'center' }}>{shown.length} of {entries.length} loaded</span>}
      </div>

      {error && <p role="alert" style={errorStyle}>{error}</p>}
      {!entries && !error && <p>Loading…</p>}
      {entries && (
        <div style={{ overflowX: 'auto' }}>
          <table className="monitor-table" style={{ width: '100%', borderCollapse: 'collapse', minWidth: '760px', fontSize: '0.9em' }}>
            <thead><tr style={{ textAlign: 'left', borderBottom: '2px solid #d1d5db' }}><th>#</th><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Details</th></tr></thead>
            <tbody>
              {shown.map((e) => (
                <tr key={e.seq} style={{ borderBottom: '1px solid #e5e7eb', verticalAlign: 'top' }}>
                  <td>{e.seq}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>{new Date(e.at).toLocaleString()}</td>
                  <td style={{ overflowWrap: 'anywhere' }}>{e.actor_label}</td>
                  <td><code>{e.action}</code></td>
                  <td>{e.target_type}<div style={{ fontSize: '0.8em', color: '#6b7280', overflowWrap: 'anywhere' }}>{e.target_id}</div></td>
                  <td style={{ fontFamily: 'monospace', fontSize: '0.8em', overflowWrap: 'anywhere', maxWidth: '320px' }}>
                    {Object.keys(e.details ?? {}).length ? JSON.stringify(e.details) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {entries && more && (
        <button type="button" onClick={() => void loadOlder()} disabled={loadingMore} style={{ marginTop: '10px' }}>
          {loadingMore ? 'Loading…' : 'Load older entries'}
        </button>
      )}
    </section>
  )
}
