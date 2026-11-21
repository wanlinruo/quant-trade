/** 迷你走势：null 处断开，不连线、不补值；少于两个有效点不画。 */
export function Spark({ data, color, height = 22, width = 120, label }: {
  data: (number | null)[]; color: string; height?: number; width?: number; label?: string
}) {
  const values = data.filter((x): x is number => x != null && Number.isFinite(x))
  if (values.length < 2) return null
  const lo = Math.min(...values); const hi = Math.max(...values); const span = hi - lo || 1
  const step = data.length > 1 ? width / (data.length - 1) : 0
  const segments: string[] = []
  let current = ''
  data.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) { if (current) segments.push(current); current = ''; return }
    const x = (i * step).toFixed(1); const y = (height - 2 - (v - lo) / span * (height - 4)).toFixed(1)
    current += `${current ? 'L' : 'M'}${x},${y}`
  })
  if (current) segments.push(current)
  return (
    <svg role="img" aria-label={label ?? '近期走势'} viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" style={{ display: 'block' }}>
      {segments.map((d, i) => <path key={i} d={d} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />)}
    </svg>
  )
}
