/**
 * Evidence trail: a small map of the whole report under a chat answer, with the
 * pages the answer came from lit up. Each lit page opens that page in the
 * document screen, where the same pages are lit on the full page map.
 */
import { memo, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { SECTION_LABEL, sourceLink, type PageInfo } from '../api/analysis'
import type { Citation } from '../api/ask'

const sectionName = (s: string | null) => SECTION_LABEL[s ?? 'other'] ?? s ?? 'Other'

interface Props { docId: string; pages: PageInfo[]; citations: Citation[] }

function EvidenceTrailView({ docId, pages, citations }: Props) {
  const { cited, total, where } = useMemo(() => {
    // one entry per cited page; a page counts as verified if any citation on it was
    const byPage = new Map<number, { verified: boolean; quote: string | null }>()
    for (const c of citations) {
      if (c.pdf_page == null) continue
      const prev = byPage.get(c.pdf_page)
      byPage.set(c.pdf_page, {
        verified: (prev?.verified ?? false) || c.verified,
        quote: prev?.quote ?? c.quote,
      })
    }
    const known = new Set(pages.map(p => p.pdf_page))
    const hits = [...byPage.keys()].filter(n => known.has(n)).sort((a, b) => a - b)
    const sections = new Set(pages.filter(p => byPage.has(p.pdf_page)).map(p => sectionName(p.section)))
    return {
      cited: new Map(hits.map(n => [n, byPage.get(n)!])),
      total: pages.length,
      where: [...sections].join(', '),
    }
  }, [pages, citations])

  if (cited.size === 0) return null
  const allCited = [...cited.keys()]

  return (
    <div className="trail">
      <div className="trail-cap">
        <b>Evidence trail</b>
        <span>{cited.size} of {total} pages{where ? ` · ${where}` : ''}</span>
      </div>
      <div className="trail-map" role="list" aria-label={`Pages used for this answer: ${allCited.join(', ')}`}>
        {pages.map(p => {
          const hit = cited.get(p.pdf_page)
          const cls = `tp sec-${p.section ?? 'other'}`
          if (!hit) return <i key={p.pdf_page} className={cls} title={`Page ${p.pdf_page} · ${sectionName(p.section)}`} />
          return (
            <Link
              key={p.pdf_page} role="listitem"
              className={`${cls} hit ${hit.verified ? '' : 'unv'}`}
              to={sourceLink(docId, p.pdf_page, hit.quote ? hit.quote.slice(0, 90) : null, allCited)}
              title={`Page ${p.pdf_page} · ${sectionName(p.section)}${hit.quote ? ` · “${hit.quote.slice(0, 120)}”` : ''}`}
              aria-label={`Open page ${p.pdf_page}, ${sectionName(p.section)}`}
            />
          )
        })}
      </div>
    </div>
  )
}

// The chat re-renders on every keystroke; the trail only changes with its own props.
export const EvidenceTrail = memo(EvidenceTrailView)
