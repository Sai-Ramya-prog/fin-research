/**
 * src/pages/SessionDetailPage.tsx
 * Documents table in a card. "Upload document" opens a modal with the form.
 */

import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getSession, type Session, type Document } from '../api/sessions'
import { DocumentRow } from '../components/DocumentRow'
import { UploadForm } from '../components/UploadForm'
import { Modal } from '../components/Modal'

export function SessionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)

  const load = useCallback(async () => {
    if (!id) return
    try {
      setSession(await getSession(id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load session')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { load() }, [load])

  const closeUpload = useCallback(() => setUploadOpen(false), [])

  function handleUploaded() {
    setUploadOpen(false)
    load() // pull in the new row; DocumentRow polls its own status
  }

  function handleDocDeleted(docId: string) {
    setSession(prev =>
      prev ? { ...prev, documents: prev.documents.filter(d => d.id !== docId) } : prev,
    )
  }

  if (loading) return <div className="page"><div className="empty">Loading…</div></div>
  if (error) return <div className="page"><div className="empty" style={{ color: 'var(--oxblood)' }}>{error}</div></div>
  if (!session) return null

  const docs: Document[] = session.documents ?? []

  return (
    <div className="page">
      <Link to="/" className="back">← All sessions</Link>
      <div className="head">
        <div>
          <div className="eyebrow">Session</div>
          <h1>{session.name}</h1>
          <p>Created {new Date(session.created_at).toLocaleDateString()} · {docs.length} document{docs.length !== 1 ? 's' : ''}</p>
        </div>
        <button className="btn primary" onClick={() => setUploadOpen(true)}>+ Upload document</button>
      </div>

      <div className="card flush">
        <div className="card-bar">
          <h2>Documents</h2>
          <span className="count">{docs.length} total</span>
        </div>
        {docs.length === 0 ? (
          <div className="empty">
            <b>No documents yet</b>
            Use “Upload document” to add an annual report.
          </div>
        ) : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Filename</th>
                  <th>Company</th>
                  <th>FY</th>
                  <th>Pages</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {docs.map(doc => (
                  <DocumentRow key={doc.id} initialDoc={doc} onDelete={handleDocDeleted} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {uploadOpen && (
        <Modal
          title="Upload"
          accent="document"
          subtitle="Choose an annual report PDF. It is read and indexed automatically."
          onClose={closeUpload}
        >
          <UploadForm sessionId={session.id} onUploaded={handleUploaded} onCancel={closeUpload} />
        </Modal>
      )}
    </div>
  )
}
