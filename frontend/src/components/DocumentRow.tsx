/**
 * src/components/DocumentRow.tsx
 * Single row inside the session-detail document table.
 * Polls GET /documents/{id} every 2 s while the doc is still in-flight.
 * Stops polling once status is `ready` or `failed`.
 */

import { useEffect, useState } from 'react'
import { getDocument, deleteDocument, type Document } from '../api/sessions'
import { StatusBadge, AgentBadge } from './StatusBadge'

const ACTIVE_AGENT: Set<string> = new Set(['queued', 'running'])

/** Keep polling while the document is processing OR an agent is still working. */
function isSettled(d: Document): boolean {
  if (d.status === 'failed') return true
  if (d.status !== 'ready') return false
  return !ACTIVE_AGENT.has(d.extraction_status ?? '') && !ACTIVE_AGENT.has(d.red_flag_status ?? '')
}
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
    if (isSettled(doc)) return

    const id = setInterval(async () => {
      try {
        const fresh = await getDocument(doc.id)
        setDoc(prev => ({ ...prev, ...fresh }))
        if (isSettled({ ...doc, ...fresh })) clearInterval(id)
      } catch {
        clearInterval(id)
      }
    }, POLL_MS)

    return () => clearInterval(id)
  }, [doc.id, doc.status, doc.extraction_status, doc.red_flag_status])

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
      <td>
        <span className="row-actions">
          <AgentBadge label="Extract" status={doc.extraction_status} />
          <AgentBadge label="Flags" status={doc.red_flag_status} />
        </span>
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
