/**
 * Ask and report: pick a session, a company that is actually in it, and a fiscal year.
 * The report comes first (saved, or written section by section with live progress),
 * then a strict chat over that company's document.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHead } from '../components/PageHead'
import { ChatPanel } from '../components/ChatPanel'
import { ReportProgress, ReportView } from '../components/ReportView'
import { useLoad } from '../components/useLoad'
import { listSessions } from '../api/sessions'
import {
  generateReport, getCompanies, getReportState, reportPdfUrl,
  type ReportState,
} from '../api/ask'

const POLL_MS = 2000

export function AskPage() {
  const [params, setParams] = useSearchParams()
  const sessions = useLoad(listSessions)

  const withDocs = (sessions.data ?? []).filter(s => s.document_count > 0)
  const sessionId = params.get('session') ?? withDocs[0]?.id ?? ''
  const companies = useLoad(() => (sessionId ? getCompanies(sessionId) : Promise.resolve([])), [sessionId])

  const options = companies.data ?? []
  const company = options.find(c => c.company === params.get('company'))?.company ?? options[0]?.company ?? ''
  const years = options.find(c => c.company === company)?.years ?? []
  const yearOpt = years.find(y => String(y.fiscal_year) === params.get('year')) ?? years[0]
  const year = yearOpt?.fiscal_year ?? 0

  const update = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params)
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)))
    setParams(next, { replace: true })
  }

  // ── report: load, auto-generate the first time, poll while it is being written ──
  const [state, setState] = useState<ReportState | null>(null)
  const [reportError, setReportError] = useState<string | null>(null)
  const [poll, setPoll] = useState(0)

  useEffect(() => {
    setState(null); setReportError(null)
    if (!sessionId || !company || !year) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async (first: boolean) => {
      try {
        let st = await getReportState(sessionId, company, year)
        if (!alive) return
        if (first && st.readiness.ready && st.report === null) st = await generateReport(sessionId, company, year)
        if (!alive) return
        setState(st)
        if (st.report?.status === 'running') timer = setTimeout(() => void tick(false), POLL_MS)
      } catch (e) {
        if (alive) setReportError(e instanceof Error ? e.message : 'Could not load the report')
      }
    }
    void tick(true)
    return () => { alive = false; if (timer) clearTimeout(timer) }
  }, [sessionId, company, year, poll])

  async function regenerate() {
    if (!window.confirm('Write the report again? The current one will be replaced.')) return
    setReportError(null)
    try {
      setState(await generateReport(sessionId, company, year, true))
      setPoll(p => p + 1)
    } catch (e) {
      setReportError(e instanceof Error ? e.message : 'Could not start the report')
    }
  }

  const report = state?.report ?? null
  const finished = report?.status === 'ready' || report?.status === 'partial'
  const docId = yearOpt?.document_id ?? ''
  const sessionName = useMemo(() => sessions.data?.find(s => s.id === sessionId)?.name, [sessions.data, sessionId])

  const head = (
    <PageHead title="Ask and" accent="report" eyebrow="Research" sample={false}
      blurb="Read the report first, then ask anything about the company. Every answer cites its page." />
  )

  if (sessions.loading) return <div className="page">{head}<div className="card"><div className="empty">Loading…</div></div></div>
  if (sessions.error) return <div className="page">{head}<div className="card"><div className="empty" style={{ color: 'var(--oxblood)' }}>{sessions.error}</div></div></div>
  if (withDocs.length === 0) {
    return (
      <div className="page">{head}
        <div className="card"><div className="empty">
          <b>No reports uploaded yet</b>
          Create a session and upload an annual report. Once it is processed you can read its report and ask questions here.
          <div style={{ marginTop: 14 }}><Link to="/" className="btn primary">Go to sessions</Link></div>
        </div></div>
      </div>
    )
  }

  return (
    <div className="page">
      {head}

      <div className="card selectors">
        <label>
          <span>Session</span>
          <select value={sessionId} onChange={e => update({ session: e.target.value, company: '', year: '' })}>
            {withDocs.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label>
          <span>Company</span>
          <select value={company} disabled={options.length === 0}
            onChange={e => update({ company: e.target.value, year: '' })}>
            {options.map(c => <option key={c.company} value={c.company}>{c.company}</option>)}
          </select>
        </label>
        <label>
          <span>Fiscal year</span>
          <select value={year || ''} disabled={years.length === 0} onChange={e => update({ year: e.target.value })}>
            {years.map(y => <option key={y.fiscal_year} value={y.fiscal_year}>FY{y.fiscal_year}</option>)}
          </select>
        </label>
        <p className="sub">Only companies uploaded to {sessionName ? `“${sessionName}”` : 'this session'} are listed. Names are matched in upper case.</p>
      </div>

      {companies.error && <div className="card"><div className="empty" style={{ color: 'var(--oxblood)' }}>{companies.error}</div></div>}
      {!companies.loading && !companies.error && options.length === 0 && (
        <div className="card"><div className="empty">
          <b>No company names in this session</b>
          Uploads need a company name and fiscal year. Add them when uploading a report.
          <div style={{ marginTop: 14 }}><Link to={`/sessions/${sessionId}`} className="btn primary">Open the session</Link></div>
        </div></div>
      )}

      {yearOpt && !yearOpt.ready && (
        <div className="card"><div className="empty">
          <b>{company} FY{year} is not ready yet</b>
          {yearOpt.reason}
          <div style={{ marginTop: 14 }}><Link to={`/sessions/${sessionId}`} className="btn outline">Check progress in the session</Link></div>
        </div></div>
      )}

      {yearOpt?.ready && (<>
        <div className="card report-head">
          <div>
            <h2>{company} <span className="accent">FY{year}</span> report</h2>
            {report?.status === 'running' || !report
              ? <ReportProgress report={report} />
              : <p className="sub">
                  {report.status === 'failed'
                    ? 'The report could not be completed.'
                    : report.status === 'partial'
                      ? 'Some sections could not be written. Regenerate to try again.'
                      : 'Every statement below is tied to a page of the uploaded report.'}
                </p>}
          </div>
          <div className="report-actions">
            {finished
              ? <a className="btn primary" href={reportPdfUrl(sessionId, company, year)} download>Download PDF</a>
              : <button className="btn" disabled>Download PDF</button>}
            <button className="btn outline" onClick={regenerate} disabled={report?.status === 'running'}>Regenerate</button>
          </div>
        </div>

        {(reportError || report?.status === 'failed' || report?.status === 'partial') && (
          <div className="card chat-error" role="alert">{reportError ?? report?.error}</div>
        )}

        {report && report.sections.length > 0 && <ReportView report={report} docId={docId} />}

        <ChatPanel sessionId={sessionId} company={company} year={year} docId={docId} />
      </>)}
      <p className="note">Reports and answers are limited to the uploaded documents. Figures are copied from the statements and checked in code; they are not investment advice.</p>
    </div>
  )
}
