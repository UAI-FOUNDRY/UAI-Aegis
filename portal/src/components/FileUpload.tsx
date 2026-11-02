
import { useId, useState } from 'react'

type FileUploadProps = {
  label: string
  accept?: string
  maxSizeMB?: number
  onFileSelect: (file: File | null) => void
  error?: string
  disabled?: boolean
}

export default function FileUpload({
  label,
  accept = '.csv,.json,.xlsx',
  maxSizeMB = 10,
  onFileSelect,
  error,
  disabled = false,
}: FileUploadProps) {
  const inputId = useId()
  const errorId = `${inputId}-error`
  const [localError, setLocalError] = useState('')
  const [fileName, setFileName] = useState('')

  function handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]

    setLocalError('')
    setFileName('')

    if (!file) {
      onFileSelect(null)
      return
    }

    if (file.size > maxSizeMB * 1024 * 1024) {
      const message = `File must be ${maxSizeMB} MB or smaller.`
      setLocalError(message)
      onFileSelect(null)
      event.target.value = ''
      return
    }

    setFileName(file.name)
    onFileSelect(file)
  }

  const message = error || localError

  return (
    <div className="ui-file-upload">
      <label className="ui-label" htmlFor={inputId}>
        {label}
      </label>

      <input
        id={inputId}
        type="file"
        accept={accept}
        disabled={disabled}
        onChange={handleChange}
        aria-invalid={Boolean(message) || undefined}
        aria-describedby={message ? errorId : undefined}
      />

      <p className="ui-file-upload__hint">
        Accepted files: {accept}. Maximum size: {maxSizeMB} MB.
      </p>

      {fileName && (
        <p className="ui-file-upload__filename">
          Selected: {fileName}
        </p>
      )}

      {message && (
        <p className="ui-error" id={errorId} role="alert">
          {message}
        </p>
      )}
    </div>
  )
}
