import { useToast } from './components/ToastProvider'
import { useState } from 'react'
import { api } from './api/client'

type CreateSubjectFormProps = {
  onCreated: () => void
  onCancel: () => void
}

const LANGUAGE_OPTIONS = [
  { value: 'python3', label: 'Python 3' },
  { value: 'c', label: 'C' },
  { value: 'cpp', label: 'C++' },
  { value: 'java', label: 'Java' },
  { value: 'sql', label: 'SQL' },
  { value: 'python3-ml', label: 'Python ML' },
]

export default function CreateSubjectForm({
  onCreated,
  onCancel,
}: CreateSubjectFormProps) {
  const { notify } = useToast()
  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [isLab, setIsLab] = useState(true)
  const [languages, setLanguages] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  function toggleLanguage(language: string) {
    setLanguages((current) =>
      current.includes(language)
        ? current.filter((item) => item !== language)
        : [...current, language],
    )
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setSuccess('')

   

    
const cleanName = name.trim()
const cleanCode = code.trim()

if (!cleanName || !cleanCode) {
  const message = 'Please enter both a subject name and subject code.'
  setError(message)
  notify(message, 'error')
  return
}

if (!/^[A-Za-z0-9_.-]{1,64}$/.test(cleanCode)) {
  const message =
    'Code must use only letters, numbers, dots, underscores, or hyphens.'
  setError(message)
  notify(message, 'error')
  return
}

if (languages.length === 0) {
  const message = 'Select at least one allowed language.'
  setError(message)
  notify(message, 'error')
  return
}


    setSaving(true)

    try {
      const { data, error: apiError, response } = await api.POST(
        '/api/v1/subjects',
        {
          body: {
            code: cleanCode,
            name: cleanName,
            department_id: null,
            is_lab: isLab,
            language_keys: languages,
            batch_ids: [],
          },
        },
      )

      if (apiError || !response.ok) {
        const details = apiError as
          | { detail?: string | { msg?: string }[] }
          | undefined

        const detail = details?.detail
        const message =
          typeof detail === 'string'
            ? detail
            : Array.isArray(detail)
              ? detail.map((item) => item.msg ?? 'Invalid field').join(', ')
              : `Request failed (${response.status}).`

        setError(message)
notify(message, 'error')
return
      }

      // The API has successfully created the subject.
      void data
      const message = 'Subject created successfully.'
setSuccess(message)
notify(message, 'success')
onCreated()
    } catch (err) {
  const message =
    err instanceof Error
      ? err.message
      : 'Could not connect to the server. Check that the backend is running.'

  setError(message)
  notify(message, 'error')
} finally {
      setSaving(false)
    }
  }

  return (
    <section className="create-subject-form">
      <h2>Create Subject</h2>
      <p>Create a subject that you can manage as its owner.</p>

      <form onSubmit={handleSubmit}>
        <div>
          <label htmlFor="subject-name">Subject name</label>
          <input
            id="subject-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Operating Systems Lab"
            maxLength={200}
            required
          />
        </div>

        <div>
          <label htmlFor="subject-code">Subject code</label>
          <input
            id="subject-code"
            value={code}
            onChange={(event) =>
              setCode(event.target.value.toUpperCase())
            }
            placeholder="e.g. DEMO-OS-LAB"
            maxLength={64}
            pattern="[A-Za-z0-9_.-]+"
            required
          />
        </div>

        <div>
          <label>
            <input
              type="checkbox"
              checked={isLab}
              onChange={(event) => setIsLab(event.target.checked)}
            />
            This is a lab subject
          </label>
        </div>

        <fieldset>
          <legend>Allowed languages</legend>

          {LANGUAGE_OPTIONS.map((language) => (
            <label key={language.value} className="language-option">
              <input
                type="checkbox"
                checked={languages.includes(language.value)}
                onChange={() => toggleLanguage(language.value)}
              />
              {language.label}
            </label>
          ))}
        </fieldset>

        {error && <p role="alert" className="form-error">{error}</p>}
        {success && <p role="status">{success}</p>}

        <div className="form-actions">
          <button type="submit" disabled={saving}>
            {saving ? 'Creating...' : 'Create Subject'}
          </button>

          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
          >
            Cancel
          </button>
        </div>
      </form>
    </section>
  )
}
