import { useCallback, useEffect, useRef, useState } from 'react'
import { api, API_URL } from './api/client'
import { errorText } from './apiErrors'
import ConfirmDialog from './components/ConfirmDialog'
import { useToast } from './components/ToastProvider'

/** Server limit (AEGIS_MAX_DATA_FILE_BYTES); checked here first so a big upload fails fast. */
const MAX_MB = 200

type ColumnSummary = {
  name: string
  type: 'number' | 'text' | 'empty'
  filled: number
  missing: number
  distinct: number | null
  min?: number
  max?: number
  mean?: number
  examples?: string[]
}

type QuestionFile = {
  id: string
  name: string
  size_bytes: number
  kind: 'table' | 'text' | 'binary'
  rows: number | null
  column_count: number | null
  read_hint: string
  created_at: string
  meta?: {
    rows?: number
    columns?: ColumnSummary[]
    header?: string[]
    preview?: string[][] | string
    lines?: number
    stats_rows?: number
  }
}

function sessionToken(): string | null {
  try {
    const saved = JSON.parse(localStorage.getItem('aegis_session') ?? 'null') as { token?: string } | null
    return saved?.token ?? null
  } catch {
    return null
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function num(n: number | undefined): string {
  if (n === undefined || n === null) return ''
  return Number.isInteger(n) ? n.toLocaleString() : String(Number(n.toPrecision(5)))
}

/** multipart upload with progress (fetch cannot report upload progress). */
function upload(questionId: string, file: File, onProgress: (fraction: number) => void): Promise<QuestionFile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_URL}/api/v1/questions/${questionId}/files`)
    const token = sessionToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        // not JSON
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as QuestionFile)
      else reject(new Error(errorText(body ?? undefined, `Upload failed (${xhr.status}).`)))
    }
    xhr.onerror = () => reject(new Error('Upload failed: check the connection to the server.'))
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

function Preview({ file }: { file: QuestionFile }) {
  const meta = file.meta ?? {}
  const cell = { padding: '3px 8px', borderBottom: '1px solid #e5e7eb', whiteSpace: 'nowrap' as const }
  if (file.kind === 'table') {
    const rows = Array.isArray(meta.preview) ? meta.preview : []
    return (
      <div style={{ marginTop: '8px' }}>
        <strong style={{ fontSize: '0.9em' }}>Columns</strong>
        {meta.stats_rows !== undefined && meta.rows !== undefined && meta.stats_rows < meta.rows && (
          <span style={{ fontSize: '0.85em', color: '#6b7280' }}> (figures from the first {meta.stats_rows.toLocaleString()} rows)</span>
        )}
        <div style={{ overflowX: 'auto', maxHeight: '240px', border: '1px solid #e5e7eb', borderRadius: '6px', margin: '4px 0 10px' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: '0.85em', width: 'max-content', minWidth: '100%' }}>
            <thead><tr style={{ textAlign: 'left', background: '#f9fafb' }}>
              <th style={cell}>Column</th><th style={cell}>Type</th><th style={cell}>Missing</th><th style={cell}>Distinct</th>
              <th style={cell}>Min</th><th style={cell}>Max</th><th style={cell}>Mean / examples</th>
            </tr></thead>
            <tbody>
              {(meta.columns ?? []).map((c) => (
                <tr key={c.name}>
                  <td style={cell}><strong>{c.name}</strong></td><td style={cell}>{c.type}</td>
                  <td style={{ ...cell, color: c.missing ? '#b91c1c' : undefined }}>{c.missing.toLocaleString()}</td>
                  <td style={cell}>{c.distinct === null ? '1000+' : c.distinct.toLocaleString()}</td>
                  <td style={cell}>{c.type === 'number' ? num(c.min) : ''}</td>
                  <td style={cell}>{c.type === 'number' ? num(c.max) : ''}</td>
                  <td style={cell}>{c.type === 'number' ? num(c.mean) : (c.examples ?? []).join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <strong style={{ fontSize: '0.9em' }}>First {rows.length} rows</strong>
        <div style={{ overflowX: 'auto', maxHeight: '240px', border: '1px solid #e5e7eb', borderRadius: '6px', marginTop: '4px' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: '0.85em', fontFamily: 'monospace', width: 'max-content', minWidth: '100%' }}>
            <thead><tr style={{ textAlign: 'left', background: '#f9fafb' }}>{(meta.header ?? []).map((h, i) => <th key={i} style={cell}>{h}</th>)}</tr></thead>
            <tbody>{rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} style={cell}>{v}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </div>
    )
  }
  if (file.kind === 'text') {
    return (
      <pre style={{ marginTop: '8px', maxHeight: '240px', overflow: 'auto', background: '#f9fafb', padding: '8px', borderRadius: '6px', fontSize: '0.85em' }}>
        {typeof meta.preview === 'string' ? meta.preview : ''}
      </pre>
    )
  }
  return <p style={{ color: '#6b7280', fontSize: '0.9em' }}>Binary file: no preview. Students' code can still open it.</p>
}

/** Data files for one question (Step B of the wizard). */
export default function QuestionFilesPanel({ questionId }: { questionId: string }) {
  const { notify } = useToast()
  const [files, setFiles] = useState<QuestionFile[] | null>(null)
  const [error, setError] = useState('')
  const [progress, setProgress] = useState<number | null>(null)
  const [open, setOpen] = useState<QuestionFile | null>(null)
  const [removing, setRemoving] = useState<QuestionFile | null>(null)
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    const result = await api.GET('/api/v1/questions/{question_id}/files', { params: { path: { question_id: questionId } } })
    if (result.error) setError(errorText(result.error, 'Could not load the data files.'))
    else setFiles(result.data as QuestionFile[])
  }, [questionId])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  async function choose(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setError('')
    if (file.size > MAX_MB * 1024 * 1024) {
      setError(`Files can be up to ${MAX_MB} MB.`)
      return
    }
    setProgress(0)
    try {
      const added = await upload(questionId, file, setProgress)
      notify(`${added.name} uploaded${added.rows !== null ? ` (${added.rows.toLocaleString()} rows)` : ''}.`, 'success')
      setOpen(added)
      void load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setProgress(null)
    }
  }

  async function show(file: QuestionFile) {
    if (open?.id === file.id) {
      setOpen(null)
      return
    }
    const result = await api.GET('/api/v1/questions/{question_id}/files/{file_id}', {
      params: { path: { question_id: questionId, file_id: file.id } },
    })
    if (result.error) notify(errorText(result.error, 'Could not open the file.'), 'error')
    else setOpen(result.data as QuestionFile)
  }

  async function remove(file: QuestionFile) {
    setBusy(true)
    const result = await api.DELETE('/api/v1/questions/{question_id}/files/{file_id}', {
      params: { path: { question_id: questionId, file_id: file.id } },
    })
    setBusy(false)
    setRemoving(null)
    if (result.error) {
      notify(errorText(result.error, 'Could not remove the file.'), 'error')
      return
    }
    if (open?.id === file.id) setOpen(null)
    notify(`${file.name} removed.`, 'success')
    void load()
  }

  return (
    <section style={{ border: '1px solid #e5e7eb', borderRadius: '10px', padding: '12px 14px', margin: '0 0 16px', textAlign: 'left' }}>
      <strong>Data files (optional)</strong>
      <p style={{ margin: '4px 0 8px', color: '#4b5563', fontSize: '0.92em' }}>
        Datasets for this question, up to {MAX_MB} MB each (e.g. <code>marks.csv</code>). Students see the file in the
        exam with a preview and a column summary, and their code opens it by name, e.g. <code>pd.read_csv("marks.csv")</code>.
        Your reference solution can use it too. Nothing is downloaded to students' laptops and no internet is involved.
      </p>
      <input ref={input} type="file" onChange={(e) => void choose(e)} style={{ display: 'none' }} />
      <button type="button" onClick={() => input.current?.click()} disabled={progress !== null}>
        {progress !== null ? `Uploading… ${Math.round(progress * 100)}%` : 'Upload a data file'}
      </button>
      {progress !== null && progress >= 1 && <span style={{ marginLeft: '8px', color: '#6b7280' }}>Reading the file…</span>}
      {error && <p role="alert" style={{ color: '#b91c1c', margin: '8px 0 0' }}>{error}</p>}
      {files && files.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '10px 0 0' }}>
          {files.map((f) => (
            <li key={f.id} style={{ borderTop: '1px solid #f3f4f6', padding: '6px 0' }}>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'baseline', flexWrap: 'wrap' }}>
                <button type="button" onClick={() => void show(f)} style={{ fontFamily: 'monospace', fontWeight: 'bold' }} aria-expanded={open?.id === f.id}>
                  {f.name}
                </button>
                <span style={{ color: '#6b7280', fontSize: '0.9em' }}>
                  {formatSize(f.size_bytes)}
                  {f.kind === 'table' && f.rows !== null && ` · ${f.rows.toLocaleString()} rows × ${f.column_count} columns`}
                </span>
                <code style={{ fontSize: '0.85em', color: '#374151' }}>{f.read_hint}</code>
                <button type="button" onClick={() => setRemoving(f)} style={{ marginLeft: 'auto' }}>Remove</button>
              </div>
              {open?.id === f.id && <Preview file={open} />}
            </li>
          ))}
        </ul>
      )}
      {removing && (
        <ConfirmDialog
          title={`Remove ${removing.name}?`}
          confirmLabel="Remove"
          danger
          busy={busy}
          onCancel={() => setRemoving(null)}
          onConfirm={() => void remove(removing)}
        >
          <p style={{ margin: 0 }}>Code that opens this file will fail. (Not possible while an exam using this question is live.)</p>
        </ConfirmDialog>
      )}
    </section>
  )
}
