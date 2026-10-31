import { useEffect, type ReactNode } from 'react'

type Props = {
  title: string
  children: ReactNode
  confirmLabel: string
  busy?: boolean
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/** Modal confirmation. Esc or Cancel closes it (unless busy). */
export default function ConfirmDialog({ title, children, confirmLabel, busy = false, danger = false, onConfirm, onCancel }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, busy])

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(0, 0, 0, 0.55)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px',
      }}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
        style={{
          background: '#ffffff',
          color: '#111827',
          borderRadius: '12px',
          padding: '22px',
          maxWidth: '520px',
          width: '100%',
          textAlign: 'left',
          boxShadow: '0 20px 40px rgba(0, 0, 0, 0.3)',
        }}
      >
        <h3 style={{ marginTop: 0 }}>{title}</h3>
        {children}
        <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '16px' }}>
          <button type="button" onClick={onCancel} disabled={busy} autoFocus>
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            style={
              danger
                ? { color: '#fff', background: '#b91c1c', border: '1px solid #991b1b', borderRadius: '6px', padding: '6px 14px', fontWeight: 'bold' }
                : { fontWeight: 'bold' }
            }
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
