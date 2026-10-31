import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorText } from '../apiErrors'
import ConfirmDialog from '../components/ConfirmDialog'
import { useToast } from '../components/ToastProvider'
import { errorStyle, inputStyle, sectionStyle, type Batch, type Department } from './adminShared'

type Member = { id: string; email: string; full_name: string; college_id: string | null }

export default function BatchesAdmin() {
  const { notify } = useToast()
  const [batches, setBatches] = useState<Batch[] | null>(null)
  const [departments, setDepartments] = useState<Department[]>([])
  const [error, setError] = useState('')
  const [form, setForm] = useState({ code: '', name: '', department_id: '' })
  const [formError, setFormError] = useState('')
  const [busy, setBusy] = useState(false)

  const [openId, setOpenId] = useState<string | null>(null)
  const [edit, setEdit] = useState({ name: '', department_id: '' })
  const [members, setMembers] = useState<Member[] | null>(null)
  const [emails, setEmails] = useState('')
  const [memberError, setMemberError] = useState('')
  const [removing, setRemoving] = useState<Member | null>(null)

  const load = useCallback(async () => {
    const [b, d] = await Promise.all([api.GET('/api/v1/catalog/batches'), api.GET('/api/v1/catalog/departments')])
    if (b.error) setError(errorText(b.error, 'Could not load batches.'))
    else setBatches(b.data as Batch[])
    if (d.data) setDepartments(d.data as Department[])
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const loadMembers = useCallback(async (batchId: string) => {
    const result = await api.GET('/api/v1/admin/batches/{batch_id}/members', { params: { path: { batch_id: batchId } } })
    if (result.error) setMemberError(errorText(result.error, 'Could not load members.'))
    else setMembers(result.data as Member[])
  }, [])

  function open(batch: Batch) {
    if (openId === batch.id) {
      setOpenId(null)
      return
    }
    setOpenId(batch.id)
    setEdit({ name: batch.name, department_id: batch.department_id ?? '' })
    setMembers(null)
    setEmails('')
    setMemberError('')
    void loadMembers(batch.id)
  }

  async function add(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setFormError('')
    const result = await api.POST('/api/v1/admin/batches', {
      body: { code: form.code.trim(), name: form.name.trim(), department_id: form.department_id || null },
    })
    setBusy(false)
    if (result.error) {
      setFormError(errorText(result.error, 'Could not add the batch.'))
      return
    }
    notify(`Batch ${(result.data as Batch).code} added.`, 'success')
    setForm({ code: '', name: '', department_id: '' })
    void load()
  }

  async function saveBatch(batchId: string) {
    setBusy(true)
    const result = await api.PATCH('/api/v1/admin/batches/{batch_id}', {
      params: { path: { batch_id: batchId } },
      body: { name: edit.name.trim(), department_id: edit.department_id || null },
    })
    setBusy(false)
    if (result.error) {
      notify(errorText(result.error, 'Could not save the batch.'), 'error')
      return
    }
    notify('Batch updated.', 'success')
    void load()
  }

  async function addMembers(batchId: string) {
    const list = emails.split(/[\s,;]+/).map((e) => e.trim()).filter(Boolean)
    if (list.length === 0) return
    setBusy(true)
    setMemberError('')
    const result = await api.POST('/api/v1/admin/batches/{batch_id}/members', {
      params: { path: { batch_id: batchId } },
      body: { emails: list },
    })
    setBusy(false)
    if (result.error) {
      setMemberError(errorText(result.error, 'Could not add members.'))
      return
    }
    notify((result.data as { message: string }).message, 'success')
    setEmails('')
    void loadMembers(batchId)
  }

  async function removeMember(batchId: string, member: Member) {
    setBusy(true)
    const result = await api.DELETE('/api/v1/admin/batches/{batch_id}/members/{user_id}', {
      params: { path: { batch_id: batchId, user_id: member.id } },
    })
    setBusy(false)
    setRemoving(null)
    if (result.error) {
      notify(errorText(result.error, 'Could not remove the student.'), 'error')
      return
    }
    notify(`${member.full_name} removed from the batch.`, 'success')
    void loadMembers(batchId)
  }

  const deptCode = (id: string | null) => departments.find((d) => d.id === id)?.code ?? '—'

  return (
    <section>
      <h2 style={{ margin: 0 }}>Batches</h2>
      <form onSubmit={add} style={sectionStyle}>
        <strong>Add batch</strong>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label>Code (e.g. CSE-AIML-2025-A)<input style={{ ...inputStyle, margin: '4px 0' }} required maxLength={64} pattern="[A-Za-z0-9_.\-]+" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} /></label>
          <label>Name<input style={{ ...inputStyle, margin: '4px 0' }} required maxLength={200} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>Department
            <select style={{ ...inputStyle, margin: '4px 0' }} value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value })}>
              <option value="">None</option>
              {departments.map((d) => <option key={d.id} value={d.id}>{d.code}</option>)}
            </select>
          </label>
          <button type="submit" disabled={busy} style={{ fontWeight: 'bold' }}>Add</button>
        </div>
        {formError && <p role="alert" style={errorStyle}>{formError}</p>}
      </form>

      {error && <p role="alert" style={errorStyle}>{error}</p>}
      {!batches && !error && <p>Loading…</p>}
      {batches && batches.length === 0 && <p>No batches yet.</p>}
      {batches?.map((b) => (
        <article key={b.id} style={sectionStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
            <div><strong>{b.code}</strong> · {b.name} <span style={{ color: '#6b7280' }}>· {deptCode(b.department_id)}</span></div>
            <button type="button" onClick={() => open(b)} aria-expanded={openId === b.id}>
              {openId === b.id ? 'Close' : 'Edit & members'}
            </button>
          </div>
          {openId === b.id && (
            <div style={{ marginTop: '10px' }}>
              <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <label>Name<input style={{ ...inputStyle, margin: '4px 0' }} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
                <label>Department
                  <select style={{ ...inputStyle, margin: '4px 0' }} value={edit.department_id} onChange={(e) => setEdit({ ...edit, department_id: e.target.value })}>
                    <option value="">None</option>
                    {departments.map((d) => <option key={d.id} value={d.id}>{d.code}</option>)}
                  </select>
                </label>
                <button type="button" disabled={busy} onClick={() => void saveBatch(b.id)}>Save batch</button>
              </div>

              <h3 style={{ fontSize: '16px', margin: '14px 0 6px' }}>Students ({members?.length ?? '…'})</h3>
              {members && members.length === 0 && <p style={{ color: '#6b7280' }}>No students in this batch.</p>}
              {members && members.length > 0 && (
                <ul style={{ paddingLeft: '18px', margin: '0 0 10px' }}>
                  {members.map((m) => (
                    <li key={m.id} style={{ marginBottom: '4px' }}>
                      {m.full_name} <span style={{ color: '#6b7280' }}>{m.college_id ?? ''} {m.email}</span>{' '}
                      <button type="button" onClick={() => setRemoving(m)} style={{ fontSize: '0.85em' }}>Remove</button>
                    </li>
                  ))}
                </ul>
              )}
              <label style={{ display: 'block' }}>
                Add students by email (comma, space or one per line; they must already exist as students)
                <textarea value={emails} onChange={(e) => setEmails(e.target.value)} style={{ display: 'block', width: '100%', maxWidth: '520px', minHeight: '60px', padding: '6px', margin: '4px 0 8px' }} />
              </label>
              <button type="button" disabled={busy || !emails.trim()} onClick={() => void addMembers(b.id)}>Add students</button>
              {memberError && <p role="alert" style={errorStyle}>{memberError}</p>}
            </div>
          )}
        </article>
      ))}

      {removing && openId && (
        <ConfirmDialog
          title={`Remove ${removing.full_name} from this batch?`}
          confirmLabel="Remove"
          danger
          busy={busy}
          onCancel={() => setRemoving(null)}
          onConfirm={() => void removeMember(openId, removing)}
        >
          <p style={{ margin: 0 }}>They stop being enrolled in subjects through this batch. Past exam sessions are kept.</p>
        </ConfirmDialog>
      )}
    </section>
  )
}
