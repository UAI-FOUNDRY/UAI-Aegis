
import { useState, type FormEvent } from 'react'
import PasswordInput from './components/PasswordInput'

type LoginPageProps = {
  onLogin: (email: string, password: string) => Promise<boolean>
  loading: boolean
  status: string
}

function LoginPage({ onLogin, loading, status }: LoginPageProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const success = await onLogin(email, password)

    if (success) {
      setPassword('')
    }
  }

  return (
    <main
      style={{
        maxWidth: '420px',
        margin: '60px auto',
        padding: '28px',
        fontFamily: 'Arial, sans-serif',
      }}
    >
      <h1>UAI-Aegis</h1>
      <h2>Professor Portal</h2>
      <p>Sign in with your university account.</p>

      <form onSubmit={handleSubmit}>
        <label htmlFor="email">Email address</label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          style={{
            display: 'block',
            width: '100%',
            boxSizing: 'border-box',
            padding: '10px',
            margin: '8px 0 18px',
          }}
        />

        <label htmlFor="password">Password</label>
        <PasswordInput
          id="password"
          autoComplete="current-password"
          required
          minLength={1}
          maxLength={256}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          wrapperStyle={{ margin: '8px 0 18px' }}
        />

        <button
          type="submit"
          disabled={loading}
          style={{ padding: '10px 18px', cursor: 'pointer' }}
        >
          {loading ? 'Signing in...' : 'Sign in'}
        </button>
      </form>

      {status && (
        <p role="status" style={{ overflowWrap: 'anywhere' }}>
          {status}
        </p>
      )}
    </main>
  )
}

export default LoginPage
