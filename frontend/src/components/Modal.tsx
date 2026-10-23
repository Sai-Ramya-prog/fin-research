import { useEffect } from 'react'

interface Props {
  title: string
  accent?: string
  subtitle?: string
  onClose: () => void
  children: React.ReactNode
}

/** Centered dialog. Closes on Esc or a click on the dark backdrop. */
export function Modal({ title, accent, subtitle, onClose, children }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={`${title} ${accent ?? ''}`.trim()}>
        <div className="modal-head">
          <div>
            <h2>{title} {accent && <em>{accent}</em>}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">×</button>
        </div>
        {children}
      </div>
    </div>
  )
}
