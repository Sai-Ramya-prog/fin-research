/** Compare screen: the same ratio for every analysed company. Ratios only, because
 *  reports are in different currencies and units. */
import { useState } from 'react'
import { PageHead } from '../components/PageHead'
import { EmptyState } from '../components/EmptyState'
import { useLoad } from '../components/useLoad'
import { analysed, getMetrics, label, latestYear, listDocs, pick, pretty } from '../api/analysis'

const METRICS: { name: string; unit: string; higher: boolean }[] = [
  { name: 'operating_margin_pct', unit: '%', higher: true },
  { name: 'net_margin_pct', unit: '%', higher: true },
  { name: 'roe_pct', unit: '%', higher: true },
  { name: 'revenue_growth_pct', unit: '%', higher: true },
  { name: 'net_profit_growth_pct', unit: '%', higher: true },
  { name: 'debt_to_equity', unit: 'x', higher: false },
  { name: 'current_ratio', unit: 'x', higher: true },
  { name: 'ocf_to_net_profit', unit: 'x', higher: true },
]

interface Col { company: string; year: number | null; values: Record<string, number | null> }

async function loadAll(): Promise<Col[]> {
  const docs = analysed(await listDocs())
  return Promise.all(docs.map(async d => {
    const rows = await getMetrics(d.id)
    const year = latestYear(rows)
    const values: Record<string, number | null> = {}
    for (const m of METRICS) values[m.name] = pick(rows, m.name, year)?.value ?? null
    return { company: label(d), year, values }
  }))
}

function winner(cols: Col[], name: string, higher: boolean): string | null {
  const have = cols.filter(c => c.values[name] !== null)
  if (have.length < 2) return null
  if (new Set(have.map(c => c.values[name])).size === 1) return null   // a tie has no winner
  return have.reduce((a, b) => ((higher ? b.values[name]! > a.values[name]! : b.values[name]! < a.values[name]!) ? b : a)).company
}

export function ComparePage() {
  const { data, error, loading } = useLoad(loadAll)
  const [key, setKey] = useState(METRICS[1].name)

  if (!data || data.length === 0) {
    return (
      <div className="page">
        <PageHead title="Compare" accent="companies" eyebrow="Comparison" sample={false}
          blurb="The same ratio for every company, side by side. The strongest value is highlighted." />
        <EmptyState loading={loading} error={error} />
      </div>
    )
  }

  const metric = METRICS.find(m => m.name === key) ?? METRICS[0]
  const win = winner(data, metric.name, metric.higher)
  const max = Math.max(...data.map(c => Math.abs(c.values[metric.name] ?? 0)), 0.0001)

  return (
    <div className="page">
      <PageHead title="Compare" accent="companies" eyebrow="Comparison" sample={false}
        blurb="The same ratio for every company, side by side. The strongest value is highlighted.">
        <div className="seg" role="group" aria-label="Metric">
          {METRICS.map(m => (
            <button key={m.name} aria-pressed={key === m.name} onClick={() => setKey(m.name)}>{pretty(m.name)}</button>
          ))}
        </div>
      </PageHead>

      <div className="card" style={{ marginBottom: 14 }}>
        <h2>{pretty(metric.name)}</h2>
        <p className="sub">{metric.unit === '%' ? 'percent' : 'times'} · {metric.higher ? 'higher is better' : 'lower is better'}</p>
        <div className="bars">
          {data.map(c => {
            const v = c.values[metric.name]
            return (
              <div className={`bar-row ${c.company === win ? 'best' : ''}`} key={c.company}>
                <span className="name">{c.company}</span>
                <div className="bar-track">
                  {v !== null && <div className="bar-fill" style={{ width: `${(Math.abs(v) / max) * 100}%` }} />}
                </div>
                <span className="num">
                  {v === null ? '—' : `${v.toFixed(metric.unit === 'x' ? 2 : 1)}${metric.unit}`}
                  {c.company === win && <span className="best-tag">best</span>}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      <div className="card">
        <h2>All ratios</h2>
        <p className="sub">Latest financial year of each report. Amounts are not compared because the reports use different currencies.</p>
        <div className="table-wrap">
          <table className="cmp">
            <thead>
              <tr><th>Ratio</th>{data.map(c => <th key={c.company}>{c.company}{c.year ? ` ${c.year}` : ''}</th>)}</tr>
            </thead>
            <tbody>
              {METRICS.map(m => {
                const w = winner(data, m.name, m.higher)
                return (
                  <tr key={m.name}>
                    <td>{pretty(m.name)} <span style={{ color: 'var(--muted)', fontWeight: 400 }}>({m.unit})</span></td>
                    {data.map(c => {
                      const v = c.values[m.name]
                      return <td key={c.company} className={c.company === w ? 'win' : ''}>{v === null ? '—' : v.toFixed(m.unit === 'x' ? 2 : 1)}</td>
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
