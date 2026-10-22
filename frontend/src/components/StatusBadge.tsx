/**
 * src/components/StatusBadge.tsx
 * Pill badge for Document status values.
 */

import type { DocumentStatus } from '../api/sessions'

interface Props {
  status: DocumentStatus
  errorMessage?: string | null
}

const COLOURS: Record<DocumentStatus, { bg: string; fg: string }> = {
  uploaded:  { bg: '#e8f0fe', fg: '#1a56db' },
  parsing:   { bg: '#fef3c7', fg: '#92400e' },
  tagging:   { bg: '#fef3c7', fg: '#92400e' },
  chunking:  { bg: '#fef3c7', fg: '#92400e' },
  indexing:  { bg: '#fef3c7', fg: '#92400e' },
  ready:     { bg: '#d1fae5', fg: '#065f46' },
  failed:    { bg: '#fee2e2', fg: '#991b1b' },
}

export function StatusBadge({ status, errorMessage }: Props) {
  const { bg, fg } = COLOURS[status] ?? { bg: '#f3f4f6', fg: '#374151' }
  return (
    <span
      title={status === 'failed' && errorMessage ? errorMessage : undefined}
      style={{
        display: 'inline-block',
        padding: '2px 10px',
        borderRadius: 9999,
        fontSize: '0.75rem',
        fontWeight: 600,
        letterSpacing: '0.03em',
        background: bg,
        color: fg,
        cursor: status === 'failed' && errorMessage ? 'help' : 'default',
        textTransform: 'uppercase',
      }}
    >
      {status}
      {status === 'failed' && errorMessage && ' ⓘ'}
    </span>
  )
}
