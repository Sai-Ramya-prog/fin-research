interface Props { title: string; blurb: string; sample?: boolean; children?: React.ReactNode }

export function PageHead({ title, blurb, sample = true, children }: Props) {
  return (
    <div className="head">
      <div>
        <h1>{title}</h1>
        <p>{blurb}</p>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        {children}
        {sample && <span className="sample-chip" title="Illustrative numbers, built in a later milestone">Sample data</span>}
      </div>
    </div>
  )
}
