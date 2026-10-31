import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

export type BadgeStatus =
  | 'active'
  | 'locked'
  | 'submitted'
  | 'flagged'

export function Badge({
  status,
  children,
}: {
  status: BadgeStatus
  children?: ReactNode
}) {
  return (
    <span className={`ui-badge ui-badge--${status}`}>
      {children ?? status}
    </span>
  )
}

export type TableColumn<T> = {
  key: string
  header: string
  render: (row: T) => ReactNode
  sortable?: boolean
  sortValue?: (row: T) => string | number
}

export function Table<T extends { id: string | number }>({
  columns,
  rows,
  emptyMessage = 'No records found.',
}: {
  columns: TableColumn<T>[]
  rows: T[]
  emptyMessage?: string
}) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [descending, setDescending] = useState(false)

  const sortedRows = [...rows].sort((a, b) => {
    if (!sortKey) return 0

    const column = columns.find((item) => item.key === sortKey)
    if (!column?.sortValue) return 0

    const left = column.sortValue(a)
    const right = column.sortValue(b)

    const result =
      typeof left === 'number' && typeof right === 'number'
        ? left - right
        : String(left).localeCompare(String(right))

    return descending ? -result : result
  })

  return (
    <div className="ui-table-wrap">
      <table className="ui-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col">
                {column.sortable && column.sortValue ? (
                  <button
                    type="button"
                    onClick={() => {
                      if (sortKey === column.key) {
                        setDescending((value) => !value)
                      } else {
                        setSortKey(column.key)
                        setDescending(false)
                      }
                    }}
                    aria-label={`Sort by ${column.header}`}
                  >
                    {column.header}
                    {sortKey === column.key
                      ? descending ? ' ↓' : ' ↑'
                      : ''}
                  </button>
                ) : column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row) => (
            <tr key={row.id}>
              {columns.map((column) => (
                <td key={column.key}>{column.render(row)}</td>
              ))}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={Math.max(columns.length, 1)}>
                {emptyMessage}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export function Dialog({
  open,
  title,
  children,
  onClose,
}: {
  open: boolean
  title: string
  children: ReactNode
  onClose: () => void
}) {
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return

    const previousFocus = document.activeElement as HTMLElement | null

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }

    document.addEventListener('keydown', handleKeyDown)
    dialogRef.current?.focus()

    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocus?.focus()
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="ui-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={dialogRef}
        className="ui-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            marginBottom: 16,
          }}
        >
          <h2>{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close dialog">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function ConfirmDialog({
  open,
  title = 'Are you sure?',
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  loading = false,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title?: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  loading?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <Dialog open={open} title={title} onClose={onCancel}>
      <p style={{ marginBottom: 20 }}>{message}</p>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <button
          type="button"
          className="ui-button ui-button--secondary"
          onClick={onCancel}
          disabled={loading}
        >
          {cancelLabel}
        </button>
        <button
          type="button"
          className="ui-button ui-button--danger"
          onClick={onConfirm}
          disabled={loading}
        >
          {loading ? 'Please wait…' : confirmLabel}
        </button>
      </div>
    </Dialog>
  )
}
