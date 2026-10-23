/**
 * src/pages/SessionsPage.tsx
 * Sessions as a table inside a card, with a create row.
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
      setSessions(await listSessions())
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
      setSessions(prev => [{ ...sess, document_count: 0 }, ...prev])
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

  return (
    <div className="page">
      <div className="head">
        <div>
          <div className="eyebrow">Workspaces</div>
          <h1>Your <em>sessions</em></h1>
          <p>One session per research question. Add annual reports to a session and the agent reads, tags and indexes them.</p>
        </div>
      </div>

      <div className="card flush">
        <div className="card-bar">
          <h2>All sessions</h2>
          <span className="count">{sessions.length} total</span>
        </div>

        <form onSubmit={handleCreate} className="new-row">
          <input
            type="text"
            className="field"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            placeholder="New session name, e.g. Auto sector FY25"
            disabled={creating}
            aria-label="New session name"
          />
          <button type="submit" className="btn primary" disabled={creating || !newName.trim()}>
            {creating ? 'Creating…' : '+ Create session'}
          </button>
        </form>
        {createError && <p className="errtext" style={{ paddingTop: 10 }}>{createError}</p>}

        {loading ? (
          <div className="empty">Loading sessions…</div>
        ) : error ? (
          <div className="empty" style={{ color: 'var(--oxblood)' }}>{error}</div>
        ) : sessions.length === 0 ? (
          <div className="empty"><b>No sessions yet</b>Name one above to get started.</div>
        ) : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Documents</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sessions.map(s => (
                  <tr key={s.id}>
                    <td><Link to={`/sessions/${s.id}`} className="strong">{s.name}</Link></td>
                    <td className="mono">{s.document_count ?? 0}</td>
                    <td className="mono">{new Date(s.created_at).toLocaleDateString()}</td>
                    <td className="r">
                      {confirmId === s.id ? (
                        <span className="row-actions">
                          <button className="btn danger solid sm" onClick={() => handleDelete(s.id)} disabled={deletingId === s.id}>
                            {deletingId === s.id ? 'Deleting…' : 'Confirm delete'}
                          </button>
                          <button className="btn outline sm" onClick={() => setConfirmId(null)}>Cancel</button>
                        </span>
                      ) : (
                        <span className="row-actions">
                          <Link to={`/sessions/${s.id}`} className="btn outline sm">Open</Link>
                          <button className="btn outline danger sm" onClick={() => handleDelete(s.id)}>Delete</button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
