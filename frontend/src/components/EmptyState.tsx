import { Link } from 'react-router-dom'

export function EmptyState({ loading, error }: { loading?: boolean; error?: string | null }) {
  if (loading) return <div className="card"><div className="empty">Loading…</div></div>
  if (error) return <div className="card"><div className="empty" style={{ color: 'var(--oxblood)' }}>{error}</div></div>
  return (
    <div className="card">
      <div className="empty">
        <b>No analysed reports yet</b>
        Upload a report in a session. Once its Extract badge turns green, its numbers appear here.
        <div style={{ marginTop: 14 }}><Link to="/" className="btn primary">Go to sessions</Link></div>
      </div>
    </div>
  )
}
