/** Turn a backend error body into a readable message. */
export function errorText(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors.
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) =>
        `${(d.loc ?? []).slice(1).join('.')}: ${d.msg ?? ''}`.trim(),
      )
      .join('; ')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) {
    // ApiError: { code, message, ... }
    return String((detail as { message: unknown }).message)
  }
  return fallback
}
