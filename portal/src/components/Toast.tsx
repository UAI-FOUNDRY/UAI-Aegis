

import { useEffect } from 'react'

export type ToastType = 'success' | 'error' | 'info'

export type ToastMessage = {
  id: string | number
  message: string
  type: ToastType
}

type ToastProps = {
  toast: ToastMessage
  onDismiss: (id: ToastMessage['id']) => void
  duration?: number
}

export default function Toast({
  toast,
  onDismiss,
  duration = 4000,
}: ToastProps) {
  useEffect(() => {
    const timer = window.setTimeout(() => {
      onDismiss(toast.id)
    }, duration)

    return () => window.clearTimeout(timer)
  }, [toast.id, duration, onDismiss])

  return (
    <div
      className={`ui-toast ui-toast--${toast.type}`}
      role={toast.type === 'error' ? 'alert' : 'status'}
    >
      <span>{toast.message}</span>

      <button
        type="button"
        className="ui-toast__close"
        aria-label="Dismiss notification"
        onClick={() => onDismiss(toast.id)}
      >
        Close
      </button>
    </div>
  )
}
