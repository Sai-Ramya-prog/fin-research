/**
 * src/pages/SessionDetailPage.tsx
 * Shows all documents in a session, the upload form, and status polling.
 */

import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getSession, type Session, type Document } from '../api/sessions'
import { DocumentRow } from '../components/DocumentRow'
import { UploadForm } from '../components/UploadForm'

export function SessionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!id) return
    try {
      const data = await getSession(id)
      setSession(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load session')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { load() }, [load])

  function handleUploaded(docId: string) {
    // Refresh session to pull in the new document row
    load()
    // DocumentRow will self-poll; just need the row to appear.
    void docId
  }

  function handleDocDeleted(docId: string) {
    setSession(prev =>
      prev ? { ...prev, documents: prev.documents.filter(d => d.id !== docId) } : prev,
    )
  }

  if (loading) return <p style={styles.msg}>Loading…</p>
  if (error)   return <p style={{ ...styles.msg, color: '#b91c1c' }}>{error}</p>
  if (!session) return null

  const docs: Document[] = session.documents ?? []

  return (
    <div style={styles.page}>
      <Link to="/" style={styles.back}>← All sessions</Link>
      <h1 style={styles.h1}>{session.name}</h1>
      <p style={styles.sub}>
        Created {new Date(session.created_at).toLocaleDateString()} · {docs.length} document{docs.length !== 1 ? 's' : ''}
      </p>

      {/* Document table */}
      {docs.length === 0 ? (
        <p style={styles.empty}>No documents yet.</p>
      ) : (
        <div style={styles.tableWrap}>
          <table style={styles.table}>
            <thead>
              <tr>
                <th style={th}>Filename</th>
                <th style={th}>Company</th>
                <th style={th}>FY</th>
                <th style={th}>Pages</th>
                <th style={th}>Status</th>
                <th style={th} />
              </tr>
            </thead>
            <tbody>
              {docs.map(doc => (
                <DocumentRow
                  key={doc.id}
                  initialDoc={doc}
                  onDelete={handleDocDeleted}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Upload form */}
      <UploadForm sessionId={session.id} onUploaded={handleUploaded} />
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  page: { maxWidth: '900px', margin: '0 auto', padding: '32px 20px' },
  back: { color: '#6b7280', textDecoration: 'none', fontSize: '0.875rem' },
  h1: { margin: '12px 0 4px', fontSize: '1.6rem', fontWeight: 700, color: '#111827' },
  sub: { margin: '0 0 24px', color: '#6b7280', fontSize: '0.875rem' },
  tableWrap: { overflowX: 'auto' },
  table: { width: '100%', borderCollapse: 'collapse' },
  empty: { color: '#6b7280', fontSize: '0.95rem', marginBottom: '16px' },
  msg: { textAlign: 'center', marginTop: '60px', color: '#6b7280' },
}

const th: React.CSSProperties = {
  padding: '10px 12px',
  background: '#f3f4f6',
  borderBottom: '2px solid #e5e7eb',
  textAlign: 'left',
  fontSize: '0.8rem',
  fontWeight: 700,
  color: '#6b7280',
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  whiteSpace: 'nowrap',
}
