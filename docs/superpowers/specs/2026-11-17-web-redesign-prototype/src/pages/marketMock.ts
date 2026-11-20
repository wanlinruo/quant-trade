// 行情组示意数据：确定性伪随机，股票代码打码，不是真实行情。
import { rng, walk } from '../mock'
import { PM, BAD } from '../scene'

export function tradingDays(n: number, end = '2026-11-13') {
  const out: string[] = []
  for (let d = new Date(`${end}T00:00:00+08:00`); out.length < n; d = new Date(d.getTime() - 86400000)) {
    if (d.getDay() === 0 || d.getDay() === 6) continue
    out.unshift(`${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`)
  }
  return out
}

/** 近 60 日情绪：涨停/跌停/炸板/上涨占比/沪深300 MA20 五日斜率 */
export function sentiment60() {
  const days = [...tradingDays(59), '11-16']
  const up = walk(60, 101, 55, 14, 0.02).map((x) => Math.max(18, Math.round(x)))
  const down = walk(60, 102, 8, 5).map((x) => Math.max(0, Math.round(Math.abs(x))))
  const brk = up.map((u, i) => Math.round(u * (0.18 + rng(200 + i)() * 0.15)))
  const adv = walk(60, 103, 52, 9).map((x) => Math.min(92, Math.max(8, +x.toFixed(1))))
  const slope = walk(60, 104, 0.1, 0.12).map((x) => +x.toFixed(3))
  if (PM) { up[59] = 71; down[59] = 6; brk[59] = 17; adv[59] = 58.4 } // 与收盘快照一致
  else { up[59] = 68; down[59] = 4; brk[59] = 14; adv[59] = 63.1 } // 与盘中快照一致
  return { days, up, down, brk, adv, slope }
}

/** 涨跌幅分布（今日）：合计＝有效 5,441；上涨（含涨停）÷ 有效＝上涨占比 */
export const dist = PM ? [
  { k: '跌停', n: 6 }, { k: '−7%以下', n: 52 }, { k: '−7~−5', n: 121 }, { k: '−5~−3', n: 352 }, { k: '−3~0', n: 1542 }, { k: '平', n: 190 },
  { k: '0~3', n: 1998 }, { k: '3~5', n: 640 }, { k: '5~7', n: 288 }, { k: '7%以上', n: 181 }, { k: '涨停', n: 71 },
] : [
  { k: '跌停', n: 4 }, { k: '−7%以下', n: 38 }, { k: '−7~−5', n: 96 }, { k: '−5~−3', n: 284 }, { k: '−3~0', n: 1374 }, { k: '平', n: 212 },
  { k: '0~3', n: 2186 }, { k: '3~5', n: 702 }, { k: '5~7', n: 311 }, { k: '7%以上', n: 166 }, { k: '涨停', n: 68 },
]

/** 分市场覆盖（请求/有效/缺失/无效/源时间） */
export const coverage = [
  { m: '沪市主板', expected: 1698, valid: 1698, missing: 0, invalid: 0, src: PM ? '15:00:02–15:00:05' : BAD ? '10:48:12–10:48:14' : '10:42:12–10:42:14' },
  { m: '深市主板', expected: 1502, valid: 1502, missing: 0, invalid: 0, src: PM ? '15:00:02–15:00:05' : BAD ? '10:48:12–10:48:13' : '10:42:12–10:42:13' },
  { m: '创业板', expected: 1388, valid: 1387, missing: 1, invalid: 0, src: PM ? '15:00:02–15:00:05' : BAD ? '10:48:12–10:48:13' : '10:42:12–10:42:13' },
  { m: '科创板', expected: 589, valid: 589, missing: 0, invalid: 0, src: PM ? '15:00:02–15:00:05' : BAD ? '10:48:12–10:48:14' : '10:42:12–10:42:14' },
  { m: '北交所', expected: 266, valid: 265, missing: 0, invalid: 1, src: PM ? '15:00:02–15:00:05' : BAD ? '10:48:11–10:48:14' : '10:42:11–10:42:14' },
]

