import { useState, type CSSProperties, type InputHTMLAttributes } from 'react'

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  /** Styles for the outer box (margins, width). The input fills it. */
  wrapperStyle?: CSSProperties
}

/**
 * A password field with a Show / Hide button, so people can check what they typed.
 * The password is only shown while "Show" is on; it goes back to dots on Hide.
 */
export default function PasswordInput({ wrapperStyle, style, ...inputProps }: Props) {
  const [visible, setVisible] = useState(false)

  return (
    <div style={{ position: 'relative', display: 'block', ...wrapperStyle }}>
      <input
        {...inputProps}
        type={visible ? 'text' : 'password'}
        // Turn off spell check and autocorrect while the password is visible.
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        style={{
          display: 'block',
          width: '100%',
          boxSizing: 'border-box',
          padding: '10px',
          paddingRight: '72px', // room for the button
          margin: 0,
          ...style,
        }}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-pressed={visible}
        aria-label={visible ? 'Hide password' : 'Show password'}
        title={visible ? 'Hide password' : 'Show password'}
        style={{
          position: 'absolute',
          top: '50%',
          right: '6px',
          transform: 'translateY(-50%)',
          padding: '4px 8px',
          fontSize: '0.85em',
          cursor: 'pointer',
        }}
      >
        {visible ? '🙈 Hide' : '👁 Show'}
      </button>
    </div>
  )
}
