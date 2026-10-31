import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorText } from '../apiErrors'
import ConfirmDialog from '../components/ConfirmDialog'
import { useToast } from '../components/ToastProvider'
import {
  ROLES,
  errorStyle,
  generatePassword,
  inputStyle,
  sectionStyle,
  type Department,
  type RoleName,
} from './adminShared'

type UserOut = {
  id: string
  email: string
  full_name: string
  college_id: string | null
  role: RoleName
  department_id: string | null
  is_active: boolean
  must_change_password: boolean
  created_at: string
}

const PAGE = 25

const emptyNew = { email: '', full_name: '', role: 'student' as RoleName, college_id: '', department_id: '', temporary_password: '' }

export default function UsersAdmin({ currentUserId }: { currentUserId: string }) {
  const { notify } = useToast()
  const [q, setQ] = useState('')
  const [search, setSearch] = useState('')
  const [role, setRole] = useState<'' | RoleName>('')
  const [offset, setOffset] = useState(0)
  const [list, setList] = useState<{ total: number; items: UserOut[] } | null>(null)
  const [error, setError] = useState('')
  const [departments, setDepartments] = useState<Department[]>([])

  const [adding, setAdding] = useState(false)
  const [newUser, setNewUser] = useState(emptyNew)
  const [editing, setEditing] = useState<UserOut | null>(null)
  const [edit, setEdit] = useState({ full_name: '', role: 'student' as RoleName, college_id: '', department_id: '' })
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')

  const [pwFor, setPwFor] = useState<UserOut | null>(null)
  const [shownPassword, setShownPassword] = useState<{ user: UserOut; password: string } | null>(null)
  const [activeFor, setActiveFor] = useState<UserOut | null>(null)

  const load = useCallback(async () => {
    const result = await api.GET('/api/v1/admin/users', {
      params: { query: { q: search, limit: PAGE, offset, ...(role ? { role } : {}) } },
    })
    if (result.error) {
      setError(errorText(result.error, 'Could not load users.'))
      return
    }
    setError('')
    setList(result.data as { total: number; items: UserOut[] })
  }, [search, role, offset])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  // Search as you type, about 300 ms after the last key.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(q.trim())
      setOffset(0)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [q])

  useEffect(() => {
    void api.GET('/api/v1/catalog/departments').then((r) => {
      if (r.data) setDepartments(r.data as Department[])
    })
  }, [])

  const deptCode = (id: string | null) => departments.find((d) => d.id === id)?.code ?? '—'

  async function createUser(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError('')
    setBusy(true)
    const result = await api.POST('/api/v1/admin/users', {
      body: {
        email: newUser.email.trim(),
        full_name: newUser.full_name.trim(),
        role: newUser.role,
        college_id: newUser.college_id.trim() || null,
        department_id: newUser.department_id || null,
        temporary_password: newUser.temporary_password || null,
      },
    })
    setBusy(false)
    if (result.error) {
      setFormError(errorText(result.error, 'Could not add the user.'))
      return
    }
    const created = result.data as UserOut
    if (newUser.temporary_password) setShownPassword({ user: created, password: newUser.temporary_password })
    notify(`${created.full_name} added.`, 'success')
    setNewUser(emptyNew)
    setAdding(false)
    void load()
  }

  function startEdit(user: UserOut) {
    setFormError('')
    setEditing(user)
    setEdit({
      full_name: user.full_name,
      role: user.role,
      college_id: user.college_id ?? '',
      department_id: user.department_id ?? '',
    })
  }

  async function saveEdit() {
    if (!editing) return
    setBusy(true)
    setFormError('')
    const result = await api.PATCH('/api/v1/admin/users/{user_id}', {
      params: { path: { user_id: editing.id } },
      body: {
        full_name: edit.full_name.trim(),
        role: edit.role,
        college_id: edit.college_id.trim() || null,
        department_id: edit.department_id || null,
      },
    })
    setBusy(false)
    if (result.error) {
      setFormError(errorText(result.error, 'Could not save.'))
      return
    }
    notify('User updated.' + (edit.role !== editing.role ? ' They were signed out so the new role applies.' : ''), 'success')
    setEditing(null)
    void load()
  }

  async function setTemporaryPassword(user: UserOut) {
    const password = generatePassword()
    setBusy(true)
    const result = await api.POST('/api/v1/admin/users/{user_id}/temporary-password', {
      params: { path: { user_id: user.id } },
      body: { password },
    })
    setBusy(false)
    setPwFor(null)
    if (result.error) {
      notify(errorText(result.error, 'Could not set a temporary password.'), 'error')
      return
    }
    setShownPassword({ user, password })
    void load()
  }

  async function toggleActive(user: UserOut) {
    setBusy(true)
    const result = await api.PATCH('/api/v1/admin/users/{user_id}', {
      params: { path: { user_id: user.id } },
      body: { is_active: !user.is_active },
    })
    setBusy(false)
    setActiveFor(null)
    if (result.error) {
      notify(errorText(result.error, 'Could not change the account.'), 'error')
      return
    }
    notify(user.is_active ? `${user.full_name} deactivated and signed out.` : `${user.full_name} reactivated.`, 'success')
    void load()
  }

  const total = list?.total ?? 0

  return (
    <section>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0 }}>Users</h2>
        {!adding && <button type="button" onClick={() => { setAdding(true); setFormError('') }}>+ Add user</button>}
      </div>

      {adding && (
        <form onSubmit={createUser} style={sectionStyle}>
          <strong>New user</strong>
          <label style={{ display: 'block', marginTop: '8px' }}>Email<input style={inputStyle} type="email" required value={newUser.email} onChange={(e) => setNewUser({ ...newUser, email: e.target.value })} /></label>
          <label style={{ display: 'block' }}>Full name<input style={inputStyle} required maxLength={200} value={newUser.full_name} onChange={(e) => setNewUser({ ...newUser, full_name: e.target.value })} /></label>
          <label style={{ display: 'block' }}>Role
            <select style={inputStyle} value={newUser.role} onChange={(e) => setNewUser({ ...newUser, role: e.target.value as RoleName })}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <label style={{ display: 'block' }}>College ID (optional)<input style={inputStyle} maxLength={64} value={newUser.college_id} onChange={(e) => setNewUser({ ...newUser, college_id: e.target.value })} /></label>
          <label style={{ display: 'block' }}>Department (optional)
            <select style={inputStyle} value={newUser.department_id} onChange={(e) => setNewUser({ ...newUser, department_id: e.target.value })}>
              <option value="">None</option>
              {departments.map((d) => <option key={d.id} value={d.id}>{d.code} — {d.name}</option>)}
            </select>
          </label>
          <label style={{ display: 'block' }}>Temporary password (optional; they must change it at first sign-in)
            <span style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
              <input style={{ ...inputStyle, margin: '4px 0' }} value={newUser.temporary_password} onChange={(e) => setNewUser({ ...newUser, temporary_password: e.target.value })} />
              <button type="button" onClick={() => setNewUser({ ...newUser, temporary_password: generatePassword() })}>Generate</button>
            </span>
          </label>
          {formError && <p role="alert" style={errorStyle}>{formError}</p>}
          <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
            <button type="submit" disabled={busy} style={{ fontWeight: 'bold' }}>{busy ? 'Adding…' : 'Add user'}</button>
            <button type="button" disabled={busy} onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </form>
      )}

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', margin: '12px 0' }}>
        <label>Search{' '}<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="name, email or college ID" style={{ padding: '6px', minWidth: '220px' }} /></label>
        <label>Role{' '}
          <select value={role} onChange={(e) => { setRole(e.target.value as '' | RoleName); setOffset(0) }} style={{ padding: '6px' }}>
            <option value="">All</option>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
      </div>

      {error && <p role="alert" style={errorStyle}>{error}</p>}
      {!list && !error && <p>Loading users…</p>}
      {list && list.items.length === 0 && <p>No users match.</p>}

      {list && list.items.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="monitor-table" style={{ width: '100%', borderCollapse: 'collapse', minWidth: '760px' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '2px solid #d1d5db' }}>
                <th>Name</th><th>Email</th><th>College ID</th><th>Role</th><th>Dept</th><th>Status</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.items.map((u) => (
                <tr key={u.id} style={{ borderBottom: '1px solid #e5e7eb', verticalAlign: 'top', opacity: u.is_active ? 1 : 0.6 }}>
                  {editing?.id === u.id ? (
                    <td colSpan={7}>
                      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
                        <label>Full name<input style={{ ...inputStyle, margin: '4px 0' }} value={edit.full_name} onChange={(e) => setEdit({ ...edit, full_name: e.target.value })} /></label>
                        <label>Role
                          <select style={{ ...inputStyle, margin: '4px 0' }} value={edit.role} disabled={u.id === currentUserId} onChange={(e) => setEdit({ ...edit, role: e.target.value as RoleName })}>
                            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                          </select>
                        </label>
                        <label>College ID<input style={{ ...inputStyle, margin: '4px 0' }} value={edit.college_id} onChange={(e) => setEdit({ ...edit, college_id: e.target.value })} /></label>
                        <label>Department
                          <select style={{ ...inputStyle, margin: '4px 0' }} value={edit.department_id} onChange={(e) => setEdit({ ...edit, department_id: e.target.value })}>
                            <option value="">None</option>
                            {departments.map((d) => <option key={d.id} value={d.id}>{d.code}</option>)}
                          </select>
                        </label>
                        <button type="button" disabled={busy} onClick={() => void saveEdit()} style={{ fontWeight: 'bold' }}>Save</button>
                        <button type="button" disabled={busy} onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                      {u.id === currentUserId && <p style={{ margin: '4px 0', color: '#6b7280' }}>You can’t change your own role.</p>}
                      {formError && <p role="alert" style={errorStyle}>{formError}</p>}
                    </td>
                  ) : (
                    <>
                      <td>{u.full_name}</td>
                      <td style={{ overflowWrap: 'anywhere' }}>{u.email}</td>
                      <td>{u.college_id ?? '—'}</td>
                      <td>{u.role}</td>
                      <td>{deptCode(u.department_id)}</td>
                      <td>
                        {u.is_active ? 'Active' : 'Deactivated'}
                        {u.must_change_password && <div style={{ fontSize: '0.85em', color: '#92400e' }}>must change password</div>}
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <button type="button" onClick={() => startEdit(u)}>Edit</button>{' '}
                        <button type="button" onClick={() => setPwFor(u)}>Temporary password</button>{' '}
                        {u.id !== currentUserId && (
                          <button type="button" onClick={() => setActiveFor(u)}>{u.is_active ? 'Deactivate' : 'Reactivate'}</button>
                        )}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > PAGE && (
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginTop: '10px' }}>
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Previous</button>
          <span>{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
          <button type="button" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next →</button>
        </div>
      )}

      {pwFor && (
        <ConfirmDialog
          title={`Set a temporary password for ${pwFor.full_name}?`}
          confirmLabel="Generate and set"
          busy={busy}
          onCancel={() => setPwFor(null)}
          onConfirm={() => void setTemporaryPassword(pwFor)}
        >
          <p style={{ margin: 0 }}>
            A new random password is set and shown to you <strong>once</strong>. Their current
            sessions are signed out, and they must change it at next sign-in.
          </p>
        </ConfirmDialog>
      )}

      {activeFor && (
        <ConfirmDialog
          title={activeFor.is_active ? `Deactivate ${activeFor.full_name}?` : `Reactivate ${activeFor.full_name}?`}
          confirmLabel={activeFor.is_active ? 'Deactivate' : 'Reactivate'}
          danger={activeFor.is_active}
          busy={busy}
          onCancel={() => setActiveFor(null)}
          onConfirm={() => void toggleActive(activeFor)}
        >
          <p style={{ margin: 0 }}>
            {activeFor.is_active
              ? 'They are signed out everywhere and can’t sign in until reactivated. Their data is kept.'
              : 'They can sign in again.'}
          </p>
        </ConfirmDialog>
      )}

      {shownPassword && (
        <ConfirmDialog
          title="Temporary password"
          confirmLabel="I’ve copied it"
          onCancel={() => setShownPassword(null)}
          onConfirm={() => setShownPassword(null)}
        >
          <p style={{ marginTop: 0 }}>For <strong>{shownPassword.user.full_name}</strong> ({shownPassword.user.email}). It won’t be shown again.</p>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <code data-testid="temp-password" style={{ fontSize: '1.3em', padding: '6px 10px', userSelect: 'all' }}>{shownPassword.password}</code>
            <button type="button" onClick={() => void navigator.clipboard.writeText(shownPassword.password).then(() => notify('Copied.', 'success'))}>Copy</button>
          </div>
        </ConfirmDialog>
      )}
    </section>
  )
}