/** 涨停幅度：创业板（30）与科创板（68）20%，其他 10%（示意按代码前缀；正式版只认 stk_limit 直取价） */
export const limPct = (code: string) => (code.startsWith('30') || code.startsWith('68') ? 19.95 : 9.95)

/** 打板梯队 */
export type Card = { code: string; name: string; h: number; seal: string | null; opens: number; status: 'sealed' | 'broken' | 'resealed'; pct: number; st?: boolean; bj?: boolean; theme: string; ai?: number; prev?: 'up' | 'stay' | 'fail'; diff?: string }
const THEMES = ['算力', '机器人', '固态电池', '低空经济', '医药', '有色']
export function ladder(): Card[] {
  const r = rng(321)
  const out: Card[] = []
  // 与市场总览一致。盘中 10:42：封 68（5/4/3/2/1 板 = 1/2/4/9/52）+ 炸板 14＝触板 82
  // 收盘：封 71（1/2/4/10/54）+ 炸板 17＝触板 88
  const counts: [number, number, number][] = PM ? [[5, 1, 0], [4, 2, 0], [3, 4, 0], [2, 10, 3], [1, 54, 14]] : [[5, 1, 0], [4, 2, 0], [3, 4, 0], [2, 9, 3], [1, 52, 11]]
  let k = 0
  counts.forEach(([h, n, nb]) => {
    for (let i = 0; i < n + nb; i++, k++) {
      const v = r()
      const status = i >= n ? 'broken' : v > 0.78 ? 'resealed' : 'sealed'
      const mm = Math.floor(Math.pow(r(), 1.6) * (PM ? 240 : 72)) // 盘中快照 10:42：只可能在 09:30–10:42 之间封板；收盘为全天
      const hh = mm < 120 ? 9 + Math.floor((30 + mm) / 60) : 13 + Math.floor((mm - 120) / 60)
      const mi = mm < 120 ? (30 + mm) % 60 : (mm - 120) % 60
      out.push({
        code: `${['60', '00', '30', '68'][k % 4]}${String(1000 + k * 37).slice(-4)}xx`, name: `示意${String.fromCharCode(65 + (k % 26))}${k}`,
        h, seal: k % 23 === 7 ? null : `${String(hh).padStart(2, '0')}:${String(mi).padStart(2, '0')}`, opens: status === 'sealed' ? 0 : 1 + Math.floor(r() * 3),
        status, pct: status === 'broken' ? +(4 + r() * 5).toFixed(2) : limPct(['60', '00', '30', '68'][k % 4]),
        st: k % 17 === 5, bj: k === 40, theme: THEMES[Math.floor(r() * THEMES.length)], ai: h === 1 && k % 6 === 0 ? +(0.5 + r() * 0.4).toFixed(2) : undefined,
      })
    }
  })
  if (PM) { const b = out.filter((c) => c.h === 1 && c.status === 'broken').at(-1); if (b) b.diff = '15:00 收盘实时快照记为封住；17:10 limit_list_d 定版记为炸板，以定版为准' }
  return out
}

/** 近 20 日晋级率（%）：1进2、2进3、3进4、4进5+ */
export function promotion() {
  const days = PM ? [...tradingDays(19), '11-16'] : tradingDays(20) // 晚间：今天的晋级结果已出
  return { days, rows: [
    { k: '1进2', d: walk(20, 401, 28, 8).map((x) => Math.max(5, Math.min(70, +x.toFixed(0)))) },
    { k: '2进3', d: walk(20, 402, 35, 12).map((x) => Math.max(0, Math.min(90, +x.toFixed(0)))) },
    { k: '3进4', d: walk(20, 403, 40, 18).map((x) => Math.max(0, Math.min(100, +x.toFixed(0)))) },
  ] }
}

