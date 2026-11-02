
import DOMPurify from 'dompurify'
import { marked } from 'marked'

type SafeMarkdownProps = {
  content: string
  className?: string
}

export default function SafeMarkdown({
  content,
  className = '',
}: SafeMarkdownProps) {
  const rawHtml = marked.parse(content, { async: false })

  const safeHtml = DOMPurify.sanitize(rawHtml, {
    USE_PROFILES: { html: true },
  })

  return (
    <div
      className={`safe-markdown ${className}`.trim()}
      dangerouslySetInnerHTML={{ __html: safeHtml }}
    />
  )
}
