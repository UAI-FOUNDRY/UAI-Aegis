import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorText } from '../apiErrors'
import ConfirmDialog from '../components/ConfirmDialog'
import { useToast } from '../components/ToastProvider'
import { errorStyle, inputStyle, sectionStyle } from './adminShared'

type Release = {
  id: string
  version: string
  platform: 'windows' | 'macos' | 'linux'
  build_sha256: string
  status: 'allowed' | 'revoked' | string
  notes: string | null
  created_at: string
}

const emptyForm = { version: '', platform: 'windows' as Release['platform'], build_sha256: '', notes: '' }

/** "App releases" = the desktop-app builds students are allowed to use. */
export default function ReleasesAdmin() {
  const { notify } = useToast()
  const [items, setItems] = useState<Release[] | null>(null)
  const [error, setError] = useState('')
  const [form, setForm] = useState(emptyForm)
  const [formError, setFormError] = useState('')
  const [busy, setBusy] = useState(false)
  const [changing, setChanging] = useState<Release | null>(null)

  const load = useCallback(async () => {
    const result = await api.GET('/api/v1/admin/client-releases')
    if (result.error) setError(errorText(result.error, 'Could not load releases.'))
    else setItems(result.data as Release[])
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  async function add(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setFormError('')
    const result = await api.POST('/api/v1/admin/client-releases', {
      body: {
        version: form.version.trim(),
        platform: form.platform,
        build_sha256: form.build_sha256.trim().toLowerCase(),
        notes: form.notes.trim() || null,
      },
    })
    setBusy(false)
    if (result.error) {
      setFormError(errorText(result.error, 'Could not add the release.'))
      return
    }
    notify(`Version ${form.version} (${form.platform}) allowed.`, 'success')
    setForm(emptyForm)
    void load()
  }

  async function setStatus(release: Release) {
    const status = release.status === 'allowed' ? 'revoked' : 'allowed'
    setBusy(true)
    const result = await api.PATCH('/api/v1/admin/client-releases/{release_id}', {
      params: { path: { release_id: release.id } },
      body: { status },
    })
    setBusy(false)
    setChanging(null)
    if (result.error) {
      notify(errorText(result.error, 'Could not change the release.'), 'error')
      return
    }
    notify(`Version ${release.version} ${status}.`, 'success')
    void load()
  }

  return (
    <section>
      <h2 style={{ margin: 0 }}>App releases</h2>
      <p style={{ color: '#4b5563' }}>The student desktop-app builds that may sign in. Revoke a build to block it.</p>
      <form onSubmit={add} style={sectionStyle}>
        <strong>Allow a new build</strong>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label>Version<input style={{ ...inputStyle, margin: '4px 0', width: '120px' }} required pattern="[0-9A-Za-z.+\-]+" maxLength={32} value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} /></label>
          <label>Platform
            <select style={{ ...inputStyle, margin: '4px 0' }} value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value as Release['platform'] })}>
              <option value="windows">Windows</option><option value="macos">macOS</option><option value="linux">Linux</option>
            </select>
          </label>
          <label style={{ flex: 1, minWidth: '260px' }}>Build SHA-256 (64 hex characters)<input style={{ ...inputStyle, maxWidth: '100%', margin: '4px 0', fontFamily: 'monospace' }} required pattern="[0-9a-fA-F]{64}" value={form.build_sha256} onChange={(e) => setForm({ ...form, build_sha256: e.target.value })} /></label>
        </div>
        <label style={{ display: 'block' }}>Notes (optional)<input style={{ ...inputStyle, maxWidth: '520px' }} maxLength={500} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
        {formError && <p role="alert" style={errorStyle}>{formError}</p>}
        <button type="submit" disabled={busy} style={{ fontWeight: 'bold' }}>Allow build</button>
      </form>

      {error && <p role="alert" style={errorStyle}>{error}</p>}
      {!items && !error && <p>Loading…</p>}
      {items && items.length === 0 && <p>No builds registered yet.</p>}
      {items && items.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: '12px' }}>
          <table className="monitor-table" style={{ width: '100%', borderCollapse: 'collapse', minWidth: '720px' }}>
            <thead><tr style={{ textAlign: 'left', borderBottom: '2px solid #d1d5db' }}><th>Version</th><th>Platform</th><th>SHA-256</th><th>Status</th><th>Added</th><th /></tr></thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} style={{ borderBottom: '1px solid #e5e7eb', verticalAlign: 'top' }}>
                  <td><strong>{r.version}</strong>{r.notes && <div style={{ fontSize: '0.85em', color: '#6b7280' }}>{r.notes}</div>}</td>
                  <td>{r.platform}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: '0.8em', overflowWrap: 'anywhere', maxWidth: '260px' }}>{r.build_sha256}</td>
                  <td style={{ color: r.status === 'allowed' ? '#166534' : '#b91c1c', fontWeight: 'bold' }}>{r.status}</td>
                  <td>{new Date(r.created_at).toLocaleDateString()}</td>
                  <td><button type="button" onClick={() => setChanging(r)}>{r.status === 'allowed' ? 'Revoke' : 'Allow again'}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {changing && (
        <ConfirmDialog
          title={`${changing.status === 'allowed' ? 'Revoke' : 'Allow'} version ${changing.version} (${changing.platform})?`}
          confirmLabel={changing.status === 'allowed' ? 'Revoke' : 'Allow'}
          danger={changing.status === 'allowed'}
          busy={busy}
          onCancel={() => setChanging(null)}
          onConfirm={() => void setStatus(changing)}
        >
          <p style={{ margin: 0 }}>
            {changing.status === 'allowed'
              ? 'Students on this build can no longer sign in to exams until they update.'
              : 'Students on this build can sign in again.'}
          </p>
        </ConfirmDialog>
      )}
    </section>
  )
}
