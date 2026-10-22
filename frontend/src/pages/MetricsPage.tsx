/** Metrics screen (sample data): KPI tiles with trend, plus ratios worked out in code. */
import { useState } from 'react'
import { PageHead } from '../components/PageHead'
import { Sparkline } from '../components/Sparkline'
import { COMPANIES, KPIS, RATIOS, type Company } from '../data/sample'

export function MetricsPage() {
  const [company, setCompany] = useState<Company>('Infosys')
  return (
    <div className="page">
      <PageHead
        title="Key metrics"
        blurb="Numbers read from the financial statements, each with the page it came from."
      >
        <div className="seg" role="group" aria-label="Company">
          {COMPANIES.map(c => (
            <button key={c} aria-pressed={company === c} onClick={() => setCompany(c)}>{c}</button>
          ))}
        </div>
      </PageHead>

      <div className="kpis">
        {KPIS[company].map(k => {
          const good = k.goodWhenUp ? k.delta >= 0 : k.delta <= 0
          return (
            <div className="card kpi" key={k.key}>
              <span className="label">{k.label}</span>
              <div>
                <span className="value">{k.value}</span>
                <span className="unit">{k.unit}</span>
              </div>
              <Sparkline data={k.series} color={good ? '#0e7c7b' : '#a61e3d'} />
              <div className="foot">
                <span className={`delta ${good ? 'good' : 'bad'}`}>
                  {k.delta >= 0 ? '▲' : '▼'} {Math.abs(k.delta).toFixed(1)}% YoY
                </span>
                <span className="src">{k.source}</span>
              </div>
            </div>
          )
        })}
      </div>

      <div className="card">
        <h2>Ratios worked out from the statements</h2>
        <p className="sub">Calculated in code from the extracted numbers, so every ratio can be traced back.</p>
        {RATIOS[company].map(r => (
          <div className="meter-row" key={r.label}>
            <div><b>{r.label}</b><small>{r.hint}</small></div>
            <div className="track"><div className="fill" style={{ width: `${Math.min(100, (r.value / r.max) * 100)}%` }} /></div>
            <div className="num">{r.value}{r.suffix}</div>
          </div>
        ))}
      </div>
      <p className="note">Illustrative numbers. Real extraction arrives with the Extraction agent.</p>
    </div>
  )
}
