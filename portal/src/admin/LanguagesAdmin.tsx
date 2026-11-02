import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { errorText } from '../apiErrors'
import ConfirmDialog from '../components/ConfirmDialog'
import NumberInput from '../components/NumberInput'
import { useToast } from '../components/ToastProvider'
import { ENGINEERING_LANGUAGES } from '../lib/languagePack'
import { errorStyle, inputStyle, sectionStyle } from './adminShared'

type LanguageOut = {
  id: string
  key: string
  name: string
  monaco_id: string
  file_name: string
  time_multiplier: number
  is_enabled: boolean
}

const emptyForm = { key: '', name: '', judge0_id: 71, file_name: '', monaco_id: 'plaintext', time_multiplier: 1, is_enabled: true }

export default function LanguagesAdmin() {
  const { notify } = useToast()
  const [items, setItems] = useState<LanguageOut[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(emptyForm)
  const [formError, setFormError] = useState('')
  const [editing, setEditing] = useState<LanguageOut | null>(null)
  const [edit, setEdit] = useState({ name: '', judge0_id: '', file_name: '', monaco_id: '', time_multiplier: 1 })
  const [packOpen, setPackOpen] = useState(false)
  const [packLog, setPackLog] = useState<string[]>([])

  const load = useCallback(async () => {
    const result = await api.GET('/api/v1/admin/languages')
    if (result.error) setError(errorText(result.error, 'Could not load languages.'))
    else setItems(result.data as LanguageOut[])
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const byKey = new Map((items ?? []).map((l) => [l.key, l]))
  const missing = ENGINEERING_LANGUAGES.filter((l) => !byKey.has(l.key))
  const disabledInPack = ENGINEERING_LANGUAGES.filter((l) => byKey.get(l.key)?.is_enabled === false)

  async function setEnabled(lang: LanguageOut, enabled: boolean) {
    const result = await api.PATCH('/api/v1/admin/languages/{language_id}', {
      params: { path: { language_id: lang.id } },
      body: { is_enabled: enabled },
    })
    if (result.error) {
      notify(errorText(result.error, 'Could not change the language.'), 'error')
      return false
    }
    return true
  }

  async function toggle(lang: LanguageOut) {
    setBusy(true)
    if (await setEnabled(lang, !lang.is_enabled)) {
      notify(`${lang.name} ${lang.is_enabled ? 'disabled' : 'enabled'}.`, 'success')
      void load()
    }
    setBusy(false)
  }

  async function addLanguage(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setFormError('')
    const result = await api.POST('/api/v1/admin/languages', {
      body: { ...form, key: form.key.trim().toLowerCase(), name: form.name.trim(), file_name: form.file_name.trim() },
    })
    setBusy(false)
    if (result.error) {
      setFormError(errorText(result.error, 'Could not add the language.'))
      return
    }
    notify(`${form.name} added.`, 'success')
    setForm(emptyForm)
    setAdding(false)
    void load()
  }

  async function saveEdit() {
    if (!editing) return
    setBusy(true)
    setFormError('')
    const result = await api.PATCH('/api/v1/admin/languages/{language_id}', {
      params: { path: { language_id: editing.id } },
      body: {
        name: edit.name.trim(),
        file_name: edit.file_name.trim(),
        monaco_id: edit.monaco_id.trim(),
        time_multiplier: edit.time_multiplier,
        // The API does not return the current Judge0 id, so it is only sent when typed.
        ...(edit.judge0_id.trim() ? { judge0_id: Number(edit.judge0_id) } : {}),
      },
    })
    setBusy(false)
    if (result.error) {
      setFormError(errorText(result.error, 'Could not save.'))
      return
    }
    notify(`${edit.name} updated.`, 'success')
    setEditing(null)
    void load()
  }

  /** Add every missing pack language and enable pack languages that are switched off. */
  async function installPack() {
    setBusy(true)
    const log: string[] = []
    for (const spec of missing) {
      const { course: _course, ...body } = spec
      void _course
      const result = await api.POST('/api/v1/admin/languages', { body: { ...body, is_enabled: true } })
      log.push(result.error ? `✖ ${spec.name}: ${errorText(result.error, 'failed')}` : `✓ Added ${spec.name}`)
    }
    for (const spec of disabledInPack) {
      const lang = byKey.get(spec.key)
      if (lang) log.push((await setEnabled(lang, true)) ? `✓ Enabled ${spec.name}` : `✖ Could not enable ${spec.name}`)
    }
    setBusy(false)
    setPackOpen(false)
    setPackLog(log)
    notify(`Language pack: ${log.filter((l) => l.startsWith('✓')).length} change(s).`, 'success')
    void load()
  }

  return (
    <section>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0 }}>Languages</h2>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button type="button" onClick={() => setPackOpen(true)} disabled={!items} style={{ fontWeight: 'bold' }}>
            Add engineering language pack
          </button>
          {!adding && <button type="button" onClick={() => { setAdding(true); setFormError('') }}>+ Add one language</button>}
        </div>
      </div>

      <div style={{ ...sectionStyle, background: '#eff6ff' }}>
        <p style={{ margin: 0 }}>
          Each language is a row the judge runs. Enabled languages appear in every subject’s
          Settings; professors tick the ones a subject uses, and any exam can then be answered
          in them. Code runs on <strong>Judge0</strong>: after adding languages, check the ids with{' '}
          <code>python -m app.cli check-judge0</code> in <code>backend/</code>. The local
          development runner only runs Python.
        </p>
      </div>

      {packLog.length > 0 && (
        <div style={sectionStyle} data-testid="pack-log">
          <strong>Language pack result</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: '18px' }}>
            {packLog.map((line) => <li key={line} style={{ color: line.startsWith('✖') ? '#b91c1c' : undefined }}>{line}</li>)}
          </ul>
        </div>
      )}

      {adding && (
        <form onSubmit={addLanguage} style={sectionStyle}>
          <strong>New language</strong>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0 12px', marginTop: '8px' }}>
            <label>Key (e.g. dart)<input style={inputStyle} required pattern="[a-z0-9_+.\-]+" maxLength={32} value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} /></label>
            <label>Name shown to students<input style={inputStyle} required maxLength={100} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>Judge0 language id<NumberInput style={inputStyle} min={1} required value={form.judge0_id} onChange={(v) => setForm({ ...form, judge0_id: v })} /></label>
            <label>File name (e.g. main.dart)<input style={inputStyle} required pattern="[A-Za-z0-9_.\-]+" maxLength={64} value={form.file_name} onChange={(e) => setForm({ ...form, file_name: e.target.value })} /></label>
            <label>Editor mode (Monaco id)<input style={inputStyle} required maxLength={32} value={form.monaco_id} onChange={(e) => setForm({ ...form, monaco_id: e.target.value })} /></label>
            <label>Time multiplier (0.5–10)<NumberInput style={inputStyle} min={0.5} max={10} step="0.5" value={form.time_multiplier} onChange={(v) => setForm({ ...form, time_multiplier: v })} /></label>
          </div>
          {formError && <p role="alert" style={errorStyle}>{formError}</p>}
          <div style={{ display: 'flex', gap: '8px' }}>
            <button type="submit" disabled={busy} style={{ fontWeight: 'bold' }}>Add language</button>
            <button type="button" disabled={busy} onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </form>
      )}

      {error && <p role="alert" style={errorStyle}>{error}</p>}
      {!items && !error && <p>Loading…</p>}
      {items && (
        <div style={{ overflowX: 'auto', marginTop: '12px' }}>
          <table className="monitor-table" style={{ width: '100%', borderCollapse: 'collapse', minWidth: '720px' }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '2px solid #d1d5db' }}>
                <th>Name</th><th>Key</th><th>File</th><th>Editor</th><th>Time ×</th><th>Status</th><th />
              </tr>
            </thead>
            <tbody>
              {items.map((l) => (
                <tr key={l.id} style={{ borderBottom: '1px solid #e5e7eb', verticalAlign: 'top' }}>
                  {editing?.id === l.id ? (
                    <td colSpan={7}>
                      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
                        <label>Name<input style={{ ...inputStyle, margin: '4px 0' }} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
                        <label>Judge0 id (blank = keep)<input style={{ ...inputStyle, margin: '4px 0', width: '120px' }} inputMode="numeric" value={edit.judge0_id} onChange={(e) => setEdit({ ...edit, judge0_id: e.target.value.replace(/\D/g, '') })} /></label>
                        <label>File<input style={{ ...inputStyle, margin: '4px 0' }} value={edit.file_name} onChange={(e) => setEdit({ ...edit, file_name: e.target.value })} /></label>
                        <label>Editor<input style={{ ...inputStyle, margin: '4px 0', width: '130px' }} value={edit.monaco_id} onChange={(e) => setEdit({ ...edit, monaco_id: e.target.value })} /></label>
                        <label>Time ×<NumberInput style={{ ...inputStyle, margin: '4px 0', width: '90px' }} min={0.5} max={10} step="0.5" value={edit.time_multiplier} onChange={(v) => setEdit({ ...edit, time_multiplier: v })} /></label>
                        <button type="button" disabled={busy} onClick={() => void saveEdit()} style={{ fontWeight: 'bold' }}>Save</button>
                        <button type="button" onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                      {formError && <p role="alert" style={errorStyle}>{formError}</p>}
                    </td>
                  ) : (
                    <>
                      <td><strong>{l.name}</strong></td>
                      <td><code>{l.key}</code></td>
                      <td>{l.file_name}</td>
                      <td>{l.monaco_id}</td>
                      <td>{l.time_multiplier}</td>
                      <td style={{ color: l.is_enabled ? '#166534' : '#6b7280', fontWeight: 'bold' }}>{l.is_enabled ? 'Enabled' : 'Disabled'}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <button type="button" disabled={busy} onClick={() => void toggle(l)}>{l.is_enabled ? 'Disable' : 'Enable'}</button>{' '}
                        <button type="button" onClick={() => { setEditing(l); setFormError(''); setEdit({ name: l.name, judge0_id: '', file_name: l.file_name, monaco_id: l.monaco_id, time_multiplier: l.time_multiplier }) }}>Edit</button>
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {packOpen && (
        <ConfirmDialog
          title="Add the engineering language pack?"
          confirmLabel={missing.length || disabledInPack.length ? 'Add and enable' : 'Close'}
          busy={busy}
          onCancel={() => setPackOpen(false)}
          onConfirm={() => (missing.length || disabledInPack.length ? void installPack() : setPackOpen(false))}
        >
          <p style={{ marginTop: 0 }}>
            Languages used in CSE, AI &amp; ML and Data Science labs ({ENGINEERING_LANGUAGES.length} in total, Judge0 CE ids).
            Existing languages are not changed, except switched-off ones in the pack, which are enabled.
          </p>
          {missing.length === 0 && disabledInPack.length === 0 ? (
            <p><strong>Every language in the pack is already added and enabled.</strong></p>
          ) : (
            <div style={{ maxHeight: '260px', overflowY: 'auto' }}>
              {missing.length > 0 && <p style={{ margin: '4px 0' }}><strong>To add ({missing.length}):</strong></p>}
              <ul style={{ margin: 0, paddingLeft: '18px' }}>
                {missing.map((l) => <li key={l.key}>{l.name} <span style={{ color: '#6b7280' }}>· {l.course}</span></li>)}
              </ul>
              {disabledInPack.length > 0 && <p style={{ margin: '8px 0 4px' }}><strong>To enable ({disabledInPack.length}):</strong></p>}
              <ul style={{ margin: 0, paddingLeft: '18px' }}>
                {disabledInPack.map((l) => <li key={l.key}>{l.name}</li>)}
              </ul>
            </div>
          )}
        </ConfirmDialog>
      )}
    </section>
  )
}
