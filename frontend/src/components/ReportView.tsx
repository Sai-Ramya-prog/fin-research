/** The report: live progress while it is written, then the five sections with page links. */
import { Link } from 'react-router-dom'
import { sourceLink } from '../api/analysis'
import { SECTION_NAMES, type Report, type ReportSection } from '../api/ask'

const short = (s: string | null, n = 90) => (s && s.length > n ? s.slice(0, n) : s)

function PageLink({ docId, page, quote }: { docId: string; page: number; quote?: string | null }) {
  return (
    <Link className="src linkish" to={sourceLink(docId, page, short(quote ?? null))} title="Open the source page">p.{page}</Link>
  )
}

function Section({ s, docId }: { s: ReportSection; docId: string }) {
  return (
    <section className="card report-sec" aria-label={s.title}>
      <h2>{s.title}</h2>
      {s.status !== 'ready' ? (
        <p className="sub">{s.note ?? 'This section is not available.'}</p>
      ) : (
        <>
          {s.sentences.map((x, i) => (
            <p className="prose" key={i}>
              {x.text} <PageLink docId={docId} page={x.page} quote={x.quote} />
              {x.quote && <span className="cite-quote">“{x.quote}”</span>}
            </p>
          ))}

          {s.tables.map(t => {
            const withPage = t.columns[t.columns.length - 1] === 'Change'
            return (
              <div key={t.title} className="rtable">
                <h3>{t.title}</h3>
                <div className="table-wrap">
                  <table className="cmp">
                    <thead>
                      <tr>{t.columns.map(c => <th key={c}>{c}</th>)}{withPage && <th>Page</th>}</tr>
                    </thead>
                    <tbody>
                      {t.rows.map((r, i) => (
                        <tr key={i} className={r.warn ? 'warn' : ''}>
                          {r.cells.map((c, j) => <td key={j}>{c}</td>)}
                          {withPage && <td>{r.page ? <PageLink docId={docId} page={r.page} /> : '—'}</td>}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })}

          {s.flags.length > 0 && (
            <div className="rflags">
              {s.flags.map((f, i) => (
                <article className={`rflag sev-${f.severity}`} key={i}>
                  <div className="flag-top">
                    <span className="pill">{f.severity}</span>
                    {f.page != null && <PageLink docId={docId} page={f.page} quote={f.evidence} />}
                  </div>
                  <h3>{f.title}</h3>
                  <p>{f.description}</p>
                  {f.evidence && <blockquote className="quote">{f.evidence}</blockquote>}
                </article>
              ))}
            </div>
          )}
          {s.note && <p className="note" style={{ marginTop: 10 }}>{s.note}</p>}
        </>
      )}
    </section>
  )
}

export function ReportProgress({ report }: { report: Report | null }) {
  const done = new Set((report?.sections ?? []).map(s => s.key))
  const n = done.size
  const total = SECTION_NAMES.length
  const pct = Math.round((n / total) * 100)
  return (
    <>
      <p className="sub">{n} of {total} sections ready</p>
      <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${pct}%` }} />
      </div>
      <ul className="steps">
        {SECTION_NAMES.map(([key, name]) => (
          <li key={key}>
            <span className={`tick ${done.has(key) ? 'on' : ''}`}>{done.has(key) ? '✓' : ''}</span>
            {name}
          </li>
        ))}
      </ul>
    </>
  )
}

export function ReportView({ report, docId }: { report: Report; docId: string }) {
  return (
    <div className="report">
      {report.sections.map(s => <Section key={s.key} s={s} docId={docId} />)}
    </div>
  )
}
