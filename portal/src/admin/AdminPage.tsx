import type { ReactNode } from 'react'
import AuditAdmin from './AuditAdmin'
import BlockedAppsAdmin from './BlockedAppsAdmin'
import BatchesAdmin from './BatchesAdmin'
import DepartmentsAdmin from './DepartmentsAdmin'
import ImportAdmin from './ImportAdmin'
import LanguagesAdmin from './LanguagesAdmin'
import ReleasesAdmin from './ReleasesAdmin'
import UsersAdmin from './UsersAdmin'
import { ADMIN_SECTIONS, type AdminSection } from './adminShared'

type Props = {
  section: AdminSection
  onSection: (section: AdminSection) => void
  userName: string
  currentUserId: string
  /** The existing subject overview (names and counts only: admins can't read questions or answers). */
  subjects: ReactNode
  /** Change password + sign out. */
  account: ReactNode
  /** Always-visible sign out, at the bottom of the side menu. */
  onSignOut: () => void
}

/** Step 12: admin area with a side menu. Only rendered for admins (see App.tsx). */
export default function AdminPage({ section, onSection, userName, currentUserId, subjects, account, onSignOut }: Props) {
  return (
    <main className="admin-layout" style={{ fontFamily: 'Arial, sans-serif', textAlign: 'left' }}>
      <nav aria-label="Admin sections" className="admin-nav">
        <div style={{ fontWeight: 'bold', fontSize: '1.1em', marginBottom: '4px' }}>UAI-Aegis Admin</div>
        <div style={{ fontSize: '0.85em', color: '#6b7280', marginBottom: '12px', overflowWrap: 'anywhere' }}>{userName}</div>
        {ADMIN_SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            aria-current={section === s.id ? 'page' : undefined}
            className={section === s.id ? 'admin-nav__item is-active' : 'admin-nav__item'}
            onClick={() => onSection(s.id)}
          >
            {s.label}
          </button>
        ))}
        <button type="button" className="admin-nav__item" onClick={onSignOut} style={{ marginTop: '10px', color: '#b91c1c' }}>
          Sign out
        </button>
      </nav>
      <div className="admin-content">
        {section === 'subjects' && subjects}
        {section === 'users' && <UsersAdmin currentUserId={currentUserId} />}
        {section === 'import' && <ImportAdmin />}
        {section === 'departments' && <DepartmentsAdmin />}
        {section === 'batches' && <BatchesAdmin />}
        {section === 'languages' && <LanguagesAdmin />}
        {section === 'releases' && <ReleasesAdmin />}
        {section === 'blocked-apps' && <BlockedAppsAdmin />}
        {section === 'audit' && <AuditAdmin />}
        {section === 'account' && account}
      </div>
    </main>
  )
}
