/** Live progress while the report is being written: real, saved section by section. */
import { SECTION_NAMES, type Report } from '../api/ask'

export function ReportProgress({ report }: { report: Report | null }) {
  const done = new Set((report?.sections ?? []).map(s => s.key))
  const n = done.size
  const total = SECTION_NAMES.length
  const pct = Math.round((n / total) * 100)
  return (
    <>
      <p className="sub">Writing the report: {n} of {total} parts done, then the PDF is put together.</p>
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
