/** Tiny inline SVG trend line. No chart library. */
interface Props { data: number[]; color: string }

export function Sparkline({ data, color }: Props) {
  const w = 120, h = 38, pad = 3
  const min = Math.min(...data), max = Math.max(...data)
  const span = max - min || 1
  const pts = data.map((v, i) => {
    const x = pad + (i * (w - 2 * pad)) / (data.length - 1)
    const y = h - pad - ((v - min) / span) * (h - 2 * pad)
    return [x, y] as const
  })
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label="Five-year trend">
      <polyline points={line} fill="none" style={{ stroke: color }} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
