
import { useEffect, useState } from 'react'
import { api } from './api/client'
import QuestionWizard from './QuestionWizard'
import ExamsTab from './ExamsTab'
import { errorText } from './apiErrors'

type Subject = {
  id: string
  code: string
  name: string
  archived: boolean
  my_role?: string | null
  language_keys: string[]
  batch_ids: string[]
}

type Language = {
  id: string
  key: string
  name: string
  is_enabled: boolean
}

type Batch = {
  id: string
  code: string
  name: string
  department_id: string | null
}

type Professor = {
  id: string
  email: string
  full_name: string
  college_id: string | null
}

type Grant = {
  id: string
  role: 'owner' | 'editor' | 'grader' | 'viewer'
  exam_id: string | null
  user: Professor
  created_at: string
}

type Tab = 'Questions' | 'Exams' | 'Settings' | 'Sharing'

const pageStyle = {
  maxWidth: '1000px',
  margin: '24px auto',
  padding: '24px',
  fontFamily: 'Arial, sans-serif',
}

const cardStyle = {
  border: '1px solid #d1d5db',
  borderRadius: '12px',
  padding: '18px',
  marginTop: '16px',
  background: '#ffffff',
  color: '#111827',
}

const inputStyle = {
  display: 'block',
  width: '100%',
  maxWidth: '500px',
  boxSizing: 'border-box' as const,
  padding: '10px',
  margin: '8px 0 16px',
}

type Props = {
  subjectId: string
  onBack: () => void
  /** Tab to show first, e.g. 'Exams' when coming back from the live monitor. */
  initialTab?: Tab
}

