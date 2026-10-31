
import createClient from 'openapi-fetch'
import type { paths } from '@shared/api/schema'

const SESSION_KEY = 'aegis_session'

export const API_URL =
  import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export const api = createClient<paths>({
  baseUrl: API_URL,
})

api.use({
  onRequest({ request }) {
    const savedSession = localStorage.getItem(SESSION_KEY)

    if (savedSession) {
      try {
        const session = JSON.parse(savedSession) as {
          token?: string
          expiresAt?: string
        }

        if (
          session.token &&
          session.expiresAt &&
          Date.parse(session.expiresAt) > Date.now()
        ) {
          request.headers.set(
            'Authorization',
            `Bearer ${session.token}`,
          )
        }
      } catch {
        // Ignore an invalid saved session.
      }
    }

    return request
  },
})
