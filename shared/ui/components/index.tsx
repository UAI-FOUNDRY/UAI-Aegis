import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  TextareaHTMLAttributes,
  SelectHTMLAttributes,
} from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'danger'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  loading?: boolean
}

export function Button({
  variant = 'primary',
  loading = false,
  disabled,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      type={props.type ?? 'button'}
      className={`ui-button ui-button--${variant} ${props.className ?? ''}`.trim()}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading ? 'Loading…' : children}
    </button>
  )
}

type FieldProps = {
  label: string
  error?: string
  id: string
}

type InputProps = InputHTMLAttributes<HTMLInputElement> & FieldProps

export function Input({ label, error, id, ...props }: InputProps) {
  const errorId = `${id}-error`

  return (
    <div>
      <label className="ui-label" htmlFor={id}>{label}</label>
      <input
        {...props}
        id={id}
        className={`ui-control ${props.className ?? ''}`.trim()}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={error ? errorId : props['aria-describedby']}
      />
      {error && <p className="ui-error" id={errorId}>{error}</p>}
    </div>
  )
}

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & FieldProps

export function Textarea({ label, error, id, ...props }: TextareaProps) {
  const errorId = `${id}-error`

  return (
    <div>
      <label className="ui-label" htmlFor={id}>{label}</label>
      <textarea
        {...props}
        id={id}
        className={`ui-control ${props.className ?? ''}`.trim()}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={error ? errorId : props['aria-describedby']}
      />
      {error && <p className="ui-error" id={errorId}>{error}</p>}
    </div>
  )
}

export type SelectOption = {
  label: string
  value: string
}

type SelectProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'> &
  FieldProps & {
    options: SelectOption[]
    placeholder?: string
  }

export function Select({
  label,
  error,
  id,
  options,
  placeholder,
  ...props
}: SelectProps) {
  const errorId = `${id}-error`

  return (
    <div>
      <label className="ui-label" htmlFor={id}>{label}</label>
      <select
        {...props}
        id={id}
        className={`ui-control ${props.className ?? ''}`.trim()}
        aria-invalid={Boolean(error) || undefined}
        aria-describedby={error ? errorId : props['aria-describedby']}
      >
        {placeholder && <option value="">{placeholder}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {error && <p className="ui-error" id={errorId}>{error}</p>}
    </div>
  )
}

type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  label: string
  error?: string
}

export function Checkbox({ label, error, id, ...props }: CheckboxProps) {
  const inputId = id ?? props.name ?? 'ui-checkbox'
  const errorId = `${inputId}-error`

  return (
    <div>
      <label
        htmlFor={inputId}
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}
      >
        <input
          {...props}
          id={inputId}
          type="checkbox"
          aria-invalid={Boolean(error) || undefined}
          aria-describedby={error ? errorId : props['aria-describedby']}
        />
        <span>{label}</span>
      </label>
      {error && <p className="ui-error" id={errorId}>{error}</p>}
    </div>
  )
}
