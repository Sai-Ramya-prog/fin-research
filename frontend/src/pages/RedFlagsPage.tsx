/** Red flags screen: real findings from the Red Flag Agent, each with evidence and page. */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHead } from '../components/PageHead'
import { EmptyState } from '../components/EmptyState'
import { useLoad } from '../components/useLoad'
import { analysed, getFlags, label, listDocs, pretty, sourceLink, type Flag } from '../api/analysis'

type Severity = Flag['severity']
const LEVELS: Severity[] = ['High', 'Medium', 'Low']
const COLOUR: Record<Severity, string> = { High: 'var(--oxblood)', Medium: 'var(--brass)', Low: 'var(--info)' }

interface Row extends Flag { company: string; docId: string }

async function loadAll(): Promise<{ rows: Row[]; companies: number }> {
  const docs = analysed(await listDocs()).filter(d => d.red_flag_status === 'done')
  const lists = await Promise.all(docs.map(async d => (await getFlags(d.id)).map(f => ({ ...f, company: label(d), docId: d.id }))))
  return { rows: lists.flat(), companies: docs.length }
}

export function RedFlagsPage() {
  const { data, error, loading } = useLoad(loadAll)
  const [level, setLevel] = useState<Severity | 'All'>('All')
  const [company, setCompany] = useState<string>('All')

  const head = (
    <PageHead title="Red" accent="flags" eyebrow="Risk" sample={false}
      blurb="Warning signs found in the reports, each with the evidence and page that triggered it." />
  )
  if (!data || data.companies === 0) return <div className="page">{head}<EmptyState loading={loading} error={error} /></div>

  const names = Array.from(new Set(data.rows.map(r => r.company)))
  const shown = data.rows.filter(r => (level === 'All' || r.severity === level) && (company === 'All' || r.company === company))
  const count = (s: Severity) => data.rows.filter(r => r.severity === s).length
  const total = data.rows.length

  return (
    <div className="page">
      <PageHead title="Red" accent="flags" eyebrow="Risk" sample={false}
        blurb="Warning signs found in the reports, each with the evidence and page that triggered it.">
        <div className="seg" role="group" aria-label="Severity">
          {(['All', ...LEVELS] as const).map(l => (
            <button key={l} aria-pressed={level === l} onClick={() => setLevel(l)}>{l}</button>
          ))}
        </div>
      </PageHead>

      <div className="card">
        <h2>{total} {total === 1 ? 'flag' : 'flags'} across {data.companies} {data.companies === 1 ? 'company' : 'companies'}</h2>
        {total > 0 && (
          <div className="stack" role="img" aria-label="Flags by severity">
            {LEVELS.map(s => <i key={s} style={{ width: `${(count(s) / total) * 100}%`, background: COLOUR[s] }} />)}
          </div>
        )}
        <div className="legend">
          {LEVELS.map(s => <span key={s}><b>{count(s)}</b>{s}</span>)}
        </div>
        <div className="seg" role="group" aria-label="Company" style={{ marginTop: 14 }}>
          {['All', ...names].map(n => (
            <button key={n} aria-pressed={company === n} onClick={() => setCompany(n)}>{n}</button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="card" style={{ marginTop: 14 }}><div className="empty"><b>Nothing to show</b>No flags match this filter.</div></div>
      ) : (
        <div className="flags">
          {shown.map((f, i) => (
            <article className={`card flag sev-${f.severity}`} key={`${f.company}-${f.type}-${i}`}>
              <div className="flag-top">
                <span className="pill">{f.severity}</span>
                <span className="tag">{f.company}</span>
                <span className="tag">{pretty(f.type)}</span>
              </div>
              <h3>{f.title ?? pretty(f.type)}</h3>
              <p style={{ margin: 0, color: 'var(--muted)', fontSize: '0.9rem' }}>{f.description}</p>
              {f.evidence && <blockquote className="quote">{f.evidence}</blockquote>}
              {f.source_page != null && (
                <Link className="src linkish" style={{ alignSelf: 'flex-start' }} to={sourceLink(f.docId, f.source_page, f.evidence)} title="Open the source page">p.{f.source_page}</Link>
              )}
            </article>
          ))}
        </div>
      )}
      <p className="note">Flags come from fixed rules over verified numbers and from the auditor's report. Thresholds are in the Red Flag agent's rules file.</p>
    </div>
  )
}
