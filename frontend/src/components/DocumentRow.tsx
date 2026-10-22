/**
 * src/components/DocumentRow.tsx
 * Single row inside the session-detail document table.
 * Polls GET /documents/{id} every 2 s while the doc is still in-flight.
 * Stops polling once status is `ready` or `failed`.
 */

import { useEffect, useState } from 'react'
import { getDocument, deleteDocument, type Document } from '../api/sessions'
import { StatusBadge } from './StatusBadge'

const TERMINAL: Set<string> = new Set(['ready', 'failed'])
const POLL_MS = 2500

interface Props {
  initialDoc: Document
  onDelete: (id: string) => void
}

export function DocumentRow({ initialDoc, onDelete }: Props) {
  const [doc, setDoc] = useState<Document>(initialDoc)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // Start polling as long as the status is not terminal.
  useEffect(() => {
    if (TERMINAL.has(doc.status)) return

    const id = setInterval(async () => {
      try {
        const fresh = await getDocument(doc.id)
        setDoc(prev => ({ ...prev, ...fresh }))
        if (TERMINAL.has(fresh.status)) clearInterval(id)
      } catch {
        clearInterval(id)
      }
    }, POLL_MS)

    return () => clearInterval(id)
  }, [doc.id, doc.status])

  async function handleDelete() {
    if (!confirmDelete) { setConfirmDelete(true); return }
    setDeleting(true)
    try {
      await deleteDocument(doc.id)
      onDelete(doc.id)
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Delete failed')
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  return (
    <tr>
      <td style={td}>{doc.filename}</td>
      <td style={td}>{doc.company}</td>
      <td style={td}>{doc.fiscal_year}</td>
      <td style={td}>{doc.page_count ?? '—'}</td>
      <td style={td}>
        <StatusBadge status={doc.status} errorMessage={doc.error_message} />
      </td>
      <td style={{ ...td, textAlign: 'right' }}>
        {confirmDelete ? (
          <span style={{ display: 'inline-flex', gap: '6px' }}>
            <button
              onClick={handleDelete}
              disabled={deleting}
              style={{ ...btnBase, background: '#b91c1c', color: '#fff' }}
            >
              {deleting ? 'Deleting…' : 'Confirm'}
            </button>
            <button
              onClick={() => setConfirmDelete(false)}
              disabled={deleting}
              style={btnBase}
            >
              Cancel
            </button>
          </span>
        ) : (
          <button onClick={handleDelete} style={{ ...btnBase, color: '#b91c1c' }}>
            Delete
          </button>
        )}
      </td>
    </tr>
  )
}

const td: React.CSSProperties = {
  padding: '10px 12px',
  borderBottom: '1px solid #e5e7eb',
  fontSize: '0.875rem',
  color: '#374151',
  verticalAlign: 'middle',
}

const btnBase: React.CSSProperties = {
  padding: '4px 12px',
  border: '1px solid #d1d5db',
  borderRadius: '6px',
  background: '#fff',
  cursor: 'pointer',
  fontSize: '0.8rem',
}
