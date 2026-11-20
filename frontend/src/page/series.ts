export type Point = [string, number | null]

/** 按行序生成图表点；拿不到的值一律为 null（图表在此断开），绝不丢点或补值。 */
export function toSeries<T>(rows: T[], x: (r: T) => string, y: (r: T) => number | null | undefined): Point[] {
  return rows.map((r) => {
    const v = y(r)
    return [x(r), typeof v === 'number' && !Number.isNaN(v) ? v : null]
  })
}

/** 不画未来：去掉时间晚于 now 的点。 */
export function clipFuture(points: Point[], now: Date, toDate: (x: string) => Date): Point[] {
  return points.filter(([x]) => toDate(x).getTime() <= now.getTime())
}
