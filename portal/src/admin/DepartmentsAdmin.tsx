import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorText } from '../apiErrors'
import { useToast } from '../components/ToastProvider'
import { errorStyle, inputStyle, sectionStyle, type Department } from './adminShared'

export default function DepartmentsAdmin() {
  const { notify } = useToast()
  const [items, setItems] = useState<Department[] | null>(null)
  const [error, setError] = useState('')
  const [form, setForm] = useState({ code: '', name: '' })
  const [editing, setEditing] = useState<string | null>(null)
  const [edit, setEdit] = useState({ code: '', name: '' })
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')

  const load = useCallback(async () => {
    const result = await api.GET('/api/v1/catalog/departments')
    if (result.error) setError(errorText(result.error, 'Could not load departments.'))
    else setItems(result.data as Department[])
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  async function add(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setFormError('')
    const result = await api.POST('/api/v1/admin/departments', { body: { code: form.code.trim(), name: form.name.trim() } })
    setBusy(false)
    if (result.error) {
      setFormError(errorText(result.error, 'Could not add the department.'))
      return
    }
    notify(`Department ${(result.data as Department).code} added.`, 'success')
    setForm({ code: '', name: '' })
    void load()
  }

  async function save(id: string) {
    setBusy(true)
    setFormError('')
    const result = await api.PATCH('/api/v1/admin/departments/{department_id}', {
      params: { path: { department_id: id } },
      body: { code: edit.code.trim(), name: edit.name.trim() },
    })
    setBusy(false)
    if (result.error) {
      setFormError(errorText(result.error, 'Could not save.'))
      return
    }
    notify('Department updated.', 'success')
    setEditing(null)
    void load()
  }

  return (
    <section>
      <h2 style={{ margin: 0 }}>Departments</h2>
      <form onSubmit={add} style={sectionStyle}>
        <strong>Add department</strong>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label>Code (e.g. CSE)<input style={{ ...inputStyle, margin: '4px 0' }} required maxLength={32} pattern="[A-Za-z0-9_.\-]+" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} /></label>
          <label>Name<input style={{ ...inputStyle, margin: '4px 0' }} required maxLength={200} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <button type="submit" disabled={busy} style={{ fontWeight: 'bold' }}>Add</button>
        </div>
        {formError && !editing && <p role="alert" style={errorStyle}>{formError}</p>}
      </form>

      {error && <p role="alert" style={errorStyle}>{error}</p>}
      {!items && !error && <p>Loading…</p>}
      {items && items.length === 0 && <p>No departments yet.</p>}
      {items && items.length > 0 && (
        <table className="monitor-table" style={{ width: '100%', borderCollapse: 'collapse', marginTop: '12px' }}>
          <thead><tr style={{ textAlign: 'left', borderBottom: '2px solid #d1d5db' }}><th>Code</th><th>Name</th><th /></tr></thead>
          <tbody>
            {items.map((d) => (
              <tr key={d.id} style={{ borderBottom: '1px solid #e5e7eb' }}>
                {editing === d.id ? (
                  <>
                    <td><input value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value })} style={{ padding: '5px', width: '100px' }} /></td>
                    <td><input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} style={{ padding: '5px', width: '100%', maxWidth: '320px' }} />
                      {formError && <p role="alert" style={errorStyle}>{formError}</p>}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button type="button" disabled={busy} onClick={() => void save(d.id)}>Save</button>{' '}
                      <button type="button" onClick={() => setEditing(null)}>Cancel</button>
                    </td>
                  </>
                ) : (
                  <>
                    <td><strong>{d.code}</strong></td>
                    <td>{d.name}</td>
                    <td><button type="button" onClick={() => { setEditing(d.id); setEdit({ code: d.code, name: d.name }); setFormError('') }}>Edit</button></td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
