/**
 * src/components/UploadForm.tsx
 * PDF upload form: file picker, company, fiscal year, progress bar, errors.
 */

import { useState, useRef } from 'react'
import { uploadDocument } from '../api/sessions'

interface Props {
  sessionId: string
  onUploaded: (docId: string) => void
}

export function UploadForm({ sessionId, onUploaded }: Props) {
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
    <form onSubmit={handleSubmit} style={styles.form}>
      <h3 style={styles.heading}>Upload document</h3>

      <label style={styles.label}>
        PDF file
        <input
          ref={fileRef}
          type="file"
          accept=".pdf"
          disabled={busy}
          style={styles.input}
        />
      </label>

      <label style={styles.label}>
        Company
        <input
          type="text"
          value={company}
          onChange={e => setCompany(e.target.value)}
          placeholder="e.g. Infosys"
          disabled={busy}
          style={styles.input}
        />
      </label>

      <label style={styles.label}>
        Fiscal year
        <input
          type="number"
          value={fiscalYear}
          onChange={e => setFiscalYear(Number(e.target.value))}
          min={2000}
          max={2100}
          disabled={busy}
          style={{ ...styles.input, width: '120px' }}
        />
      </label>

      {progress !== null && (
        <div style={styles.progressWrap}>
          <div style={{ ...styles.progressBar, width: `${progress}%` }} />
          <span style={styles.progressLabel}>{progress}%</span>
        </div>
      )}

      {error && <p style={styles.error}>{error}</p>}

      <button type="submit" disabled={busy} style={styles.btn}>
        {busy ? 'Uploading…' : 'Upload'}
      </button>
    </form>
  )
}

const styles: Record<string, React.CSSProperties> = {
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
    padding: '20px',
    background: '#f9fafb',
    border: '1px solid #e5e7eb',
    borderRadius: '8px',
    marginTop: '20px',
    maxWidth: '480px',
  },
  heading: { margin: 0, fontSize: '1rem', fontWeight: 600, color: '#111827' },
  label: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
    fontSize: '0.875rem',
    color: '#374151',
    fontWeight: 500,
  },
  input: {
    padding: '6px 10px',
    border: '1px solid #d1d5db',
    borderRadius: '6px',
    fontSize: '0.9rem',
    background: '#fff',
  },
  progressWrap: {
    position: 'relative',
    height: '18px',
    background: '#e5e7eb',
    borderRadius: '9999px',
    overflow: 'hidden',
  },
  progressBar: {
    position: 'absolute',
    top: 0,
    left: 0,
    height: '100%',
    background: '#3b82f6',
    transition: 'width 0.2s',
  },
  progressLabel: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '0.7rem',
    fontWeight: 700,
    color: '#1e40af',
  },
  error: { margin: 0, color: '#b91c1c', fontSize: '0.875rem' },
  btn: {
    alignSelf: 'flex-start',
    padding: '8px 20px',
    background: '#3b82f6',
    color: '#fff',
    border: 'none',
    borderRadius: '6px',
    fontSize: '0.9rem',
    fontWeight: 600,
    cursor: 'pointer',
  },
}
