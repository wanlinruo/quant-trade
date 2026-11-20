// 交易组示意数据：确定性伪随机，不是真实账户、委托或持仓。
import { MINUTES, minuteTimes, rng, walk, type Status } from '../mock'
import { PM, BAD, GAP } from '../scene'

export const positions = [
  { code: '603xxx.SH', name: '示意股票 A', qty: 300, avail: 0, reserved: 0, cost: 15.82, last: 16.31, chg: 2.07 },
  { code: '002xxx.SZ', name: '示意股票 B', qty: 200, avail: 0, reserved: 0, cost: 19.6, last: 19.42, chg: -0.92 },
  // 晚间：q-1116-0004 收盘对账确认已成交，示意股票 C 已卖出
  ...(PM ? [] : [{ code: '601xxx.SH', name: '示意股票 C', qty: 100, avail: 100, reserved: 100, cost: 9.62, last: 9.85, chg: 0.51 }]),
]
if (PM) { positions[0].last = 16.44; positions[0].chg = 2.88; positions[1].last = 19.35; positions[1].chg = -1.27 }
export const EQUITY_BASE = 100000

/** 账户总资产分时（原始观测，不算收益） */
export function equityIntraday() {
  const v = walk(MINUTES, 21, EQUITY_BASE, 18, 0.03)
  if (PM) { const d = 100031.4 - v[v.length - 1]; v.forEach((_, i) => (v[i] += (d * i) / (v.length - 1))) } // 收盘与 KPI 一致
  const t = minuteTimes()
  // 异常日：隧道中断期间收不到券商快照，资产观测记缺失，不连线
  return v.map((x, i) => ({ time: t[i], value: GAP && i >= GAP[0] && i <= GAP[1] ? null : +x.toFixed(2) }))
}

/** 委托生命周期：每个环节的时间戳（秒，相对 09:25:00），null＝未记录 */
export type Stage = { name: string; t: number | null }
export const orders = [
  { id: 'q-1116-0001', at: '09:25:10', code: '603xxx.SH', side: '买', px: 15.82, qty: 300, filled: 300, status: 'ok' as Status, statusText: '全部成交', broker: 'J00981…',
    stages: [{ name: '意图', t: 10.0 }, { name: '风控通过', t: 10.02 }, { name: '网关 ACK', t: 10.4 }, { name: '券商受理', t: 11.1 }, { name: '成交', t: 31.6 }] as Stage[] },
  { id: 'q-1116-0002', at: '09:25:10', code: '002xxx.SZ', side: '买', px: 19.6, qty: 200, filled: 200, status: 'ok' as Status, statusText: '全部成交', broker: 'J00982…',
    stages: [{ name: '意图', t: 10.0 }, { name: '风控通过', t: 10.03 }, { name: '网关 ACK', t: 10.5 }, { name: '券商受理', t: null }, { name: '成交', t: 64.2 }] as Stage[] },
  { id: 'q-1116-0003', at: '09:31:02', code: '600xxx.SH', side: '买', px: 8.11, qty: 500, filled: 0, status: 'closed' as Status, statusText: '已撤', broker: 'J00987…',
    stages: [{ name: '意图', t: 362 }, { name: '风控通过', t: 362.02 }, { name: '网关 ACK', t: 362.5 }, { name: '券商受理', t: 363.4 }, { name: '撤单', t: 422 }] as Stage[] },
  PM
    // 晚间：15:30 收盘对账从券商快照确认已成交；券商受理时刻券商没有回报，仍记缺失，不按成交时刻反推
    ? { id: 'q-1116-0004', at: '10:05:44', code: '601xxx.SH', side: '卖', px: 9.85, qty: 100, filled: 100, status: 'ok' as Status, statusText: '全部成交（对账确认）', broker: 'J00993…',
      stages: [{ name: '意图', t: 2444 }, { name: '风控通过', t: 2444.02 }, { name: '网关 ACK', t: 2444.6 }, { name: '券商受理', t: null }, { name: '成交', t: 2451.3 }, { name: '对账确认', t: 21912 }] as Stage[] }
    : { id: 'q-1116-0004', at: '10:05:44', code: '601xxx.SH', side: '卖', px: 9.85, qty: 100, filled: 0, status: 'warn' as Status, statusText: '状态未知', broker: '—',
      stages: [{ name: '意图', t: 2444 }, { name: '风控通过', t: 2444.02 }, { name: '网关 ACK', t: 2444.6 }, { name: '券商受理', t: null }] as Stage[] },
]
export const rejects = [
  { at: '09:25:10', code: '920xxx.BJ', qty: 1000, rule: '北交所禁开仓', detail: '当前账户无北交所交易权限（#589）' },
  { at: '10:05:44', code: '688xxx.SH', qty: 800, rule: '单笔 ≤ 1 万', detail: '委托金额 1.86 万 > 1 万' },
]

