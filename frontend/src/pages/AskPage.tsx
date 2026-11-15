/**
 * Ask and report: pick a session, a company that is actually in it, and a fiscal year.
 * "Generate report" writes the report (live progress); then it can be previewed or downloaded as a
 * PDF, and the strict chat over that company's document opens.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { PageHead } from '../components/PageHead'
import { ChatPanel } from '../components/ChatPanel'
import { PdfPreview } from '../components/PdfPreview'
import { ReportProgress } from '../components/ReportProgress'
import { useLoad } from '../components/useLoad'
import { listSessions } from '../api/sessions'
import {
  cancelReport, generateReport, getCompanies, getReportState, reportPdfUrl,
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

  // ── report: load what is saved, then poll while it is being written ──
  const [state, setState] = useState<ReportState | null>(null)
  const [reportError, setReportError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [preview, setPreview] = useState(false)
  const ready = !!(sessionId && company && year)

  useEffect(() => {
    setState(null); setReportError(null); setPreview(false)
    if (!ready) return
    let alive = true
    getReportState(sessionId, company, year)
      .then(st => { if (alive) setState(st) })
      .catch(e => { if (alive) setReportError(e instanceof Error ? e.message : 'Could not load the report') })
    return () => { alive = false }
  }, [sessionId, company, year, ready])

  const running = state?.report?.status === 'running'
  useEffect(() => {
    if (!running) return
    let alive = true
    const timer = setInterval(() => {
      getReportState(sessionId, company, year)
        .then(st => { if (alive) setState(st) })
        .catch(() => undefined)               // a missed poll is fine; the next one will update
    }, POLL_MS)
    return () => { alive = false; clearInterval(timer) }
  }, [running, sessionId, company, year])

  async function generate(regenerate: boolean) {
    if (regenerate && !window.confirm('Generate the report again? The current one will be replaced.')) return
    setReportError(null); setStarting(true)
    try {
      setState(await generateReport(sessionId, company, year, regenerate))
    } catch (e) {
      setReportError(e instanceof Error ? e.message : 'Could not start the report')
    } finally {
      setStarting(false)
    }
  }

  /** Changing session / company / year while a report is being written stops it, after asking. */
  function change(patch: Record<string, string>) {
    if (running) {
      const ok = window.confirm(
        'A report is being generated. If you change the selection, the generation will stop and the progress will be lost. Continue?')
      if (!ok) return
      void cancelReport(sessionId, company, year).catch(() => undefined)
    }
    update(patch)
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
          <select value={sessionId} onChange={e => change({ session: e.target.value, company: '', year: '' })}>
            {withDocs.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label>
          <span>Company</span>
          <select value={company} disabled={options.length === 0}
            onChange={e => change({ company: e.target.value, year: '' })}>
            {options.map(c => <option key={c.company} value={c.company}>{c.company}</option>)}
          </select>
        </label>
        <label>
          <span>Fiscal year</span>
          <select value={year || ''} disabled={years.length === 0} onChange={e => change({ year: e.target.value })}>
            {years.map(y => <option key={y.fiscal_year} value={y.fiscal_year}>FY{y.fiscal_year}</option>)}
          </select>
        </label>
        <p className="sub">Only companies uploaded to {sessionName ? `“${sessionName}”` : 'this session'} are listed. Names are matched in upper case.</p>
        <div className="gen-slot">
          <button className="btn primary" onClick={() => generate(finished)}
            disabled={!yearOpt?.ready || !state || running || starting}>
            {running || starting ? 'Generating…' : finished ? 'Regenerate report' : 'Generate report'}
          </button>
        </div>
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
            {!state && !reportError && <p className="sub">Checking for a saved report…</p>}
            {state && !report && (
              <p className="sub">
                Click <b>Generate report</b> above to create a PDF research report for {company} FY{year}: company
                snapshot, key metrics and ratios, peer comparison, red flags and outlook. When it is ready you can
                preview it and ask questions about it.
              </p>
            )}
            {running && <ReportProgress report={report} />}
            {report?.status === 'ready' && <p className="sub">The report is ready. Preview it here or download the PDF, then ask your questions below.</p>}
            {report?.status === 'partial' && <p className="sub">The report is ready, but some parts could not be written.</p>}
            {report?.status === 'failed' && <p className="sub">The report could not be generated. Click <b>Generate report</b> to try again.</p>}
          </div>
          <div className="report-actions">
            {finished && <button className="btn primary" onClick={() => setPreview(true)}>Preview</button>}
            {finished && <a className="btn outline" href={reportPdfUrl(sessionId, company, year)} download>Download PDF</a>}
          </div>
        </div>

        {(reportError || report?.status === 'failed' || report?.status === 'partial') && (
          <div className="card chat-error" role="alert">{reportError ?? report?.error}</div>
        )}

        {finished
          ? <ChatPanel sessionId={sessionId} company={company} year={year} docId={docId} />
          : (
            <section className="card chat locked" aria-label="Chat (locked)">
              <h2>Ask about {company} <span className="accent">FY{year}</span></h2>
              <p className="sub">The chat opens once the report has been generated.</p>
            </section>
          )}

        {preview && (
          <PdfPreview title={`${company} FY${year} report`} src={reportPdfUrl(sessionId, company, year, true)}
            downloadUrl={reportPdfUrl(sessionId, company, year)} onClose={() => setPreview(false)} />
        )}
      </>)}
      <p className="note">Reports and answers are limited to the uploaded documents. Figures are copied from the statements and checked in code; they are not investment advice.</p>
    </div>
  )
}
