import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorText } from '../apiErrors'
import { fmtTime, isPass, runKindText, verdictSummary, verdictText, type RunDetail } from '../lib/grading'
import CodeBlock from './CodeBlock'

type Props = {
  examId: string
  sessionId: string
  runId: string
  /** Show the run's code too (run history); the answer view already shows the code. */
  showCode?: boolean
}

/** One run's per-test results from GET /exams/{id}/sessions/{sid}/runs/{run_id}. */
export default function RunResults({ examId, sessionId, runId, showCode = false }: Props) {
  const [run, setRun] = useState<RunDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    void api
      .GET('/api/v1/exams/{exam_id}/sessions/{session_id}/runs/{run_id}', {
        params: { path: { exam_id: examId, session_id: sessionId, run_id: runId } },
      })
      .then((result) => {
        if (cancelled) return
        if (result.error) setError(errorText(result.error, 'Could not load the run.'))
        else setRun(result.data as RunDetail)
      })
    return () => {
      cancelled = true
    }
  }, [examId, sessionId, runId])

  if (error) return <p role="alert" style={{ color: '#b91c1c' }}>{error}</p>
  if (!run) return <p>Loading run…</p>

  return (
    <div>
      <p style={{ margin: '0 0 6px' }}>
        <strong>{runKindText(run.kind)}</strong> · {run.language} · {fmtTime(run.created_at)}
        {run.status === 'error' && <span style={{ color: '#b91c1c' }}> · grader error</span>}
        <br />
        <span style={{ color: '#4b5563' }}>{verdictSummary(run.verdicts)}</span>
      </p>
      {showCode && <CodeBlock code={run.code} language={run.language} caption="Code in this run" />}
      {run.results.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: '8px' }}>
          <table className="monitor-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9em' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid #d1d5db' }}>
                <th>Test</th>
                <th>Type</th>
                <th>Result</th>
                <th>Time</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {run.results.map((t) => (
                <tr key={t.test_id} style={{ borderBottom: '1px solid #f3f4f6', verticalAlign: 'top' }}>
                  <td>#{t.ordinal}{t.group && t.group !== 'main' ? ` (${t.group})` : ''}</td>
                  <td>{t.is_sample ? 'Sample' : 'Hidden'}</td>
                  <td style={{ color: isPass(t.verdict) ? '#166534' : '#b91c1c', fontWeight: 'bold', whiteSpace: 'nowrap' }}>
                    {verdictText(t.verdict)}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{t.time_ms !== null ? `${t.time_ms} ms` : '—'}</td>
                  <td style={{ maxWidth: '360px' }}>
                    {t.message && <div>{t.message}</div>}
                    {t.stdout && (
                      <details>
                        <summary>Output</summary>
                        <pre style={{ whiteSpace: 'pre-wrap', margin: '4px 0' }}>{t.stdout}</pre>
                      </details>
                    )}
                    {t.stderr && (
                      <details>
                        <summary>Errors</summary>
                        <pre style={{ whiteSpace: 'pre-wrap', margin: '4px 0', color: '#b91c1c' }}>{t.stderr}</pre>
                      </details>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
