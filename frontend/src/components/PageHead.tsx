interface Props { title: string; accent?: string; eyebrow?: string; blurb: string; sample?: boolean; children?: React.ReactNode }

export function PageHead({ title, accent, eyebrow, blurb, sample = true, children }: Props) {
  return (
    <div className="head">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}{accent && <> <em>{accent}</em></>}</h1>
        <p>{blurb}</p>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        {children}
        {sample && <span className="sample-chip" title="Illustrative numbers, built in a later milestone">Sample data</span>}
      </div>
    </div>
  )
}
