/** Pill badge for Document status values. Colours come from theme tokens. */
import type { DocumentStatus } from '../api/sessions'

interface Props {
  status: DocumentStatus
  errorMessage?: string | null
}

const KIND: Record<DocumentStatus, string> = {
  uploaded: 'new',
  parsing: 'busy',
  tagging: 'busy',
  chunking: 'busy',
  indexing: 'busy',
  ready: 'ok',
  failed: 'bad',
}

export function StatusBadge({ status, errorMessage }: Props) {
  const hasErr = status === 'failed' && !!errorMessage
  return (
    <span
      className={`badge ${KIND[status] ?? ''}`}
      title={hasErr ? errorMessage! : undefined}
      style={hasErr ? { cursor: 'help' } : undefined}
    >
      {status}
      {hasErr && ' ⓘ'}
    </span>
  )
}
