
import { useCallback, useEffect, useState } from 'react'
import { API_URL } from '../api/client'

export type User = {
  id: string
  email: string
  full_name: string
  role: 'admin' | 'professor' | 'student'
  is_active: boolean
  must_change_password: boolean
  college_id: string | null
  department_id: string | null
  created_at: string
}

export type Session = {
  token: string
  expiresAt: string
  user: User
}

type LoginResponse = {
  token: string
  expires_at: string
  user: User
}

type ApiError = {
  detail?: string | { msg?: string }[]
}

const SESSION_KEY = 'aegis_session'
const LEGACY_TOKEN_KEY = 'aegis_token'

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

function clearSavedSession() {
  localStorage.removeItem(SESSION_KEY)
  localStorage.removeItem(LEGACY_TOKEN_KEY)
}

export function useAuth() {
  const [session, setSession] = useState<Session | null>(null)
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [checkingSession, setCheckingSession] = useState(true)

  useEffect(() => {
    let cancelled = false

    async function restoreSession() {
      try {
        const saved = localStorage.getItem(SESSION_KEY)

        if (!saved) return

        let restored: Session

        try {
          restored = JSON.parse(saved) as Session
        } catch {
          clearSavedSession()
          return
        }

        if (
          !restored.token ||
          !restored.expiresAt ||
          Date.parse(restored.expiresAt) <= Date.now()
        ) {
          clearSavedSession()
          if (!cancelled) {
            setStatus('Your session expired. Please sign in again.')
          }
          return
        }

        const response = await fetch(`${API_URL}/api/v1/auth/me`, {
          headers: {
            Authorization: `Bearer ${restored.token}`,
          },
        })

        const data = await readResponse(response)

        if (!response.ok) {
          clearSavedSession()
          if (!cancelled) {
            setStatus('Your session is no longer valid. Please sign in again.')
          }
          return
        }

        const user = data as User

      if (!user.is_active || !['admin', 'professor'].includes(user.role)) {
  clearSavedSession()

  if (!cancelled) {
    setStatus(
      !user.is_active
        ? 'This account is inactive. Contact your administrator.'
        : user.role === 'student'
          ? 'Students use the UAI Aegis desktop app'
          : 'This portal is for authorized staff only.',
    )
  }
  return
}

        const updatedSession = { ...restored, user }

        localStorage.setItem(SESSION_KEY, JSON.stringify(updatedSession))

        if (!cancelled) setSession(updatedSession)
      } catch {
        clearSavedSession()

        if (!cancelled) {
          setStatus('Could not restore your session. Please sign in again.')
        }
      } finally {
        if (!cancelled) setCheckingSession(false)
      }
    }

    void restoreSession()

    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    setStatus('')
    setLoading(true)

    try {
      const response = await fetch(`${API_URL}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, client: 'portal' }),
      })

      const data = await readResponse(response)

      if (!response.ok) {
        throw new Error(
          getErrorMessage(data, `Login failed (HTTP ${response.status})`),
        )
      }

      const result = data as LoginResponse

      if (!result.user.is_active) {
        throw new Error('This account is inactive. Contact your administrator.')
      }

      if (!['admin', 'professor'].includes(result.user.role)) {
  clearSavedSession()
  setSession(null)

  if (result.user.role === 'student') {
    setStatus('Students use the UAI Aegis desktop app')
    return false
  }

  throw new Error('This portal is for authorized staff only.')
}

      if (Date.parse(result.expires_at) <= Date.now()) {
        throw new Error('The server returned an expired session. Please try again.')
      }

      const newSession: Session = {
        token: result.token,
        expiresAt: result.expires_at,
        user: result.user,
      }

      localStorage.setItem(SESSION_KEY, JSON.stringify(newSession))
      localStorage.removeItem(LEGACY_TOKEN_KEY)
      setSession(newSession)
      setStatus('')
      return true
    } catch (error) {
      const message = error instanceof Error ? error.message : ''

      setStatus(
        message.toLowerCase().includes('fetch') ||
          message.toLowerCase().includes('network')
          ? 'Unable to connect to the server. Please check that the backend is running and try again.'
          : message || 'An unexpected error occurred.',
      )

      return false
    } finally {
      setLoading(false)
    }
  }, [])

  const logout = useCallback(async () => {
    const token = session?.token

    clearSavedSession()
    setSession(null)
    setStatus('You have signed out.')

    if (token) {
      try {
        await fetch(`${API_URL}/api/v1/auth/logout`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        })
      } catch {
        // Local sign-out is already complete.
      }
    }
  }, [session])

  const updateSession = useCallback((updatedSession: Session) => {
    localStorage.setItem(SESSION_KEY, JSON.stringify(updatedSession))
    setSession(updatedSession)
  }, [])

  return {
    session,
    status,
    setStatus,
    loading,
    checkingSession,
    login,
    logout,
    updateSession,
    setSession,
  }
}