function SubjectPage({ subjectId, onBack, initialTab }: Props) {
  const [subject, setSubject] = useState<Subject | null>(null)
  const [languages, setLanguages] = useState<Language[]>([])
  const [batches, setBatches] = useState<Batch[]>([])
  const [selectedLanguages, setSelectedLanguages] = useState<string[]>([])
  const [selectedBatches, setSelectedBatches] = useState<string[]>([])
  const [grants, setGrants] = useState<Grant[]>([])
  const [professors, setProfessors] = useState<Professor[]>([])
  const [search, setSearch] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Grant['role']>('viewer')
  const [tab, setTab] = useState<Tab>(initialTab ?? 'Settings')
  // Set when "Edit question" is clicked on an exam; cleared on manual tab changes.
  const [questionFromExam, setQuestionFromExam] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  // Only grant frontend permissions for explicitly recognized roles.
  const canEditSettings =
    subject?.my_role === 'owner' ||
    subject?.my_role === 'editor'

  const canManageSharing = subject?.my_role === 'owner'

  async function loadSubject() {
    setLoading(true)
    setError('')

    try {
      const result = await api.GET('/api/v1/subjects/{subject_id}', {
        params: { path: { subject_id: subjectId } },
      })

      if (result.error) {
        throw new Error(
          `Could not load subject (HTTP ${result.response.status}).`,
        )
      }

      const current = result.data as Subject

      setSubject(current)
      setSelectedLanguages(current.language_keys ?? [])
      setSelectedBatches(current.batch_ids ?? [])
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not load subject.',
      )
    } finally {
      setLoading(false)
    }
  }

  async function loadSettingsCatalogs() {
    try {
      const [languageResult, batchResult] = await Promise.all([
        api.GET('/api/v1/catalog/languages'),
        api.GET('/api/v1/catalog/batches'),
      ])

      if (languageResult.error) {
        throw new Error('Could not load available languages.')
      }

      if (batchResult.error) {
        throw new Error('Could not load available batches.')
      }

      setLanguages((languageResult.data ?? []) as Language[])
      setBatches((batchResult.data ?? []) as Batch[])
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not load settings.',
      )
    }
  }

  async function loadSharing() {
    // Never request the sharing endpoint for a non-owner.
    if (subject?.my_role !== 'owner') {
      setGrants([])
      return
    }

    try {
      const result = await api.GET('/api/v1/subjects/{subject_id}/access', {
        params: { path: { subject_id: subjectId } },
      })

      if (result.error) {
        throw new Error(
          `Could not load sharing access (HTTP ${result.response.status}).`,
        )
      }

      setGrants((result.data ?? []) as Grant[])
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'Could not load sharing access.',
      )
    }
  }

  // Load subject details and settings catalogs first.
  useEffect(() => {
    setSubject(null)
    setGrants([])
    setTab(initialTab ?? 'Settings')
    setMessage('')
    setError('')

    void loadSubject()
    void loadSettingsCatalogs()

    // Load initial data when the selected subject changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectId])

  // The sharing request runs only after the subject role is known.
  useEffect(() => {
    if (subject?.my_role === 'owner') {
      void loadSharing()
    } else {
      setGrants([])

      // Prevent a non-owner from remaining on the Sharing tab.
      setTab((currentTab) =>
        currentTab === 'Sharing' ? 'Settings' : currentTab,
      )
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subject?.my_role, subjectId])

  // Used by the exam form's Batches section. Adds one existing batch to the
  // subject's saved batches; the subject is updated from the response so the
  // Exams tab (and its unsaved form) stays mounted.
  async function linkBatchToSubject(batchId: string) {
    if (!subject) throw new Error('Subject not loaded.')
    if (subject.batch_ids.includes(batchId)) return

    const result = await api.PUT('/api/v1/subjects/{subject_id}/batches', {
      params: { path: { subject_id: subjectId } },
      body: { batch_ids: [...subject.batch_ids, batchId] },
    })

    if (result.error) {
      throw new Error(errorText(result.error, 'Could not link the batch.'))
    }

    setSubject(result.data as Subject)
    // Keep any unsaved ticks on the Settings tab, plus the new batch.
    setSelectedBatches((current) =>
      current.includes(batchId) ? current : [...current, batchId],
    )
  }

  async function saveSettings() {
    if (!canEditSettings) {
      setError('You do not have permission to edit subject settings.')
      return
    }

    setSaving(true)
    setMessage('')
    setError('')

    try {
      const [languageResult, batchResult] = await Promise.all([
        api.PUT('/api/v1/subjects/{subject_id}/languages', {
          params: { path: { subject_id: subjectId } },
          body: { keys: selectedLanguages },
        }),
        api.PUT('/api/v1/subjects/{subject_id}/batches', {
          params: { path: { subject_id: subjectId } },
          body: { batch_ids: selectedBatches },
        }),
      ])

      if (languageResult.error || batchResult.error) {
        throw new Error(
          'Could not save all settings. Check your access and try again.',
        )
      }

      await loadSubject()
      setMessage('Language and batch settings saved successfully.')
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not save settings.',
      )
    } finally {
      setSaving(false)
    }
  }

  async function searchProfessors() {
    if (!canManageSharing) {
      setError('You do not have permission to manage subject sharing.')
      return
    }

    setError('')
    setProfessors([])

    try {
      const result = await api.GET('/api/v1/catalog/professors', {
        params: { query: { q: search.trim() || undefined } },
      })

      if (result.error) {
        throw new Error(
          `Professor search failed (HTTP ${result.response.status}).`,
        )
      }

      setProfessors((result.data ?? []) as Professor[])
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'Could not search professors.',
      )
    }
  }

  async function grantAccess(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!canManageSharing) {
      setError('You do not have permission to manage subject sharing.')
      return
    }

    setError('')
    setMessage('')

    try {
      const result = await api.POST('/api/v1/subjects/{subject_id}/access', {
        params: { path: { subject_id: subjectId } },
        body: { email: email.trim(), role },
      })

      if (result.error) {
        throw new Error(
          `Could not grant access (HTTP ${result.response.status}).`,
        )
      }

      setEmail('')
      setMessage('Access granted successfully.')
      await loadSharing()
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not grant access.',
      )
    }
  }

  async function revokeAccess(grantId: string) {
    if (!canManageSharing) {
      setError('You do not have permission to manage subject sharing.')
      return
    }

    if (!window.confirm("Revoke this person's access to the subject?")) {
      return
    }

    setError('')
    setMessage('')

    try {
      const result = await api.DELETE(
        '/api/v1/subjects/{subject_id}/access/{grant_id}',
        {
          params: {
            path: { subject_id: subjectId, grant_id: grantId },
          },
        },
      )

      if (result.error) {
        throw new Error(
          `Could not revoke access (HTTP ${result.response.status}).`,
        )
      }

      setMessage('Access revoked.')
      await loadSharing()
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not revoke access.',
      )
    }
  }

  function toggleValue(
    value: string,
    selected: string[],
    setSelected: (next: string[]) => void,
  ) {
    if (!canEditSettings) {
      return
    }

    setSelected(
      selected.includes(value)
        ? selected.filter((item) => item !== value)
        : [...selected, value],
    )
  }

  if (loading && !subject) {
    return (
      <main style={pageStyle}>
        <p>Loading subject...</p>
      </main>
    )
  }

  if (!subject) {
    return (
      <main style={pageStyle}>
        <button type="button" onClick={onBack}>
          ← Back to dashboard
        </button>
        <p role="alert" style={{ color: '#b91c1c' }}>
          {error || 'Subject not found.'}
        </p>
      </main>
    )
  }

  const visibleTabs: Tab[] = canManageSharing
    ? ['Questions', 'Exams', 'Settings', 'Sharing']
    : ['Questions', 'Exams', 'Settings']

  return (
    <main style={pageStyle}>
      <button type="button" onClick={onBack}>
        ← Back to dashboard
      </button>

      <h1>{subject.name}</h1>
      <p>
        <strong>Subject code:</strong> {subject.code}
      </p>
      <p>
        <strong>Your role:</strong> {subject.my_role ?? 'Not specified'}
      </p>

      <nav
        aria-label="Subject sections"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '8px',
          margin: '20px 0',
        }}
      >
        {visibleTabs.map((item) => (
          <button
            type="button"
            key={item}
            onClick={() => {
              setTab(item)
              setQuestionFromExam(null)
              setMessage('')
              setError('')
            }}
            aria-pressed={tab === item}
            style={{
              padding: '10px 16px',
              fontWeight: tab === item ? 'bold' : 'normal',
              background: tab === item ? '#dbeafe' : '#ffffff',
              border: '1px solid #9ca3af',
              borderRadius: '8px',
            }}
          >
            {item}
          </button>
        ))}
      </nav>

      {error && (
        <p role="alert" style={{ color: '#b91c1c' }}>
          {error}
        </p>
      )}

      {message && (
        <p role="status" style={{ color: '#166534' }}>
          {message}
        </p>
      )}

     {tab === 'Questions' && (
  <>
    {questionFromExam && (
      <p
        role="status"
        style={{ background: '#fef3c7', color: '#92400e', padding: '8px 12px', borderRadius: '6px' }}
      >
        Opened from an exam. Changes to this question apply to every exam
        that uses it.{' '}
        <button
          type="button"
          onClick={() => {
            setTab('Exams')
            setQuestionFromExam(null)
          }}
        >
          ← Back to exams
        </button>
      </p>
    )}
    <QuestionWizard
      key={questionFromExam ?? 'questions'}
      subjectId={subjectId}
      openQuestionId={questionFromExam ?? undefined}
    />
  </>
)}

      {tab === 'Exams' && (
        <ExamsTab
          subjectId={subjectId}
          subjectLanguages={languages.filter((language) =>
            subject.language_keys.includes(language.key),
          )}
          canEdit={canEditSettings}
          onGoToTab={(target) => {
            setTab(target)
            setMessage('')
            setError('')
          }}
          subjectBatches={batches.filter((batch) =>
            subject.batch_ids.includes(batch.id),
          )}
          allBatches={batches}
          onLinkBatch={linkBatchToSubject}
          onEditQuestion={(questionId) => {
            setQuestionFromExam(questionId)
            setTab('Questions')
            setMessage('')
            setError('')
          }}
        />
      )}

      {tab === 'Settings' && (
        <section style={cardStyle}>
          <h2>Subject settings</h2>

          <h3>Allowed languages</h3>

          {languages.length === 0 && (
            <p>No languages were returned by the API.</p>
          )}

          {languages.map((language) => (
            <label
              key={language.key}
              style={{ display: 'block', margin: '10px 0' }}
            >
              <input
                type="checkbox"
                disabled={!canEditSettings || !language.is_enabled}
                checked={selectedLanguages.includes(language.key)}
                onChange={() =>
                  toggleValue(
                    language.key,
                    selectedLanguages,
                    setSelectedLanguages,
                  )
                }
              />{' '}
              {language.name} ({language.key})
              {!language.is_enabled && ' — disabled'}
            </label>
          ))}

          <h3 style={{ marginTop: '28px' }}>Assigned batches</h3>

          {batches.length === 0 && (
            <p>No batches were returned by the API.</p>
          )}

          {batches.map((batch) => (
            <label
              key={batch.id}
              style={{ display: 'block', margin: '10px 0' }}
            >
              <input
                type="checkbox"
                disabled={!canEditSettings}
                checked={selectedBatches.includes(batch.id)}
                onChange={() =>
                  toggleValue(
                    batch.id,
                    selectedBatches,
                    setSelectedBatches,
                  )
                }
              />{' '}
              {batch.name} ({batch.code})
            </label>
          ))}

          {canEditSettings ? (
            <button
              type="button"
              onClick={() => void saveSettings()}
              disabled={saving}
            >
              {saving ? 'Saving...' : 'Save settings'}
            </button>
          ) : (
            <p>You have read-only access to these settings.</p>
          )}
        </section>
      )}

      {tab === 'Sharing' && canManageSharing && (
        <section style={cardStyle}>
          <h2>Share subject access</h2>
          <p>Find a professor by name or email, then grant a role.</p>

          <form
            onSubmit={(event) => {
              event.preventDefault()
              void searchProfessors()
            }}
          >
            <label htmlFor="professor-search">Search professors</label>
            <input
              id="professor-search"
              style={inputStyle}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Name or email"
            />
            <button type="submit">Search</button>
          </form>

          {professors.map((professor) => (
            <div
              key={professor.id}
              style={{
                borderTop: '1px solid #e5e7eb',
                padding: '10px 0',
              }}
            >
              <strong>{professor.full_name}</strong>
              <p>{professor.email}</p>
              <button
                type="button"
                onClick={() => setEmail(professor.email)}
              >
                Use this email
              </button>
            </div>
          ))}

          <form onSubmit={grantAccess} style={{ marginTop: '24px' }}>
            <label htmlFor="grant-email">Professor email</label>
            <input
              id="grant-email"
              style={inputStyle}
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="professor@university.edu"
            />

            <label htmlFor="grant-role">Access role</label>
            <select
              id="grant-role"
              style={inputStyle}
              value={role}
              onChange={(event) =>
                setRole(event.target.value as Grant['role'])
              }
            >
              <option value="viewer">Viewer — view only</option>
              <option value="grader">Grader — grading access</option>
              <option value="editor">Editor — edit access</option>
            </select>

            <button type="submit">Grant access</button>
          </form>

          <h3 style={{ marginTop: '28px' }}>Current access</h3>

          {grants.length === 0 && (
            <p>No access grants returned.</p>
          )}

          {grants.map((grant) => (
            <div
              key={grant.id}
              style={{
                borderTop: '1px solid #e5e7eb',
                padding: '12px 0',
              }}
            >
              <strong>{grant.user.full_name}</strong>
              <p>{grant.user.email}</p>
              <p>Role: {grant.role}</p>

              {grant.exam_id && (
                <p>Limited to exam: {grant.exam_id}</p>
              )}

              <button
                type="button"
                onClick={() => void revokeAccess(grant.id)}
              >
                Revoke access
              </button>
            </div>
          ))}
        </section>
      )}
    </main>
  )
}

export default SubjectPage
