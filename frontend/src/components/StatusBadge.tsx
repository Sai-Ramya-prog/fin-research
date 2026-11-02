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

const AGENT_KIND: Record<string, string> = {
  queued: 'new', running: 'busy', done: 'ok', failed: 'bad', skipped: '',
}

/** Small badge for the Extraction / Red Flag agent stages. */
export function AgentBadge({ label, status }: { label: string; status?: string | null }) {
  if (!status) return <span className="mono" style={{ color: 'var(--muted)' }}>—</span>
  return <span className={`badge ${AGENT_KIND[status] ?? ''}`} title={`${label}: ${status}`}>{label}</span>
}
