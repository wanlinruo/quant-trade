const pad = (n: number) => String(n).padStart(2, '0')

export const hms = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`

const WEEK = '日一二三四五六'
export const dayLabel = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} 周${WEEK[d.getDay()]}`

/** 只按本地时间与星期判断时段；节假日要等交易日历接入（后续计划），此处不推断。 */
export function phaseOf(d: Date): string {
  const day = d.getDay()
  if (day === 0 || day === 6) return '周末休市'
  const s = d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds()
  if (s < 9 * 3600 + 15 * 60) return '盘前'
  if (s < 9 * 3600 + 30 * 60) return '集合竞价'
  if (s < 11 * 3600 + 30 * 60) return '盘中'
  if (s < 13 * 3600) return '午间休市'
  if (s < 15 * 3600) return '盘中'
  return '盘后'
}

/** 后端给的是不带时区的本地时间串；解析失败返回 null，不猜。 */
export function parseLocal(iso: string | null): Date | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}
