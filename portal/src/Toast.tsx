
import { useEffect } from 'react'

export type ToastType = 'error' | 'success' | 'info'

type ToastProps = {
  type: ToastType
  message: string
  onClose: () => void
}

export default function Toast({ type, message, onClose }: ToastProps) {
  useEffect(() => {
    if (!message) return

    const timer = window.setTimeout(onClose, 5000)
    return () => window.clearTimeout(timer)
  }, [message, onClose])

  if (!message) return null

  const title = {
    error: 'Error',
    success: 'Success',
    info: 'Information',
  }[type]

  return (
    <div
      className={`app-toast app-toast--${type}`}
      role={type === 'error' ? 'alert' : 'status'}
      aria-live={type === 'error' ? 'assertive' : 'polite'}
    >
      <div className="app-toast__content">
        <strong>{title}</strong>
        <p>{message}</p>
      </div>

      <button
        type="button"
        className="app-toast__close"
        onClick={onClose}
        aria-label="Dismiss notification"
      >
        ×
      </button>
    </div>
  )
}
