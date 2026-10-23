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
      <td className="strong">{doc.filename}</td>
      <td>{doc.company}</td>
      <td className="mono">{doc.fiscal_year}</td>
      <td className="mono">{doc.page_count ?? '—'}</td>
      <td>
        <StatusBadge status={doc.status} errorMessage={doc.error_message} />
      </td>
      <td className="r">
        {confirmDelete ? (
          <span className="row-actions">
            <button className="btn danger solid sm" onClick={handleDelete} disabled={deleting}>
              {deleting ? 'Deleting…' : 'Confirm'}
            </button>
            <button className="btn outline sm" onClick={() => setConfirmDelete(false)} disabled={deleting}>
              Cancel
            </button>
          </span>
        ) : (
          <button className="btn outline danger sm" onClick={handleDelete}>Delete</button>
        )}
      </td>
    </tr>
  )
}
