/** Ask and report screen (sample data): a research answer with sources, and a report builder. */
import { PageHead } from '../components/PageHead'
import { CHAT, REPORT_SECTIONS } from '../data/sample'

export function AskPage() {
  const done = REPORT_SECTIONS.filter(s => s.done).length
  const pct = Math.round((done / REPORT_SECTIONS.length) * 100)
  return (
    <div className="page">
      <PageHead
        title="Ask and" accent="report" eyebrow="Q&A"
        blurb="Ask why or how, get an answer that cites its pages, then turn the findings into a report."
      />
      <div className="split">
        <div className="card chat">
          <div className="q">{CHAT.question}</div>
          <div className="a">
            <p>{CHAT.answer}</p>
            <div className="chips">
              {CHAT.sources.map(s => <span className="src" key={s}>{s}</span>)}
            </div>
          </div>
          <div className="ask-box">
            <input disabled placeholder="Asking opens with the Research agent" aria-label="Question" />
            <button className="btn" disabled>Ask</button>
          </div>
        </div>

        <div className="card">
          <h2>Report</h2>
          <p className="sub">{done} of {REPORT_SECTIONS.length} sections ready</p>
          <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ width: `${pct}%` }} />
          </div>
          <ul className="steps">
            {REPORT_SECTIONS.map(s => (
              <li key={s.name}>
                <span className={`tick ${s.done ? 'on' : ''}`}>{s.done ? '✓' : ''}</span>
                {s.name}
              </li>
            ))}
          </ul>
          <button className="btn" disabled>Download report</button>
        </div>
      </div>
      <p className="note">Sample conversation. Answers will come from the pages indexed for each document.</p>
    </div>
  )
}