/** 对账：3 层 × 30 个交易日 */
export const reconLayers = ['资金', '持仓', '委托/成交']
export function reconDays() {
  const out: string[] = []
  for (let d = new Date(PM ? '2026-11-16T00:00:00+08:00' : '2026-11-13T00:00:00+08:00'); out.length < 30; d = new Date(d.getTime() - 86400000)) {
    if (d.getDay() === 0 || d.getDay() === 6) continue
    out.unshift(`${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`)
  }
  return out
}
export function reconMatrix() {
  // 实盘 11-11 才启动：之前的交易日没有对账，显示“未对账”，不补
  return reconLayers.map((_, li) => reconDays().map((_, di) => {
    const o = PM ? 1 : 0 // 晚间多了今天一列，整体左移一格
    if (di < 27 - o) return { s: 'idle' as Status, n: 0 }
    if (li === 2 && di === 28 - o) return { s: 'bad' as Status, n: 1 }
    return { s: 'ok' as Status, n: 0 }
  }))
}
export const rules = [
  { rule: '1 万试运行额度', limit: '启用', used: '启用', s: 'ok' as Status },
  { rule: '单笔委托金额', limit: '≤ 1 万', used: '0.47 万（最大一笔）', s: 'ok' as Status },
  { rule: '单票持仓占比', limit: '≤ 5%', used: '4.9%', s: 'warn' as Status },
  { rule: '总敞口', limit: '≤ 10%', used: PM ? '8.8%' : '9.8%', s: (PM ? 'ok' : 'warn') as Status },
  { rule: '单日亏损', limit: '≤ 2%', used: PM ? '未亏损（收盘 +0.03%）' : null, s: (PM ? 'ok' : 'idle') as Status },
  { rule: '北交所新开仓', limit: '禁止', used: '今日拦截 1 次', s: 'ok' as Status },
  ...(BAD ? [{ rule: '停机闸', limit: '未触发才可开仓', used: '10:32:35 已触发 · 之后尚无新开仓意图', s: 'bad' as Status }] : []),
]
export const killLog = [
  ...(BAD ? [{ at: '11-16 10:32:35', act: '触发', who: 'watchdog', why: 'Redis 隧道心跳中断超过 90s（10:31:05 起）；10:36:40 隧道恢复，停机闸保持' }] : []),
  { at: '11-11 09:12:40', act: '清除', who: '维护者（受管入口）', why: '实盘激活 #137，三方核验通过' },
  { at: '11-10 15:41:05', act: '清除', who: '维护者（受管入口）', why: '链路恢复，对账一致' },
  { at: '11-10 14:03:12', act: '触发', who: 'watchdog', why: 'Windows 重启窗口，Redis 隧道断开' },
]

/** 运行记录：计划时点 vs 实际 */
export const runPoints = [
  { plan: '09:15:00', act: '09:15:01', phase: '开盘准备', s: 'ok' as Status, cand: null, intents: 0, blocked: 0, cost: 0.8 },
  { plan: '09:25:00', act: '09:25:10', phase: '竞价', s: 'ok' as Status, cand: 12, intents: 2, blocked: 1, cost: 2.1 },
  { plan: '09:31:00', act: '09:31:02', phase: '开盘确认', s: 'ok' as Status, cand: 3, intents: 1, blocked: 0, cost: 1.4 },
  { plan: '10:05:00', act: '10:05:44', phase: '盘中调仓', s: 'warn' as Status, cand: 5, intents: 1, blocked: 1, cost: 2.4 },
  ...(PM ? [
    { plan: '11:25:00', act: '11:25:01', phase: '午盘检查', s: 'ok' as Status, cand: 0, intents: 0, blocked: 0, cost: 0.9 },
    { plan: '14:25:00', act: '14:25:02', phase: '尾盘检查', s: 'ok' as Status, cand: 2, intents: 0, blocked: 0, cost: 1.1 },
    { plan: '14:50:00', act: '14:50:01', phase: '收盘前', s: 'ok' as Status, cand: 0, intents: 0, blocked: 0, cost: 0.7 },
  ] : [
    { plan: '11:25:00', act: null, phase: '午盘检查', s: 'idle' as Status, cand: null, intents: null, blocked: null, cost: null },
    { plan: '14:25:00', act: null, phase: '尾盘检查', s: 'idle' as Status, cand: null, intents: null, blocked: null, cost: null },
    { plan: '14:50:00', act: null, phase: '收盘前', s: 'idle' as Status, cand: null, intents: null, blocked: null, cost: null },
  ]),
]

