import { useMemo, useState } from 'react'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import xml from 'highlight.js/lib/languages/xml'
import 'highlight.js/styles/github-dark.css'

hljs.registerLanguage('c', c)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('java', java)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('xml', xml) // HTML

/** Map an Aegis language key (python3, python3-ml, cpp17, sql, ...) to a highlighter. */
function highlighterFor(languageKey: string | null | undefined): string | null {
  const key = (languageKey ?? '').toLowerCase()
  if (key.startsWith('python')) return 'python'
  if (key.startsWith('sql')) return 'sql'
  if (key === 'html' || key === 'xml') return 'xml'
  if (key.startsWith('cpp') || key.startsWith('c++')) return 'cpp'
  if (key === 'c' || key.startsWith('c1') || key.startsWith('c9')) return 'c'
  if (key.startsWith('java') && !key.startsWith('javascript')) return 'java'
  if (key.startsWith('javascript') || key.startsWith('node') || key === 'js') return 'javascript'
  return null
}

type Props = {
  code: string
  language?: string | null
  /** Shown above the code, e.g. "python3 · saved 10:42". */
  caption?: string
}

/**
 * Read-only code with syntax highlighting and a copy button.
 * Student code is untrusted: highlight.js escapes it, and the result is sanitised again.
 */
export default function CodeBlock({ code, language, caption }: Props) {
  const [copied, setCopied] = useState(false)

  const html = useMemo(() => {
    const name = highlighterFor(language)
    const highlighted = name
      ? hljs.highlight(code, { language: name, ignoreIllegals: true }).value
      : hljs.highlightAuto(code, ['python', 'sql', 'cpp', 'java', 'javascript', 'c']).value
    return DOMPurify.sanitize(highlighted, { ALLOWED_TAGS: ['span'], ALLOWED_ATTR: ['class'] })
  }, [code, language])

  async function copy() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
        <span style={{ fontSize: '0.85em', color: '#4b5563' }}>{caption ?? language ?? ''}</span>
        <button type="button" onClick={() => void copy()} style={{ fontSize: '0.85em' }}>
          {copied ? '✓ Copied' : 'Copy code'}
        </button>
      </div>
      {code ? (
        <pre className="code-view hljs">
          <code dangerouslySetInnerHTML={{ __html: html }} />
        </pre>
      ) : (
        <p style={{ color: '#6b7280', fontStyle: 'italic' }}>No code was saved.</p>
      )}
    </div>
  )
}
