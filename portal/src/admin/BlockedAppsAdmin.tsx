import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorText } from '../apiErrors'
import ConfirmDialog from '../components/ConfirmDialog'
import { useToast } from '../components/ToastProvider'
import { errorStyle, inputStyle, sectionStyle } from './adminShared'

type BlockedApp = {
  id: string
  process: string
  label: string
  action: 'strike' | 'lock' | string
  created_at: string
}

/** Common apps, one click to add. Process names as Windows/macOS show them, lower case. */
const SUGGESTIONS: { process: string; label: string }[] = [
  { process: 'whatsapp', label: 'WhatsApp' },
  { process: 'telegram', label: 'Telegram' },
  { process: 'discord', label: 'Discord' },
  { process: 'chatgpt', label: 'ChatGPT' },
  { process: 'claude', label: 'Claude' },
  { process: 'slack', label: 'Slack' },
]

const emptyForm = { process: '', label: '', action: 'strike' as 'strike' | 'lock' }

/** Apps students must not run during exams; the student app checks for them. */
export default function BlockedAppsAdmin() {
  const { notify } = useToast()
  const [items, setItems] = useState<BlockedApp[] | null>(null)
  const [error, setError] = useState('')
  const [form, setForm] = useState(emptyForm)
  const [formError, setFormError] = useState('')
  const [busy, setBusy] = useState(false)
  const [removing, setRemoving] = useState<BlockedApp | null>(null)

  const load = useCallback(async () => {
    const result = await api.GET('/api/v1/admin/blocked-apps')
    if (result.error) setError(errorText(result.error, 'Could not load the list.'))
    else setItems(result.data as BlockedApp[])
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  async function add(entry: { process: string; label: string; action: 'strike' | 'lock' }) {
    setBusy(true)
    setFormError('')
    const result = await api.POST('/api/v1/admin/blocked-apps', {
      body: { process: entry.process.trim(), label: entry.label.trim(), action: entry.action },
    })
    setBusy(false)
    if (result.error) {
      setFormError(errorText(result.error, 'Could not add the app.'))
      return false
    }
    notify(`${entry.label} added. Students' apps check for it from their next exam.`, 'success')
    void load()
    return true
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (await add(form)) setForm(emptyForm)
  }

  async function remove(app: BlockedApp) {
    setBusy(true)
    const result = await api.DELETE('/api/v1/admin/blocked-apps/{app_id}', { params: { path: { app_id: app.id } } })
    setBusy(false)
    setRemoving(null)
    if (result.error) {
      notify(errorText(result.error, 'Could not remove the app.'), 'error')
      return
    }
    notify(`${app.label} removed.`, 'success')
    void load()
  }

  const listed = new Set((items ?? []).map((a) => a.process))
  const suggestions = SUGGESTIONS.filter((s) => !listed.has(s.process))

  return (
    <section>
      <h2 style={{ margin: 0 }}>Blocked apps</h2>
      <p style={{ color: '#4b5563' }}>
        Apps students must close before an exam (chat apps, AI assistants…). The student app checks running apps
        before Start and every few seconds during the exam; no app update is needed. Remote-control and
        screen-recording tools are always blocked.
      </p>

      <form onSubmit={submit} style={sectionStyle}>
        <strong>Add an app</strong>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label>
            Process name
            <input
              style={{ ...inputStyle, margin: '4px 0', width: '200px', fontFamily: 'monospace' }}
              required
              maxLength={68}
              placeholder="e.g. WhatsApp.exe"
              value={form.process}
              onChange={(e) => setForm({ ...form, process: e.target.value })}
            />
          </label>
          <label>
            Name shown to students
            <input
              style={{ ...inputStyle, margin: '4px 0', width: '200px' }}
              required
              maxLength={80}
              placeholder="e.g. WhatsApp"
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
            />
          </label>
          <label>
            If it is running during an exam
            <select
              style={{ ...inputStyle, margin: '4px 0' }}
              value={form.action}
              onChange={(e) => setForm({ ...form, action: e.target.value as 'strike' | 'lock' })}
            >
              <option value="strike">Count one strike</option>
              <option value="lock">Pause the exam (professor resumes)</option>
            </select>
          </label>
        </div>
        <p style={{ fontSize: '0.85em', color: '#6b7280', margin: '4px 0 8px' }}>
          The process name is what Task Manager (Details tab) or Activity Monitor shows; “.exe” is optional.
        </p>
        {formError && <p role="alert" style={errorStyle}>{formError}</p>}
        <button type="submit" disabled={busy} style={{ fontWeight: 'bold' }}>Add app</button>
        {suggestions.length > 0 && (
          <div style={{ marginTop: '12px' }}>
            <span style={{ fontSize: '0.9em', color: '#4b5563' }}>Common ones: </span>
            {suggestions.map((s) => (
              <button
                key={s.process}
                type="button"
                disabled={busy}
                onClick={() => void add({ ...s, action: 'strike' })}
                style={{ margin: '4px 6px 0 0' }}
              >
                + {s.label}
              </button>
            ))}
          </div>
        )}
      </form>

      {error && <p role="alert" style={errorStyle}>{error}</p>}
      {!items && !error && <p>Loading…</p>}
      {items && items.length === 0 && <p>No apps on the list yet.</p>}
      {items && items.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: '12px' }}>
          <table className="monitor-table" style={{ width: '100%', borderCollapse: 'collapse', minWidth: '560px' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '2px solid #d1d5db' }}>
                <th>App</th><th>Process</th><th>During an exam</th><th>Added</th><th />
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id} style={{ borderBottom: '1px solid #e5e7eb' }}>
                  <td><strong>{a.label}</strong></td>
                  <td style={{ fontFamily: 'monospace' }}>{a.process}</td>
                  <td style={{ color: a.action === 'lock' ? '#b91c1c' : undefined }}>
                    {a.action === 'lock' ? 'Pauses the exam' : 'One strike'}
                  </td>
                  <td>{new Date(a.created_at).toLocaleDateString()}</td>
                  <td><button type="button" onClick={() => setRemoving(a)}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {removing && (
        <ConfirmDialog
          title={`Remove ${removing.label}?`}
          confirmLabel="Remove"
          busy={busy}
          onCancel={() => setRemoving(null)}
          onConfirm={() => void remove(removing)}
        >
          <p style={{ margin: 0 }}>Students may run it during exams again (from their next exam).</p>
        </ConfirmDialog>
      )}
    </section>
  )
}
