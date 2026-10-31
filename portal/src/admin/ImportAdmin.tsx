import { useState } from 'react'
import { API_URL } from '../api/client'
import { errorText } from '../apiErrors'
import ConfirmDialog from '../components/ConfirmDialog'
import { useToast } from '../components/ToastProvider'
import { errorStyle, sectionStyle, sessionToken } from './adminShared'

type ImportReport = {
  dry_run: boolean
  rows: number
  created: number
  updated: number
  batch_memberships_added: number
  errors: { line: number; message: string }[]
}

const COLUMNS = ['email', 'full_name', 'role', 'college_id', 'department_code', 'batch_code']

const SAMPLE = `email,full_name,role,college_id,department_code,batch_code
asha.patel@college.edu,Asha Patel,student,CSE-2025-001,CSE,CSE-AIML-2025-A
rahul.verma@college.edu,Rahul Verma,student,CSE-2025-002,CSE,CSE-AIML-2025-A
meera.iyer@college.edu,Dr. Meera Iyer,professor,FAC-017,CSE,
`

/** POST /admin/users/import is multipart, so it is sent with fetch rather than the typed client. */
async function upload(file: File, dryRun: boolean): Promise<ImportReport> {
  const form = new FormData()
  form.append('file', file)
  const token = sessionToken()
  const response = await fetch(`${API_URL}/api/v1/admin/users/import?dry_run=${dryRun}`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(errorText(data, `Import failed (HTTP ${response.status}).`))
  return data as ImportReport
}

export default function ImportAdmin() {
  const { notify } = useToast()
  const [file, setFile] = useState<File | null>(null)
  const [report, setReport] = useState<ImportReport | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [fileKey, setFileKey] = useState(0)

  function downloadSample() {
    const url = URL.createObjectURL(new Blob([SAMPLE], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'aegis-users-sample.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  async function check() {
    if (!file) return
    setBusy(true)
    setError('')
    setReport(null)
    try {
      setReport(await upload(file, true))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not check the file.')
    } finally {
      setBusy(false)
    }
  }

  async function confirmImport() {
    if (!file) return
    setBusy(true)
    try {
      const saved = await upload(file, false)
      setReport(saved)
      setConfirming(false)
      notify(`Imported: ${saved.created} added, ${saved.updated} updated, ${saved.batch_memberships_added} batch membership(s).`, 'success')
      setFile(null)
      setFileKey((k) => k + 1)
    } catch (err) {
      setConfirming(false)
      setError(err instanceof Error ? err.message : 'Import failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2 style={{ margin: 0 }}>CSV import</h2>
      <div style={sectionStyle}>
        <p style={{ marginTop: 0 }}>
          Add or update students and professors in bulk. Columns (header row required):
        </p>
        <p><code>{COLUMNS.join(', ')}</code></p>
        <ul style={{ margin: '0 0 10px', paddingLeft: '20px', color: '#4b5563' }}>
          <li><code>email</code> and <code>full_name</code> are required; <code>role</code> is student, professor or admin.</li>
          <li><code>department_code</code> and <code>batch_code</code> must already exist (Departments, Batches).</li>
          <li>All-or-nothing: if any row has a problem, nothing is saved.</li>
        </ul>
        <button type="button" onClick={downloadSample}>Download sample CSV</button>
      </div>

      <div style={sectionStyle}>
        <label style={{ display: 'block' }}>
          CSV file (UTF-8, under 2 MB)
          <input
            key={fileKey}
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null)
              setReport(null)
              setError('')
            }}
            style={{ display: 'block', margin: '6px 0 10px' }}
          />
        </label>
        <button type="button" disabled={!file || busy} onClick={() => void check()} style={{ fontWeight: 'bold' }}>
          {busy && !confirming ? 'Checking…' : 'Check file (dry run)'}
        </button>
        {error && <p role="alert" style={errorStyle}>{error}</p>}
      </div>

      {report && (
        <div style={sectionStyle} data-testid="import-report">
          <strong>{report.dry_run ? 'Dry run: nothing saved yet' : 'Imported'}</strong>
          <ul>
            <li>{report.rows} row(s) read</li>
            <li>{report.created} user(s) {report.dry_run ? 'to add' : 'added'}</li>
            <li>{report.updated} user(s) {report.dry_run ? 'to update' : 'updated'}</li>
            <li>{report.batch_memberships_added} batch membership(s) {report.dry_run ? 'to add' : 'added'}</li>
          </ul>
          {report.errors.length > 0 ? (
            <>
              <p style={{ ...errorStyle, fontWeight: 'bold' }}>{report.errors.length} problem(s); fix the file and check again:</p>
              <table className="monitor-table" style={{ borderCollapse: 'collapse' }}>
                <thead><tr style={{ textAlign: 'left' }}><th>Line</th><th>Problem</th></tr></thead>
                <tbody>
                  {report.errors.map((e, i) => (
                    <tr key={`${e.line}-${i}`}><td>{e.line}</td><td style={{ color: '#b91c1c' }}>{e.message}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            report.dry_run && (
              <button type="button" onClick={() => setConfirming(true)} style={{ fontWeight: 'bold' }}>
                Confirm import
              </button>
            )
          )}
        </div>
      )}

      {confirming && report && (
        <ConfirmDialog
          title="Import these users?"
          confirmLabel="Import"
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void confirmImport()}
        >
          <p style={{ margin: 0 }}>
            {report.created} added, {report.updated} updated, {report.batch_memberships_added} batch membership(s).
          </p>
        </ConfirmDialog>
      )}
    </section>
  )
}
