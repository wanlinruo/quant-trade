const pad = (n: number) => String(n).padStart(2, '0')

/** 市场时区。页面一律按它显示时刻，不依赖浏览器所在时区（契约时间都带时区）。 */
export const MARKET_TZ = 'Asia/Shanghai'
const FMT = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TZ, hourCycle: 'h23', weekday: 'short',
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
})
const WEEKDAY: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }

/** 某一时刻在市场时区下的日历与钟点。 */
export function marketParts(d: Date): { year: number; month: number; day: number; hour: number; minute: number; second: number; weekday: number } {
  const p = Object.fromEntries(FMT.formatToParts(d).map((x) => [x.type, x.value]))
  return { year: +p.year, month: +p.month, day: +p.day, hour: +p.hour, minute: +p.minute, second: +p.second, weekday: WEEKDAY[p.weekday] }
}

export const hms = (d: Date) => { const p = marketParts(d); return `${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}` }

/** ECharts 时间轴标签：按市场时区显示 HH:MM（ECharts 默认按浏览器时区格式化）。 */
export const axisHm = (v: number) => hms(new Date(v)).slice(0, 5)

const WEEK = '日一二三四五六'
export const dayLabel = (d: Date) => { const p = marketParts(d); return `${p.year}-${pad(p.month)}-${pad(p.day)} 周${WEEK[p.weekday]}` }

/** 按市场时区的钟点与星期判断时段；节假日要等交易日历接入（后续计划），此处不推断。 */
export function phaseOf(d: Date): string {
  const p = marketParts(d)
  if (p.weekday === 0 || p.weekday === 6) return '周末休市'
  const s = p.hour * 3600 + p.minute * 60 + p.second
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
