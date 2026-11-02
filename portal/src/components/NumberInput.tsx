import { useState, type InputHTMLAttributes } from 'react'

type NumberInputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'type' | 'value' | 'onChange' | 'min' | 'max'
> & {
  value: number | string
  onChange: (value: number) => void
  min?: number
  max?: number
}

/** Remove leading zeros ("01" -> "1", "005" -> "5") but keep "0" and "0.5". */
function stripLeadingZeros(raw: string): string {
  return raw.replace(/^(-?)0+(?=\d)/, '$1')
}

function clamp(value: number, min?: number, max?: number): number {
  if (min !== undefined && value < min) return min
  if (max !== undefined && value > max) return max
  return value
}

/**
 * A number field that shows exactly what the user typed, minus leading zeros.
 * The field may be empty while editing; on blur an empty or invalid entry goes
 * back to the last valid value, and out-of-range values are clamped.
 */
export default function NumberInput({
  value,
  onChange,
  min,
  max,
  onBlur,
  ...rest
}: NumberInputProps) {
  const [text, setText] = useState(String(value))
  const [lastValue, setLastValue] = useState(Number(value))

  // The value changed from outside (e.g. another question was opened).
  if (Number(value) !== lastValue) {
    setLastValue(Number(value))
    setText(String(value))
  }

  function commit(next: number) {
    setLastValue(next)
    onChange(next)
  }

  return (
    <input
      {...rest}
      type="number"
      min={min}
      max={max}
      // A string value makes React rewrite "01" in the box; a number would not.
      value={text}
      onChange={(event) => {
        const raw = event.target.value
        if (raw === '') {
          setText('')
          return
        }
        const cleaned = stripLeadingZeros(raw)
        setText(cleaned)
        const parsed = Number(cleaned)
        if (Number.isFinite(parsed)) commit(parsed)
      }}
      onBlur={(event) => {
        const parsed = Number(text)
        if (text === '' || !Number.isFinite(parsed)) {
          setText(String(value))
        } else {
          const limited = clamp(parsed, min, max)
          if (limited !== parsed) commit(limited)
          setText(String(limited))
        }
        onBlur?.(event)
      }}
    />
  )
}
