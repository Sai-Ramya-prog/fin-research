/** Compare screen (sample data): one metric as bars, all metrics in a table. */
import { useState } from 'react'
import { PageHead } from '../components/PageHead'
import { COMPANIES, COMPARE, type Company } from '../data/sample'

function best(values: Record<Company, number>, higher: boolean): Company {
  return COMPANIES.reduce((a, b) => ((higher ? values[b] > values[a] : values[b] < values[a]) ? b : a))
}

export function ComparePage() {
  const [key, setKey] = useState(COMPARE[1].key)
  const metric = COMPARE.find(m => m.key === key) ?? COMPARE[0]
  const winner = best(metric.values, metric.higherIsBetter)
  const max = Math.max(...COMPANIES.map(c => metric.values[c]))

  return (
    <div className="page">
      <PageHead
        title="Compare" accent="companies" eyebrow="Comparison"
        blurb="The same metric for every company, side by side. The strongest value is highlighted."
      >
        <div className="seg" role="group" aria-label="Metric">
          {COMPARE.map(m => (
            <button key={m.key} aria-pressed={key === m.key} onClick={() => setKey(m.key)}>{m.label}</button>
          ))}
        </div>
      </PageHead>

      <div className="card" style={{ marginBottom: 14 }}>
        <h2>{metric.label}</h2>
        <p className="sub">{metric.unit}{metric.higherIsBetter ? ' · higher is better' : ' · lower is better'}</p>
        <div className="bars">
          {COMPANIES.map(c => (
            <div className={`bar-row ${c === winner ? 'best' : ''}`} key={c}>
              <span className="name">{c}</span>
              <div className="bar-track"><div className="bar-fill" style={{ width: `${(metric.values[c] / max) * 100}%` }} /></div>
              <span className="num">{metric.values[c]}{c === winner && <span className="best-tag">best</span>}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2>All metrics</h2>
        <p className="sub">Consolidated figures, latest financial year.</p>
        <div className="table-wrap">
          <table className="cmp">
            <thead>
              <tr><th>Metric</th>{COMPANIES.map(c => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {COMPARE.map(m => {
                const w = best(m.values, m.higherIsBetter)
                return (
                  <tr key={m.key}>
                    <td>{m.label} <span style={{ color: 'var(--muted)', fontWeight: 400 }}>({m.unit})</span></td>
                    {COMPANIES.map(c => <td key={c} className={c === w ? 'win' : ''}>{m.values[c]}</td>)}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
      <p className="note">Illustrative numbers. The Comparison agent will fill this from the real reports.</p>
    </div>
  )
}
