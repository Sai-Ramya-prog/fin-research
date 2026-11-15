/** Strict chat for the selected company: answers cite pages, and each citation opens the page. */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { sourceLink } from '../api/analysis'
import { askChat, clearChat, getChat, type ChatMessage } from '../api/ask'

const STARTERS = [
  'What is the revenue?',
  'What are the main risks management mentions?',
  'Where does the company operate?',
]

interface Props { sessionId: string; company: string; year: number; docId: string }

export function ChatPanel({ sessionId, company, year, docId }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<string | null>(null)   // the question being answered
  const [error, setError] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    setMessages([]); setError(null); setBusy(null)
    getChat(sessionId, company, year)
      .then(m => { if (alive) setMessages(m) })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : 'Could not load the chat') })
    return () => { alive = false }
  }, [sessionId, company, year])

  useEffect(() => { endRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [messages, busy])

  async function send(question: string) {
    const qn = question.trim()
    if (!qn || busy) return
    setError(null); setText(''); setBusy(qn)
    try {
      const answer = await askChat(sessionId, company, year, qn)
      // reload so the saved question and the answer come back in stored order
      setMessages(await getChat(sessionId, company, year).catch(() => [...messages, answer]))
    } catch (e) {
      setText(qn)
      setError(e instanceof Error ? e.message : 'The question failed')
    } finally {
      setBusy(null)
    }
  }

  async function clear() {
    if (!window.confirm(`Clear the chat for ${company} FY${year}?`)) return
    await clearChat(sessionId, company, year).catch(() => undefined)
    setMessages([])
  }

  return (
    <section className="card chat" aria-label="Chat">
      <div className="chat-head">
        <div>
          <h2>Ask about {company} <span className="accent">FY{year}</span></h2>
          <p className="sub">Answers come only from this company's uploaded report. If it is not in the document, you will be told.</p>
        </div>
        {messages.length > 0 && <button className="btn sm outline" onClick={clear}>Clear chat</button>}
      </div>

      <div className="chat-log" aria-live="polite">
        {messages.length === 0 && !busy && (
          <div className="starters">
            <span className="sub">Try:</span>
            {STARTERS.map(s => <button key={s} className="btn sm ghost" onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        {messages.map(m => m.role === 'user' ? (
          <div className="q" key={m.id}>{m.content}</div>
        ) : (
          <div className={`a kind-${m.kind}`} key={m.id}>
            {m.kind === 'unverified' && <div className="badge-warn">Unverified: the quotes could not be matched to the pages. Check the source before relying on this.</div>}
            {m.kind === 'not_found' && <div className="badge-muted">Not found in this document</div>}
            {m.kind === 'other_company' && <div className="badge-muted">Different company</div>}
            <p>{m.content}</p>
            {m.citations.length > 0 && (
              <div className="chips">
                {m.citations.map((c, i) => c.pdf_page != null && (
                  <Link key={i} className={`src linkish cite ${c.verified ? '' : 'unv'}`}
                    to={sourceLink(docId, c.pdf_page, c.quote ? c.quote.slice(0, 90) : null)}
                    title={c.quote ? `“${c.quote}”` : 'Open the source page'}>
                    p.{c.pdf_page}{c.verified ? ' ✓' : ''}
                  </Link>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && (<>
          <div className="q">{busy}</div>
          <div className="a thinking">Reading the report…</div>
        </>)}
        <div ref={endRef} />
      </div>

      {error && <div className="chat-error" role="alert">{error}</div>}
      <form className="ask-box" onSubmit={e => { e.preventDefault(); void send(text) }}>
        <input value={text} onChange={e => setText(e.target.value)} disabled={!!busy} maxLength={2000}
          placeholder={`Ask anything about ${company}'s report`} aria-label="Question" />
        <button className="btn" type="submit" disabled={!!busy || !text.trim()}>Ask</button>
      </form>
    </section>
  )
}
