import CreateSubjectForm from './CreateSubjectForm'
import SubjectPage from './SubjectPage'
import MonitorPage from './MonitorPage'
import PasswordInput from './components/PasswordInput'
import SessionPage from './SessionPage'
import ResultsPage from './ResultsPage'
import AnswersPage from './AnswersPage'
import AdminPage from './admin/AdminPage'
import { isAdminSection } from './admin/adminShared'
import LoginPage from './LoginPage'
import { useAuth, type Session } from './hooks/useAuth'
import { useEffect, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { API_URL, api } from './api/client'
import './App.css'


type BatchProgress = {
  batch_id: string
  code: string
  name: string
  enrolled: number
  started: number
  submitted: number
  locked: number
  flagged: number
  grading_pending: number
}

type DashboardExam = {
  id: string
  title: string
  status: string
  languages: string[] | null
  languages_used: Record<string, number>
  opened_at: string | null
  closed_at: string | null
  results_released: boolean
  batches: BatchProgress[]
}

type DashboardSubject = {
  id: string
  code: string
  name: string
  my_role: string
  languages: string[]
  exams: DashboardExam[]
}

type AdminSubjectSummary = {
  id: string
  code: string
  name: string
  archived: boolean
  question_count: number
  exam_count: number
}

type ApiError = {
  detail?: string | { msg?: string }[]
}

async function readResponse(response: Response): Promise<unknown> {
  const text = await response.text()

  if (!text) return null

  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function getErrorMessage(data: unknown, fallback: string): string {
  if (typeof data === 'object' && data !== null && 'detail' in data) {
    const detail = (data as ApiError).detail

    if (typeof detail === 'string') return detail

    if (Array.isArray(detail)) {
      return detail.map((item) => item.msg ?? 'Invalid input').join(', ')
    }
  }

  return fallback
}

function App() {

  const location = useLocation()
  const navigate = useNavigate()

  const {
    session,
    status,
    setStatus,
    loading,
    checkingSession,
    login,
    logout,
    updateSession,
  } = useAuth()

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [changingPassword, setChangingPassword] = useState(false)

  const [dashboardSubjects, setDashboardSubjects] =
    useState<DashboardSubject[]>([])
  const [adminSubjects, setAdminSubjects] =
    useState<AdminSubjectSummary[]>([])
  const [dashboardLoading, setDashboardLoading] = useState(false)
  const [dashboardError, setDashboardError] = useState('')

  const [showCreateSubject, setShowCreateSubject] = useState(false)

  async function loadDashboard() {
    if (!session) return

    setDashboardLoading(true)
    setDashboardError('')

    try {
      if (session.user.role === 'admin') {
        const response = await fetch(
          `${API_URL}/api/v1/admin/subject-summary`,
          {
            headers: {
              Authorization: `Bearer ${session.token}`,
            },
          },
        )

        const data = await readResponse(response)

        if (!response.ok) {
          throw new Error(
            getErrorMessage(
              data,
              `Could not load admin summary (HTTP ${response.status}).`,
            ),
          )
        }

        setAdminSubjects(data as AdminSubjectSummary[])
        setDashboardSubjects([])
        return
      }

      if (session.user.role !== 'professor') {
        setDashboardSubjects([])
        setAdminSubjects([])
        setDashboardError('This portal is for authorized staff only.')
        return
      }

      const { data, error, response } =
        await api.GET('/api/v1/dashboard')

      if (error) {
        setDashboardError(
          `Could not load dashboard (HTTP ${response.status}).`,
        )
        return
      }

      setDashboardSubjects(data ?? [])
      setAdminSubjects([])
    } catch (error) {
      setDashboardError(
        error instanceof Error
          ? error.message
          : 'Could not connect to the server while loading the dashboard.',
      )
    } finally {
      setDashboardLoading(false)
    }
  }

  useEffect(() => {
    if (session) {
      void loadDashboard()
    } else {
      setDashboardSubjects([])
      setAdminSubjects([])
      setDashboardError('')
      setDashboardLoading(false)
      setShowCreateSubject(false)
    }
  }, [session])

  async function handleChangePassword(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault()
    setStatus('')

    if (!session) return

    if (newPassword !== confirmPassword) {
      setStatus('The new password and confirmation do not match.')
      return
    }

    if (newPassword.length < 1 || newPassword.length > 256) {
      setStatus(
        'The new password must contain between 1 and 256 characters.',
      )
      return
    }

    setChangingPassword(true)

    try {
      const response = await fetch(
        `${API_URL}/api/v1/auth/change-password`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${session.token}`,
          },
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
          }),
        },
      )

      const data = await readResponse(response)

      if (!response.ok) {
        throw new Error(
          getErrorMessage(
            data,
            `Password change failed (HTTP ${response.status})`,
          ),
        )
      }

      const updatedSession: Session = {
        ...session,
        user: {
          ...session.user,
          must_change_password: false,
        },
      }

      updateSession(updatedSession)

      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setStatus('Password changed successfully.')
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : 'An unexpected error occurred.',
      )
    } finally {
      setChangingPassword(false)
    }
  }

  async function handleLogout() {
    setCurrentPassword('')
    setNewPassword('')
    setConfirmPassword('')
    navigate('/')
    await logout()
  }

  function renderPasswordForm() {
    return (
      <section aria-labelledby="password-heading">
        <h2 id="password-heading">Change password</h2>
        <p>Change your account password using the form below.</p>

        <form onSubmit={handleChangePassword}>
          <label htmlFor="current-password">Current password</label>
          <PasswordInput
            id="current-password"
            autoComplete="current-password"
            required
            maxLength={256}
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            wrapperStyle={{ margin: '8px 0 18px' }}
          />

          <label htmlFor="new-password">New password</label>
          <PasswordInput
            id="new-password"
            autoComplete="new-password"
            required
            minLength={1}
            maxLength={256}
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            wrapperStyle={{ margin: '8px 0 18px' }}
          />

          <label htmlFor="confirm-password">
            Confirm new password
          </label>
          <PasswordInput
            id="confirm-password"
            autoComplete="new-password"
            required
            minLength={1}
            maxLength={256}
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
            wrapperStyle={{ margin: '8px 0 18px' }}
          />

          <button type="submit" disabled={changingPassword}>
            {changingPassword
              ? 'Changing password...'
              : 'Change password'}
          </button>
        </form>

        {session?.user.must_change_password && (
          <p role="alert">
            Your account requires a password change before continuing.
          </p>
        )}
      </section>
    )
  }

  function renderSignOut() {
    return (
      <>
        <hr />
        <button
          type="button"
          onClick={() => void handleLogout()}
        >
          Sign out
        </button>

        {status && (
          <p role="status" style={{ overflowWrap: 'anywhere' }}>
            {status}
          </p>
        )}
      </>
    )
  }

  const subjectMatch = location.pathname.match(/^\/subjects\/([^/]+)$/)
  const monitorMatch = location.pathname.match(/^\/exams\/([^/]+)\/monitor$/)
  const sessionMatch = location.pathname.match(/^\/exams\/([^/]+)\/sessions\/([^/]+)$/)
  const resultsMatch = location.pathname.match(/^\/exams\/([^/]+)\/results$/)
  const answersMatch = location.pathname.match(/^\/exams\/([^/]+)\/answers$/)
  const examPath = (examId: string, rest: string) => `/exams/${encodeURIComponent(examId)}/${rest}`

  // Live monitor (Step 10), professors only.
  if (session?.user.role === 'professor' && monitorMatch) {
    const examId = decodeURIComponent(monitorMatch[1])
    return (
      <MonitorPage
        examId={examId}
        onBack={(subjectId) =>
          navigate(
            subjectId ? `/subjects/${encodeURIComponent(subjectId)}` : '/',
            subjectId ? { state: { tab: 'Exams' } } : undefined,
          )
        }
        onOpenSession={(sid) => navigate(examPath(examId, `sessions/${encodeURIComponent(sid)}`))}
        onOpenResults={() => navigate(examPath(examId, 'results'))}
      />
    )
  }

  // Session detail, results and answer-by-answer checking (Step 11), professors only.
  if (session?.user.role === 'professor' && sessionMatch) {
    const examId = decodeURIComponent(sessionMatch[1])
    return (
      <SessionPage
        key={sessionMatch[2]}
        examId={examId}
        sessionId={decodeURIComponent(sessionMatch[2])}
        onBackToMonitor={() => navigate(examPath(examId, 'monitor'))}
        onOpenResults={() => navigate(examPath(examId, 'results'))}
      />
    )
  }

  if (session?.user.role === 'professor' && resultsMatch) {
    const examId = decodeURIComponent(resultsMatch[1])
    return (
      <ResultsPage
        examId={examId}
        onBackToMonitor={() => navigate(examPath(examId, 'monitor'))}
        onOpenSession={(sid) => navigate(examPath(examId, `sessions/${encodeURIComponent(sid)}`))}
        onOpenAnswers={() => navigate(examPath(examId, 'answers'))}
      />
    )
  }

  if (session?.user.role === 'professor' && answersMatch) {
    const examId = decodeURIComponent(answersMatch[1])
    return (
      <AnswersPage
        examId={examId}
        onBackToResults={() => navigate(examPath(examId, 'results'))}
        onOpenSession={(sid) => navigate(examPath(examId, `sessions/${encodeURIComponent(sid)}`))}
      />
    )
  }

  // Only professors can open subject detail pages in this portal.
  if (session?.user.role === 'professor' && subjectMatch) {
    const backTab = (location.state as { tab?: 'Exams' } | null)?.tab
    return (
      <SubjectPage
        subjectId={decodeURIComponent(subjectMatch[1])}
        initialTab={backTab}
        onBack={() => navigate('/')}
      />
    )
  }

  // Admin dashboard: subject metadata and counts only.
  if (session?.user.role === 'admin') {
    // Step 12: /admin/<section>, with the subject overview as the first section.
    const adminSegment = location.pathname.match(/^\/admin\/([^/]+)$/)?.[1]
    const adminSection = isAdminSection(adminSegment) ? adminSegment : 'subjects'
    return (
      <AdminPage
        section={adminSection}
        onSection={(next) => navigate(`/admin/${next}`)}
        userName={`${session.user.full_name} (${session.user.email})`}
        currentUserId={session.user.id}
        onSignOut={() => void handleLogout()}
        account={
          <section>
            <h2 style={{ marginTop: 0 }}>My account</h2>
            {renderPasswordForm()}
            {renderSignOut()}
          </section>
        }
        subjects={
        <section aria-labelledby="admin-subjects-heading">
          <h2 id="admin-subjects-heading">Subject Overview</h2>
          <p>
            This dashboard displays subject information and counts only.
          </p>

          <button
            type="button"
            onClick={() => void loadDashboard()}
            disabled={dashboardLoading}
          >
            {dashboardLoading ? 'Loading...' : 'Refresh subjects'}
          </button>

          {dashboardLoading && (
            <p role="status">Loading subject summaries...</p>
          )}

          {dashboardError && (
            <p role="alert" style={{ color: '#b91c1c' }}>
              {dashboardError}
            </p>
          )}

          {!dashboardLoading &&
            !dashboardError &&
            adminSubjects.length === 0 && (
              <p>No subjects found.</p>
            )}

          <div
            style={{
              display: 'grid',
              gridTemplateColumns:
                'repeat(auto-fit, minmax(250px, 1fr))',
              gap: '16px',
              marginTop: '20px',
            }}
          >
            {adminSubjects.map((subject) => (
              <article
                key={subject.id}
                style={{
                  border: '1px solid #d1d5db',
                  borderRadius: '12px',
                  padding: '18px',
                  background: '#ffffff',
                  color: '#111827',
                }}
              >
                <h3>{subject.name}</h3>

                <p>
                  <strong>Code:</strong> {subject.code}
                </p>

                <p>
                  <strong>Status:</strong>{' '}
                  {subject.archived ? 'Archived' : 'Active'}
                </p>

                <p>
                  <strong>Questions:</strong>{' '}
                  {subject.question_count}
                </p>

                <p>
                  <strong>Exams:</strong> {subject.exam_count}
                </p>
              </article>
            ))}
          </div>
        </section>
        }
      />
    )
  }

  // Professor dashboard.
  if (session?.user.role === 'professor') {
    return (
      <main
        style={{
          maxWidth: '1100px',
          margin: '40px auto',
          padding: '28px',
          fontFamily: 'Arial, sans-serif',
        }}
      >
        <h1>UAI-Aegis</h1>
        <h2>Professor Portal</h2>
        

        <p>Welcome, {session.user.full_name}!</p>
        <p>Email: {session.user.email}</p>
        <p>Role: {session.user.role}</p>
        <p>
          Session expires:{' '}
          {new Date(session.expiresAt).toLocaleString()}
        </p>

        <hr />

        <section aria-labelledby="subjects-heading">
          <h2 id="subjects-heading">My Subjects</h2>

          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: '10px',
              alignItems: 'center',
            }}
          >
            <button
              type="button"
              onClick={() => void loadDashboard()}
              disabled={dashboardLoading}
            >
              {dashboardLoading ? 'Loading...' : 'Refresh dashboard'}
            </button>

            <button
              type="button"
              onClick={() => {
                setDashboardError('')
                setShowCreateSubject(true)
              }}
              disabled={showCreateSubject}
            >
              + Create Subject
            </button>
          </div>

          {showCreateSubject && (
            <div
              style={{
                marginTop: '20px',
                marginBottom: '20px',
                padding: '20px',
                border: '1px solid #d1d5db',
                borderRadius: '12px',
                background: '#ffffff',
                color: '#111827',
              }}
            >
              <CreateSubjectForm
                onCancel={() => setShowCreateSubject(false)}
                onCreated={() => {
                  setShowCreateSubject(false)
                  void loadDashboard()
                }}
              />
            </div>
          )}

          {dashboardLoading && (
            <p role="status">
              Loading your subjects and exam progress...
            </p>
          )}

          {dashboardError && (
            <p role="alert" style={{ color: '#b91c1c' }}>
              {dashboardError}
            </p>
          )}

          {!dashboardLoading &&
            !dashboardError &&
            dashboardSubjects.length === 0 &&
            !showCreateSubject && (
              <p>No subjects yet — create one.</p>
            )}

          <div
            style={{
              display: 'grid',
              gridTemplateColumns:
                'repeat(auto-fit, minmax(280px, 1fr))',
              gap: '16px',
              marginTop: '20px',
            }}
          >
            {dashboardSubjects.map((subject) => (
              <article
                key={subject.id}
                style={{
                  border: '1px solid #d1d5db',
                  borderRadius: '12px',
                  padding: '18px',
                  background: '#ffffff',
                  color: '#111827',
                }}
              >
                <h3>{subject.name}</h3>

                <button
                  type="button"
                  onClick={() =>
                    navigate(
                      `/subjects/${encodeURIComponent(subject.id)}`,
                    )
                  }
                  style={{
                    marginBottom: '12px',
                    padding: '8px 14px',
                    cursor: 'pointer',
                  }}
                >
                  Open subject
                </button>

                <p>
                  <strong>Code:</strong> {subject.code}
                </p>

                <p>
                  <strong>Your role:</strong> {subject.my_role}
                </p>

                <p>
                  <strong>Allowed languages:</strong>{' '}
                  {subject.languages.length
                    ? subject.languages.join(', ')
                    : 'None specified'}
                </p>

                <p>
                  <strong>Exams:</strong> {subject.exams.length}
                </p>

                {subject.exams.map((exam) => (
                  <div
                    key={exam.id}
                    style={{
                      borderTop: '1px solid #e5e7eb',
                      marginTop: '14px',
                      paddingTop: '12px',
                    }}
                  >
                    <h4>{exam.title}</h4>

                    <p>
                      <strong>Status:</strong> {exam.status}
                    </p>

                    <p>
                      <strong>Exam languages:</strong>{' '}
                      {exam.languages?.length
                        ? exam.languages.join(', ')
                        : 'None specified'}
                    </p>

                    <p>
                      <strong>Languages used:</strong>{' '}
                      {Object.keys(exam.languages_used ?? {}).length
                        ? Object.entries(exam.languages_used)
                            .map(
                              ([language, count]) =>
                                `${language} (${count})`,
                            )
                            .join(', ')
                        : 'No responses yet'}
                    </p>

                    {exam.batches.length === 0 ? (
                      <p>No batch progress available.</p>
                    ) : (
                      exam.batches.map((batch) => (
                        <div
                          key={batch.batch_id}
                          style={{
                            background: '#f9fafb',
                            borderRadius: '8px',
                            padding: '12px',
                            marginTop: '10px',
                          }}
                        >
                          <h5>
                            {batch.name} ({batch.code})
                          </h5>

                          <p>Enrolled: {batch.enrolled}</p>
                          <p>Started: {batch.started}</p>
                          <p>Submitted: {batch.submitted}</p>

                          <p
                            style={{
                              color:
                                batch.locked > 0 ? '#b45309' : 'inherit',
                              fontWeight:
                                batch.locked > 0 ? 'bold' : 'normal',
                            }}
                          >
                            Locked: {batch.locked}
                          </p>

                          <p
                            style={{
                              color:
                                batch.flagged > 0 ? '#b91c1c' : 'inherit',
                              fontWeight:
                                batch.flagged > 0 ? 'bold' : 'normal',
                            }}
                          >
                            Flagged: {batch.flagged}
                          </p>

                          <p>Grading pending: {batch.grading_pending}</p>
                        </div>
                      ))
                    )}
                  </div>
                ))}
              </article>
            ))}
          </div>
        </section>

        <hr />

        {renderPasswordForm()}
        {renderSignOut()}
      </main>
    )
  }

  // Wait for session restoration before showing the login page.
  if (checkingSession && !session) {
    return (
      <main>
        <p role="status">Checking your session...</p>
      </main>
    )
  }

  // Login page.
  return (
    <LoginPage
      onLogin={login}
      loading={loading}
      status={status}
    />
  )
}

export default App
