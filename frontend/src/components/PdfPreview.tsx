/** Full-screen preview of the report PDF (the browser's own PDF viewer). */
import { useEffect } from 'react'

interface Props { title: string; src: string; downloadUrl: string; onClose: () => void }

export function PdfPreview({ title, src, downloadUrl, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="pv" role="dialog" aria-modal="true" aria-label={`Preview of ${title}`} onClick={onClose}>
      <div className="pv-box" onClick={e => e.stopPropagation()}>
        <div className="pv-head">
          <b>{title}</b>
          <div className="report-actions">
            <a className="btn sm primary" href={downloadUrl} download>Download PDF</a>
            <button className="btn sm outline" onClick={onClose}>Close</button>
          </div>
        </div>
        <iframe className="pv-frame" src={src} title={`Preview of ${title}`} />
      </div>
    </div>
  )
}