/** 昨日（11-13）收盘仍封住的涨停：4/3/2/首板 = 1/3/7/43，合计 54（与今日运行“昨 54”一致） */
export const YEST_TIERS: Record<number, number> = { 4: 1, 3: 3, 2: 7, 1: 43 }
export const YEST_RATE: Record<number, number> = { 1: 26, 2: 50, 3: 33 } // 11-13 的 1进2 / 2进3 / 3进4（示意）
/** 昨日涨停今日表现：晋级＝今日在高一层封住；炸板＝今日触板未封；断板按今日涨跌分红盘 / 绿盘 / 跌停 */
export type FollowOut = 'up' | 'brk' | 'red' | 'green' | 'down'
export type Follow = { code: string; name: string; yh: number; out: FollowOut; pct: number; theme: string; card?: Card }
export function follow(cards: Card[]): Follow[] {
  const r = rng(654)
  const out: Follow[] = []
  let k = 0
  ;[4, 3, 2, 1].forEach((yh) => {
    const next = cards.filter((c) => c.h === yh + 1)
    next.filter((c) => c.status !== 'broken').forEach((c) => out.push({ code: c.code, name: c.name, yh, out: 'up', pct: c.pct, theme: c.theme, card: c }))
    next.filter((c) => c.status === 'broken').forEach((c) => out.push({ code: c.code, name: c.name, yh, out: 'brk', pct: c.pct, theme: c.theme, card: c }))
    const rest = YEST_TIERS[yh] - next.length
    for (let i = 0; i < rest; i++, k++) {
      const v = r()
      const cy = ['00', '60', '30'][k % 3]
      const lim = cy === '30' ? 19.98 : 9.98
      const o: FollowOut = v < (PM ? 0.1 : 0.06) ? 'down' : v < (PM ? 0.62 : 0.55) ? 'green' : 'red'
      const pct = o === 'down' ? -lim : o === 'green' ? -+(0.2 + r() * (yh > 1 ? 8.5 : 6.5)).toFixed(2) : +(0.1 + r() * 7.5).toFixed(2)
      out.push({ code: `${cy}${String(3000 + k * 61).slice(-4)}xx`, name: `示意${String.fromCharCode(97 + (k % 26))}${k}`, yh, out: o, pct, theme: THEMES[Math.floor(r() * THEMES.length)] })
    }
  })
  return out
}

/** 个股分时（示意，按涨跌幅%）：首封前爬升，封住后贴着涨停价；开板处下探再回封；炸板的收在当前涨幅。 */
export function stockPath(c: Card, n: number) {
  const r = rng(c.code.split('').reduce((a, ch) => a * 31 + ch.charCodeAt(0), 7) % 2147483647 || 1)
  const lim = limPct(c.code)
  const idx = (t: string) => { const [h, m] = t.split(':').map(Number); const x = h * 60 + m; return x >= 13 * 60 ? 121 + (x - 13 * 60) : x - (9 * 60 + 30) }
  const si = Math.min(n - 1, c.seal ? idx(c.seal) : Math.floor(n * 0.4))
  const open = +(r() * 4 - 0.5).toFixed(2)
  const y: number[] = []
  for (let i = 0; i <= si; i++) y.push(+(open + ((lim - open) * Math.pow(i / Math.max(1, si), 1.4)) + (i < si ? (r() - 0.5) * 0.8 : 0)).toFixed(2))
  const marks: { i: number; k: '首封' | '开板' | '回封' }[] = [{ i: si, k: '首封' }]
  let i = si + 1
  for (let o = 0; o < c.opens && i < n; o++) {
    const gap = 3 + Math.floor(r() * 25); for (let j = 0; j < gap && i < n; j++, i++) y.push(lim)
    if (i >= n) break
    marks.push({ i, k: '开板' })
    const last = c.status === 'broken' && o === c.opens - 1
    const len = last ? n - i : 3 + Math.floor(r() * 8)
    const depth = last ? lim - c.pct : 1 + r() * 3
    for (let j = 0; j < len && i < n; j++, i++) y.push(+(lim - depth * Math.min(1, (j + 1) / Math.max(2, len / 3)) + (r() - 0.5) * 0.4).toFixed(2))
    if (!last && i < n) marks.push({ i, k: '回封' })
  }
  while (y.length < n) y.push(lim)
  if (c.status === 'broken') y[n - 1] = c.pct
  return { y: y.slice(0, n), lim, marks: marks.filter((m) => m.i < n) }
}
