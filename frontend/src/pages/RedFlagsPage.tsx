/** Red flags screen (sample data): severity summary and evidence cards. */
import { useState } from 'react'
import { PageHead } from '../components/PageHead'
import { FLAGS, type Severity } from '../data/sample'

const LEVELS: Severity[] = ['High', 'Medium', 'Low']
const COLOUR: Record<Severity, string> = { High: '#a61e3d', Medium: '#b7791f', Low: '#5b7aa3' }

export function RedFlagsPage() {
  const [level, setLevel] = useState<Severity | 'All'>('All')
  const shown = level === 'All' ? FLAGS : FLAGS.filter(f => f.severity === level)
  const count = (s: Severity) => FLAGS.filter(f => f.severity === s).length

  return (
    <div className="page">
      <PageHead
        title="Red flags"
        blurb="Warning signs found in the reports, each with the sentence and page that triggered it."
      >
        <div className="seg" role="group" aria-label="Severity">
          {(['All', ...LEVELS] as const).map(l => (
            <button key={l} aria-pressed={level === l} onClick={() => setLevel(l)}>{l}</button>
          ))}
        </div>
      </PageHead>

      <div className="card">
        <h2>{FLAGS.length} flags across 4 companies</h2>
        <div className="stack" role="img" aria-label="Flags by severity">
          {LEVELS.map(s => <i key={s} style={{ width: `${(count(s) / FLAGS.length) * 100}%`, background: COLOUR[s] }} />)}
        </div>
        <div className="legend">
          {LEVELS.map(s => <span key={s}><b>{count(s)}</b>{s}</span>)}
        </div>
      </div>

      <div className="flags">
        {shown.map(f => (
          <article className={`card flag sev-${f.severity}`} key={f.id}>
            <div className="flag-top">
              <span className="pill">{f.severity}</span>
              <span className="tag">{f.company}</span>
              <span className="tag">{f.category}</span>
            </div>
            <h3>{f.title}</h3>
            <blockquote className="quote">{f.evidence}</blockquote>
            <span className="src" style={{ alignSelf: 'flex-start' }}>{f.source}</span>
          </article>
        ))}
      </div>
      <p className="note">Illustrative findings. The Red Flag agent will produce real ones with evidence.</p>
    </div>
  )
}
