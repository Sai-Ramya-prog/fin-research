/**
 * src/pages/SessionsPage.tsx
 * Lists all sessions, lets the user create and delete sessions.
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  listSessions,
  createSession,
  deleteSession,
  type SessionSummary,
} from '../api/sessions'

export function SessionsPage() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  async function load() {
    try {
      const data = await listSessions()
      setSessions(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load sessions')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    if (!newName.trim()) return
    setCreateError(null)
    setCreating(true)
    try {
      const sess = await createSession(newName.trim())
      setSessions(prev => [...prev, { ...sess, document_count: 0 }])
      setNewName('')
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : 'Failed to create session')
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(id: string) {
    if (confirmId !== id) { setConfirmId(id); return }
    setDeletingId(id)
    try {
      await deleteSession(id)
      setSessions(prev => prev.filter(s => s.id !== id))
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDeletingId(null)
      setConfirmId(null)
    }
  }

  if (loading) return <p style={styles.msg}>Loading sessions…</p>
  if (error)   return <p style={{ ...styles.msg, color: '#b91c1c' }}>{error}</p>

  return (
    <div style={styles.page}>
      <h1 style={styles.h1}>Sessions</h1>

      {/* Create form */}
      <form onSubmit={handleCreate} style={styles.createRow}>
        <input
          type="text"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          placeholder="New session name…"
          disabled={creating}
          style={styles.textInput}
        />
        <button type="submit" disabled={creating || !newName.trim()} style={styles.btn}>
          {creating ? 'Creating…' : 'Create session'}
        </button>
        {createError && <span style={{ color: '#b91c1c', fontSize: '0.85rem' }}>{createError}</span>}
      </form>

      {sessions.length === 0 ? (
        <p style={styles.empty}>No sessions yet — create one above.</p>
      ) : (
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={th}>Name</th>
              <th style={th}>Documents</th>
              <th style={th}>Created</th>
              <th style={th} />
            </tr>
          </thead>
          <tbody>
            {sessions.map(s => (
              <tr key={s.id}>
                <td style={td}>
                  <Link to={`/sessions/${s.id}`} style={styles.link}>{s.name}</Link>
                </td>
                <td style={td}>{s.document_count}</td>
                <td style={td}>{new Date(s.created_at).toLocaleDateString()}</td>
                <td style={{ ...td, textAlign: 'right' }}>
                  {confirmId === s.id ? (
                    <span style={{ display: 'inline-flex', gap: '6px' }}>
                      <button
                        onClick={() => handleDelete(s.id)}
                        disabled={deletingId === s.id}
                        style={{ ...btnSm, background: '#b91c1c', color: '#fff', border: 'none' }}
                      >
                        {deletingId === s.id ? 'Deleting…' : 'Confirm delete'}
                      </button>
                      <button onClick={() => setConfirmId(null)} style={btnSm}>Cancel</button>
                    </span>
                  ) : (
                    <button onClick={() => handleDelete(s.id)} style={{ ...btnSm, color: '#b91c1c' }}>
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  page: { maxWidth: '760px', margin: '0 auto', padding: '32px 20px' },
  h1: { margin: '0 0 24px', fontSize: '1.6rem', fontWeight: 700, color: '#111827' },
  createRow: { display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '24px', flexWrap: 'wrap' },
  textInput: { flex: 1, minWidth: '200px', padding: '8px 12px', border: '1px solid #d1d5db', borderRadius: '6px', fontSize: '0.9rem' },
  btn: { padding: '8px 18px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: '6px', fontSize: '0.9rem', fontWeight: 600, cursor: 'pointer' },
  table: { width: '100%', borderCollapse: 'collapse' },
  empty: { color: '#6b7280', fontSize: '0.95rem' },
  msg: { textAlign: 'center', marginTop: '60px', color: '#6b7280' },
  link: { color: '#2563eb', textDecoration: 'none', fontWeight: 500 },
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
}

const td: React.CSSProperties = {
  padding: '10px 12px',
  borderBottom: '1px solid #e5e7eb',
  fontSize: '0.9rem',
  color: '#374151',
}

const btnSm: React.CSSProperties = {
  padding: '4px 12px',
  border: '1px solid #d1d5db',
  borderRadius: '6px',
  background: '#fff',
  cursor: 'pointer',
  fontSize: '0.8rem',
}
