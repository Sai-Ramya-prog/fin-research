/**
 * Document screen: the page map (what the Document Agent found), the source
 * page viewer (proof for a number) and the validation panel (how far to trust).
 */
import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { PageHead } from '../components/PageHead'
import { useLoad } from '../components/useLoad'
import {
  getDocDetail, getPageMap, getValidation, label, pageImageUrl, pretty, fmtAmount, SECTION_LABEL,
  type PageInfo,
} from '../api/analysis'

const sectionName = (s: string | null) => SECTION_LABEL[s ?? 'other'] ?? s ?? 'Other'

export function DocumentPage() {
  const { id = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const doc = useLoad(() => getDocDetail(id), [id])
  const map = useLoad(() => getPageMap(id), [id])
  const val = useLoad(() => getValidation(id), [id])

  const page = Number(params.get('page')) || null
  const highlight = params.get('q')
  const [imgState, setImgState] = useState<'loading' | 'ok' | 'error'>('loading')
  useEffect(() => { setImgState('loading') }, [id, page, highlight])

  const pages = map.data?.pages ?? []
  const current: PageInfo | undefined = pages.find(p => p.pdf_page === page)
  const open = (n: number, q?: string) => {
    const next = new URLSearchParams()
    next.set('page', String(n))
    if (q) next.set('q', q)
    setParams(next, { replace: true })
  }

  if (doc.error) return <div className="page"><div className="empty" style={{ color: 'var(--oxblood)' }}>{doc.error}</div></div>
  const d = doc.data
  const v = val.data

  return (
    <div className="page wide">
      <Link to="/" className="back" onClick={e => { e.preventDefault(); history.back() }}>← Back</Link>
      <PageHead
        title={d ? label(d) : 'Document'} accent={d?.fiscal_year ? `FY${d.fiscal_year}` : undefined}
        eyebrow="Document" sample={false}
        blurb={d?.filename ?? 'Loading…'}
      />

      {/* ── page map ─────────────────────────────────────────────── */}
      <section className="card">
        <h2>Page map</h2>
        <p className="sub">
          {map.data
            ? `${map.data.page_count} pages · ${map.data.tier1} important pages (tier 1) embedded for search · the rest (tier 2) stay in the database until a question needs them.`
            : 'Loading…'}
        </p>
        {map.error && <div className="empty" style={{ color: 'var(--oxblood)' }}>{map.error}</div>}
        <div className="pagemap" role="list">
          {pages.map(p => (
            <button
              key={p.pdf_page} role="listitem"
              className={`pm sec-${p.section ?? 'other'} ${p.tier === 1 ? 'tier1' : 'tier2'} ${page === p.pdf_page ? 'sel' : ''}`}
              title={`Page ${p.pdf_page}: ${sectionName(p.section)} · tier ${p.tier}${p.indexed ? ' · embedded' : ''}`}
              onClick={() => open(p.pdf_page)}
              aria-label={`Page ${p.pdf_page}, ${sectionName(p.section)}`}
            />
          ))}
        </div>
        <div className="legend">
          {map.data?.sections.map(s => (
            <span key={s.section}><i className={`pm sec-${s.section} tier1`} />{sectionName(s.section)} <b>{s.pages}</b></span>
          ))}
          <span className="legend-note"><i className="pm sec-other tier1" /> filled = tier 1 &nbsp; <i className="pm sec-other tier2" /> outlined = tier 2</span>
        </div>
      </section>

      {/* ── source page viewer ───────────────────────────────────── */}
      {page && (
        <section className="card" style={{ marginTop: 14 }}>
          <div className="viewer-head">
            <div>
              <h2>Source page {page}</h2>
              <p className="sub" style={{ margin: 0 }}>
                {current ? `${sectionName(current.section)} · tier ${current.tier}${current.printed_page ? ` · printed page ${current.printed_page}` : ''}` : ''}
              </p>
            </div>
            <span className="row-actions">
              <button className="btn outline sm" disabled={page <= 1} onClick={() => open(page - 1)}>← Prev</button>
              <button className="btn outline sm" disabled={!!map.data && page >= map.data.page_count} onClick={() => open(page + 1)}>Next →</button>
              <button className="btn outline sm" onClick={() => setParams({}, { replace: true })}>Close</button>
            </span>
          </div>
          {highlight && <blockquote className="quote" style={{ margin: '0 0 12px' }}>Looking for: {highlight}</blockquote>}
          <div className="pdf-frame">
            {imgState === 'loading' && <div className="empty">Rendering page…</div>}
            {imgState === 'error' && <div className="empty" style={{ color: 'var(--oxblood)' }}>Could not render this page. The PDF may have been removed from storage.</div>}
            <img
              key={`${page}-${highlight}`}
              src={pageImageUrl(id, page, highlight)} alt={`PDF page ${page}`}
              style={{ display: imgState === 'ok' ? 'block' : 'none' }}
              onLoad={() => setImgState('ok')} onError={() => setImgState('error')}
            />
          </div>
        </section>
      )}

      {/* ── validation panel ─────────────────────────────────────── */}
      <section className="card" style={{ marginTop: 14 }}>
        <h2>Validation</h2>
        <p className="sub">Every extracted number is checked in code before it is used.</p>
        {val.error && <div className="empty" style={{ color: 'var(--oxblood)' }}>{val.error}</div>}
        {v && (
          <>
            <div className="kpis" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
              <div className="card kpi"><span className="label">Verified</span><span className="value">{v.verified}<small>/{v.extracted_total}</small></span></div>
              <div className="card kpi"><span className="label">Need review</span><span className="value" style={{ color: v.needs_review ? 'var(--brass-ink)' : undefined }}>{v.needs_review}</span></div>
              <div className="card kpi"><span className="label">Ratios computed</span><span className="value">{v.ratios_verified}<small>/{v.ratios_total}</small></span></div>
              <div className="card kpi">
                <span className="label">Hand-checked match</span>
                <span className="value">{v.golden_total ? `${v.golden_matched}/${v.golden_total}` : '—'}</span>
              </div>
            </div>
            <div className="val-cols">
              <div>
                <h3 className="mini">Checks applied</h3>
                <ul className="checks">{v.checks.map(c => <li key={c}>{c}</li>)}</ul>
              </div>
              <div>
                <h3 className="mini">Statements found</h3>
                <ul className="checks">
                  {['profit_loss', 'balance_sheet', 'cash_flow'].map(s => {
                    const pg = v.statement_pages[s]
                    return (
                      <li key={s} className={pg ? '' : 'miss'}>
                        {sectionName(s)}:{' '}
                        {pg ? pg.slice(0, 6).map(n => (
                          <button key={n} className="src linkish" onClick={() => open(n)}>p.{n}</button>
                        )) : 'not found'}
                      </li>
                    )
                  })}
                </ul>
              </div>
            </div>
            {v.review_items.length > 0 && (
              <>
                <h3 className="mini">Needs review</h3>
                <table className="grid">
                  <thead><tr><th>Metric</th><th>Year</th><th className="r">Value</th><th>Page</th><th>Why</th></tr></thead>
                  <tbody>
                    {v.review_items.map((m, i) => (
                      <tr key={i}>
                        <td className="strong">{pretty(m.name)}</td>
                        <td className="mono">{m.year ?? '—'}</td>
                        <td className="r mono">{m.value != null ? fmtAmount(m.value, m.unit) : '—'}</td>
                        <td>{m.source_page ? <button className="src linkish" onClick={() => open(m.source_page!)}>p.{m.source_page}</button> : '—'}</td>
                        <td style={{ color: 'var(--muted)' }}>{m.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </>
        )}
      </section>
    </div>
  )
}
