/**
 * src/components/UploadForm.tsx
 * PDF upload form: file picker, company, fiscal year, progress bar, errors.
 */

import { useState, useRef } from 'react'
import { uploadDocument } from '../api/sessions'

interface Props {
  sessionId: string
  onUploaded: (docId: string) => void
  onCancel?: () => void
}

export function UploadForm({ sessionId, onUploaded, onCancel }: Props) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [company, setCompany] = useState('')
  const [fiscalYear, setFiscalYear] = useState<number>(new Date().getFullYear())
  const [progress, setProgress] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const file = fileRef.current?.files?.[0]
    if (!file) { setError('Please choose a PDF file.'); return }
    if (!company.trim()) { setError('Company name is required.'); return }

    setError(null)
    setProgress(0)
    setBusy(true)

    try {
      const doc = await uploadDocument(sessionId, file, company.trim(), fiscalYear, setProgress)
      setProgress(null)
      setCompany('')
      if (fileRef.current) fileRef.current.value = ''
      onUploaded(doc.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setProgress(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="modal-body">
      <label className="lbl">
        PDF file
        <input ref={fileRef} type="file" accept=".pdf" disabled={busy} className="field" />
      </label>

      <label className="lbl">
        Company
        <input
          type="text"
          value={company}
          onChange={e => setCompany(e.target.value)}
          placeholder="e.g. Infosys"
          disabled={busy}
          className="field"
        />
      </label>

      <label className="lbl">
        Fiscal year
        <input
          type="number"
          value={fiscalYear}
          onChange={e => setFiscalYear(Number(e.target.value))}
          min={2000}
          max={2100}
          disabled={busy}
          className="field"
          style={{ maxWidth: 140 }}
        />
      </label>

      {progress !== null && (
        <div className="upload-progress" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
          <div style={{ width: `${progress}%` }} />
          <span>{progress}%</span>
        </div>
      )}

      {error && <p className="form-err">{error}</p>}

      <div className="modal-foot">
        {onCancel && <button type="button" className="btn outline" onClick={onCancel} disabled={busy}>Cancel</button>}
        <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Uploading…' : 'Upload'}</button>
      </div>
    </form>
  )
}
