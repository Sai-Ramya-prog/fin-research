/** Metrics screen: real numbers the Extraction Agent read from the statements. */
import { useEffect, useState } from 'react'
import { PageHead } from '../components/PageHead'
import { EmptyState } from '../components/EmptyState'
import { useLoad } from '../components/useLoad'
import {
  analysed, fmtAmount, getMetrics, label, latestYear, listDocs, pick, pretty,
  type DocItem, type Metric,
} from '../api/analysis'

const KPI_NAMES = ['revenue', 'net_profit', 'operating_cash_flow', 'cash', 'total_assets', 'eps']
const METERS: { name: string; hint: string; max: number; suffix: string }[] = [
  { name: 'gross_margin_pct', hint: 'gross profit / revenue', max: 100, suffix: '%' },
  { name: 'operating_margin_pct', hint: 'operating profit / revenue', max: 100, suffix: '%' },
  { name: 'net_margin_pct', hint: 'net profit / revenue', max: 100, suffix: '%' },
  { name: 'roe_pct', hint: 'net profit / total equity', max: 100, suffix: '%' },
  { name: 'debt_to_equity', hint: 'total debt / total equity', max: 3, suffix: 'x' },
  { name: 'current_ratio', hint: 'current assets / current liabilities', max: 3, suffix: 'x' },
  { name: 'ocf_to_net_profit', hint: 'operating cash flow / net profit', max: 2, suffix: 'x' },
]

const growth = (cur: number, prior: number) => (prior ? ((cur - prior) / Math.abs(prior)) * 100 : null)

export function MetricsPage() {
  const docs = useLoad(listDocs)
  const ready: DocItem[] = docs.data ? analysed(docs.data) : []
  const [docId, setDocId] = useState<string | null>(null)

  useEffect(() => { if (!docId && ready.length) setDocId(ready[0].id) }, [ready.length]) // eslint-disable-line
  const rows = useLoad<Metric[]>(() => (docId ? getMetrics(docId) : Promise.resolve([])), [docId])

  if (!ready.length) {
    return (
      <div className="page">
        <PageHead title="Key" accent="metrics" eyebrow="Extraction" sample={false}
          blurb="Numbers read from the financial statements, each with the page it came from." />
        <EmptyState loading={docs.loading} error={docs.error} />
      </div>
    )
  }

  const metrics = rows.data ?? []
  const year = latestYear(metrics)
  const doc = ready.find(d => d.id === docId)
  const unit = pick(metrics, 'revenue', year)?.unit ?? pick(metrics, 'total_assets', year)?.unit ?? ''
  const review = metrics.filter(m => m.status !== 'verified').length
  const table = metrics.filter(m => m.kind !== 'ratio')
  const names = Array.from(new Set(table.map(m => m.name)))

  return (
    <div className="page">
      <PageHead
        title="Key" accent="metrics" eyebrow="Extraction" sample={false}
        blurb="Numbers read from the financial statements, each with the page it came from."
      >
        <div className="seg" role="group" aria-label="Company">
          {ready.map(d => (
            <button key={d.id} aria-pressed={docId === d.id} onClick={() => setDocId(d.id)}>{label(d)}</button>
          ))}
        </div>
      </PageHead>

      {rows.loading ? <div className="card"><div className="empty">Loading…</div></div> : (
        <>
          <div className="kpis">
            {KPI_NAMES.map(name => {
              const cur = pick(metrics, name, year)
              if (!cur || cur.value === null) return null
              const prior = pick(metrics, name, year ? year - 1 : null)
              const delta = prior && prior.value !== null ? growth(cur.value, prior.value) : null
              const good = delta === null ? true : delta >= 0
              return (
                <div className="card kpi" key={name}>
                  <span className="label">{pretty(name)}</span>
                  <div>
                    <span className="value">{fmtAmount(cur.value, cur.unit)}</span>
                    <span className="unit">{cur.unit?.replace(/^.\s?in\s/, '') ?? ''}</span>
                  </div>
                  <div className="foot" style={{ marginTop: 'auto' }}>
                    {delta !== null
                      ? <span className={`delta ${good ? 'good' : 'bad'}`}>{delta >= 0 ? '▲' : '▼'} {Math.abs(delta).toFixed(1)}% YoY</span>
                      : <span />}
                    <span className="src" title={cur.source_text ?? undefined}>p.{cur.source_page ?? '?'}</span>
                  </div>
                  {cur.status !== 'verified' && (
                    <span className="badge busy" title={cur.note ?? undefined} style={{ alignSelf: 'flex-start' }}>needs review</span>
                  )}
                </div>
              )
            })}
          </div>

          <div className="card" style={{ marginBottom: 14 }}>
            <h2>Ratios worked out from the statements</h2>
            <p className="sub">Calculated in code from the extracted numbers, so every ratio can be traced back{year ? ` · ${doc ? label(doc) : ''} ${year}` : ''}.</p>
            {METERS.map(m => {
              const r = pick(metrics, m.name, year)
              if (!r || r.value === null) return null
              const pct = Math.max(0, Math.min(100, (r.value / m.max) * 100))
              return (
                <div className="meter-row" key={m.name} title={r.formula ?? undefined}>
                  <div><b>{pretty(m.name)}</b><small>{m.hint}</small></div>
                  <div className="track"><div className="fill" style={{ width: `${pct}%` }} /></div>
                  <div className="num">{r.value.toFixed(m.suffix === 'x' ? 2 : 1)}{m.suffix}</div>
                </div>
              )
            })}
          </div>

          <div className="card flush">
            <div className="card-bar">
              <h2>Every number, with its source</h2>
              <span className="count">{review ? `${review} need review` : 'all verified'}</span>
            </div>
            <div className="table-wrap">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th className="r">{year ?? 'Current'}</th>
                    <th className="r">{year ? year - 1 : 'Prior'}</th>
                    <th>Page</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {names.map(n => {
                    const cur = pick(table, n, year)
                    const pri = pick(table, n, year ? year - 1 : null)
                    const any = cur ?? pri
                    if (!any) return null
                    const bad = [cur, pri].find(x => x && x.status !== 'verified')
                    return (
                      <tr key={n}>
                        <td className="strong" title={any.source_text ?? undefined}>{pretty(n)}</td>
                        <td className="r mono">{cur?.value != null ? fmtAmount(cur.value, cur.unit) : '—'}</td>
                        <td className="r mono">{pri?.value != null ? fmtAmount(pri.value, pri.unit) : '—'}</td>
                        <td className="mono">{cur?.source_page ?? pri?.source_page ?? '—'}</td>
                        <td>
                          {bad
                            ? <span className="badge busy" title={bad.note ?? undefined}>needs review</span>
                            : <span className="badge ok">verified</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
          <p className="note">
            {unit ? `Amounts in ${unit}. ` : ''}Hover a metric name to see the exact line it was read from.
            “Verified” means the number was found on its source page and agrees with the other statements.
          </p>
        </>
      )}
    </div>
  )
}