/** 模拟账户 */
export const paperAccounts = [
  { id: 'pa-01', name: 'S6 主线（实盘档位）', strat: 'two_stage_limit_hold', ver: 'v7', s: (BAD ? 'bad' : 'running') as Status, ret: 3.42, dd: -4.1, n: 3, since: '2026-09-22', seed: 51 },
  { id: 'pa-02', name: 'S6 聚宽兼容', strat: 'two_stage_limit_hold', ver: 'v7', s: (BAD ? 'bad' : 'running') as Status, ret: 2.97, dd: -4.6, n: 3, since: '2026-09-22', seed: 52 },
  { id: 'pa-03', name: 'lightgbm_price 试验', strat: 'lightgbm_price', ver: 'v2', s: 'closed' as Status, ret: -1.25, dd: -6.8, n: 0, since: '2026-10-08', seed: 53 },
]
export function paperCurve(seed: number, n = 40) {
  const r = rng(seed)
  let v = 1, b = 1
  const days: string[] = []
  const nav: number[] = [], bt: number[] = []
  for (let d = new Date('2026-09-22T00:00:00+08:00'); days.length < n; d = new Date(d.getTime() + 86400000)) {
    if (d.getDay() === 0 || d.getDay() === 6) continue
    days.push(`${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`)
    const x = (r() - 0.46) * 0.012
    v *= 1 + x; b *= 1 + x + (r() - 0.5) * 0.002
    nav.push(+v.toFixed(4)); bt.push(+b.toFixed(4))
  }
  let peak = 0
  const dd = nav.map((x) => { peak = Math.max(peak, x); return +((x / peak - 1) * 100).toFixed(2) })
  return { days, nav, bt, dd }
}

/** 模拟账户自己的账本：与实盘同一套信号与风控，但按模拟撮合假设成交（不经过券商，不会有“状态未知”）。
 *  pa-01 按实盘档位撮合（限价、按 1 分钟成交量占比 ≤ 5%）；pa-02 按聚宽口径（下一根 1 分钟开盘价成交）。 */
export type PaperFill = { at: string; id: string; code: string; side: '买' | '卖'; qty: number; px: number; ref: number; bp: number; rule: string }
export function paperBook(accId: string) {
  if (accId === 'pa-03') return { positions: [], orders: [], fills: [] as PaperFill[], rejects: [] as typeof rejects }
  const jq = accId === 'pa-02'
  const adj = (x: number, bp: number) => +(x * (1 + bp / 10000)).toFixed(2)
  const fills: PaperFill[] = [
    { at: '09:25:10', id: `${accId}-0001`, code: '603xxx.SH', side: '买', qty: 300, ref: 15.82, px: jq ? adj(15.82, 6) : 15.82, bp: jq ? 6 : 0, rule: jq ? '下一根 1 分钟开盘价' : '竞价价成交（量占比 1.2%）' },
    { at: '09:25:10', id: `${accId}-0002`, code: '002xxx.SZ', side: '买', qty: 200, ref: 19.6, px: jq ? adj(19.6, 9) : 19.6, bp: jq ? 9 : 0, rule: jq ? '下一根 1 分钟开盘价' : '竞价价成交（量占比 0.8%）' },
    { at: '09:31:02', id: `${accId}-0003`, code: '600xxx.SH', side: '买', qty: 500, ref: 8.11, px: jq ? adj(8.11, 12) : 8.12, bp: jq ? 12 : 12, rule: jq ? '下一根 1 分钟开盘价（实盘同一意图未成交已撤）' : '限价内成交（量占比 3.1%；实盘同一意图未成交已撤）' },
    { at: '10:05:44', id: `${accId}-0004`, code: '601xxx.SH', side: '卖', qty: 100, ref: 9.85, px: jq ? adj(9.85, -5) : 9.85, bp: jq ? -5 : 0, rule: jq ? '下一根 1 分钟开盘价' : '限价内成交（量占比 0.2%）' },
  ]
  const pos = [
    { code: '603xxx.SH', qty: 300, avail: 0, cost: fills[0].px, last: positions[0].last },
    { code: '002xxx.SZ', qty: 200, avail: 0, cost: fills[1].px, last: positions[1].last },
    { code: '600xxx.SH', qty: 500, avail: 0, cost: fills[2].px, last: PM ? 8.29 : 8.2 },
  ]
  const ords = fills.map((f) => ({ at: f.at, id: f.id, code: f.code, side: f.side, px: f.ref, qty: f.qty, status: 'ok' as Status, statusText: '模拟成交' }))
  return { positions: pos, orders: ords, fills, rejects }
}
