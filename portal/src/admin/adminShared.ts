/** Small helpers shared by the admin sections (Step 12). */

export const sectionStyle = {
  border: '1px solid #e5e7eb',
  borderRadius: '10px',
  padding: '14px 16px',
  marginTop: '14px',
  background: '#ffffff',
}

export const inputStyle = {
  display: 'block',
  width: '100%',
  maxWidth: '360px',
  boxSizing: 'border-box' as const,
  padding: '7px',
  margin: '4px 0 10px',
}

export const errorStyle = { color: '#b91c1c', margin: '6px 0' }

/** The signed-in user's bearer token (same storage as api/client.ts). */
export function sessionToken(): string | null {
  const saved = localStorage.getItem('aegis_session')
  if (!saved) return null
  try {
    return (JSON.parse(saved) as { token?: string }).token ?? null
  } catch {
    return null
  }
}

/**
 * A strong temporary password (14 chars, no look-alike characters).
 * The backend needs at least 10 characters and 4 different ones.
 */
export function generatePassword(length = 14): string {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#%*?'
  const bytes = new Uint32Array(length)
  for (;;) {
    crypto.getRandomValues(bytes)
    const password = Array.from(bytes, (b) => alphabet[b % alphabet.length]).join('')
    if (/[A-Z]/.test(password) && /[a-z]/.test(password) && /\d/.test(password) && new Set(password).size >= 6) {
      return password
    }
  }
}

export const ADMIN_SECTIONS = [
  { id: 'subjects', label: 'Subjects' },
  { id: 'users', label: 'Users' },
  { id: 'import', label: 'CSV import' },
  { id: 'departments', label: 'Departments' },
  { id: 'batches', label: 'Batches' },
  { id: 'languages', label: 'Languages' },
  { id: 'releases', label: 'App releases' },
  { id: 'audit', label: 'Audit' },
  { id: 'account', label: 'My account' },
] as const

export type AdminSection = (typeof ADMIN_SECTIONS)[number]['id']

export function isAdminSection(value: string | undefined): value is AdminSection {
  return ADMIN_SECTIONS.some((s) => s.id === value)
}

export const ROLES =['student', 'professor', 'admin'] as const
export type RoleName = (typeof ROLES)[number]

export type Department = { id: string; code: string; name: string }
export type Batch = { id: string; code: string; name: string; department_id: string | null }
