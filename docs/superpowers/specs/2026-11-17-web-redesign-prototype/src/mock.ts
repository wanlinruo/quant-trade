// 全部是示意数据（确定性伪随机），不是真实行情、账户或运行记录。
import { PM, BAD, GAP } from './scene'
export function rng(seed: number) {
  let s = seed
  return () => {
    s = (s * 16807) % 2147483647
    return (s - 1) / 2147483646
  }
}
export function walk(n: number, seed: number, start: number, vol: number, drift = 0) {
  const r = rng(seed)
  let v = start
  return Array.from({ length: n }, () => (v += (r() - 0.5 + drift) * vol))
}

export type Status = 'ok' | 'warn' | 'bad' | 'idle' | 'running' | 'closed'
export const statusText: Record<Status, string> = {
  ok: '正常', warn: '警告', bad: '失败', idle: '待运行', running: '运行中', closed: '休市',
}
export const statusIcon: Record<Status, string> = {
  ok: '●', warn: '▲', bad: '✕', idle: '○', running: '◐', closed: '—',
}

/* ---------- 今日运行 ---------- */
export const events = [
  { at: '09:15', name: '开盘准备', status: 'ok' as Status, done: '09:15:01', cost: '0.8s', note: '盘前检查 16 / 0' },
  { at: '09:25', name: '竞价', status: 'ok' as Status, done: '09:25:10', cost: '2.1s', note: '候选 12 → 入档 3 → 闸门 0' },
  { at: '09:31', name: '开盘确认', status: 'ok' as Status, done: '09:31:02', cost: '1.4s', note: '候选 3 → 意图 1' },
  { at: '10:05', name: '盘中调仓', status: 'warn' as Status, done: '10:05:44', cost: '2.4s', note: '迟到 44s（阈值 30s）· 意图 1' },
  ...(PM ? [
    { at: '11:25', name: '午盘检查', status: 'ok' as Status, done: '11:25:01', cost: '0.9s', note: '持仓 2 · 无新意图' },
    { at: '14:25', name: '尾盘检查', status: 'ok' as Status, done: '14:25:02', cost: '1.1s', note: '无新意图' },
    { at: '14:50', name: '收盘前', status: 'ok' as Status, done: '14:50:01', cost: '0.7s', note: '无未结委托需撤' },
    { at: '15:00', name: '收盘', status: 'ok' as Status, done: '15:00:04', cost: '0.5s', note: '收盘快照已记录' },
    { at: '15:30', name: '收盘对账', status: 'ok' as Status, done: '15:30:12', cost: '6.8s', note: '三层一致 · q-1116-0004 确认已成交' },
  ] : [
    { at: '11:25', name: '午盘检查', status: 'idle' as Status, done: '', cost: '', note: '' },
    { at: '14:25', name: '尾盘检查', status: 'idle' as Status, done: '', cost: '', note: '' },
    { at: '14:50', name: '收盘前', status: 'idle' as Status, done: '', cost: '', note: '' },
    { at: '15:00', name: '收盘', status: 'idle' as Status, done: '', cost: '', note: '' },
    { at: '15:30', name: '收盘对账', status: 'idle' as Status, done: '', cost: '', note: '' },
  ]),
]

export const phases = [
  { title: '盘前', time: '08:30–09:15', status: 'ok' as Status },
  { title: '竞价', time: '09:15–09:25', status: 'ok' as Status },
  { title: '盘中', time: '09:30–15:00', status: (PM ? 'warn' : BAD ? 'bad' : 'running') as Status },
  { title: '收盘', time: '15:00–15:40', status: (PM ? 'ok' : 'idle') as Status },
  { title: '晚间任务', time: '15:40–21:00', status: (PM ? 'running' : 'idle') as Status },
]

/** 链路状态时间轴：小时为单位的区间 */
const E = PM ? 15.5 : BAD ? 10.806 : 10.7
export const links: { name: string; segs: [number, number, Status, string?][] }[] = [
  { name: 'QMT 客户端', segs: [[9, E, 'ok']] },
  { name: '执行器', segs: [[9, E, 'ok']] },
  { name: '行情网关', segs: [[9, 9.42, 'ok'], [9.42, 9.46, 'warn', '行情延迟 38s'], [9.46, E, 'ok']] },
  { name: 'Redis 隧道', segs: BAD ? [[9, 10.518, 'ok'], [10.518, 10.611, 'bad', '10:31:05–10:36:40 隧道中断 5 分 35 秒'], [10.611, 10.806, 'ok']] : [[9, E, 'ok']] },
  { name: 'live-core', segs: BAD ? [[9, 10.518, 'ok'], [10.518, 10.543, 'warn', '心跳丢失，等待 90s 阈值'], [10.543, 10.806, 'bad', '10:32:35 停机闸触发：只允许减仓与撤单']] : [[9, E, 'ok']] },
  { name: 'watchdog', segs: PM ? [[9, 10.1, 'ok'], [10.1, 11.5, 'warn', '巡检间隔 40s > 30s（11:30 午休后恢复）'], [11.5, E, 'ok']] : [[9, 10.1, 'ok'], [10.1, 10.7, 'warn', '巡检间隔 40s > 30s']] },
]

/** 当日分钟时间戳（秒）：盘中场景到 10:42（73 根），晚间场景为全天 241 根，跳过午休。 */
export const MINUTES = PM ? 241 : BAD ? 79 : 73
export function minuteTimes(n = MINUTES) {
  const a = new Date('2026-11-16T09:30:00+08:00').getTime() / 1000
  const b = new Date('2026-11-16T13:01:00+08:00').getTime() / 1000
  return Array.from({ length: n }, (_, i) => (i <= 120 ? a + i * 60 : b + (i - 121) * 60))
}
/** 分时：09:30 起 1 分钟 */
export function intraday() {
  const n = MINUTES
  const px = walk(n, 7, 3420, 3.2, 0.06)
  if (PM) { const d = 3452.18 - px[n - 1]; px.forEach((_, i) => (px[i] += (d * i) / (n - 1))) } // 收盘与 KPI 一致
  const vol = walk(n, 11, 50, 18).map((x) => Math.abs(x) + 12)
  const t = minuteTimes(n)
  // 异常日：隧道中断期间没有行情快照，记缺失（null），图上留空，不连线、不补
  return px.map((p, i) => ({ time: t[i], value: GAP && i >= GAP[0] && i <= GAP[1] ? null : +p.toFixed(2), vol: GAP && i >= GAP[0] && i <= GAP[1] ? null : Math.round(vol[i] * 1e6) }))
}

/* ---------- 服务看板 ---------- */
export const services = [
  { id: 'daily-archive', name: 'daily-archive', at: '15:40', ver: 'e6966ba', avg: 840 },
  { id: 'tushare-sync', name: 'tushare-sync', at: '17:00', ver: '11234d8', avg: 372 },
  { id: 'premarket-limits', name: 'premarket-limits', at: '08:58', ver: 'e6966ba', avg: 41 },
  { id: 'limit-up-shadow', name: 'limit-up-shadow', at: '19:35', ver: 'e1d0fc1', avg: 48 },
  { id: 'catalog-verify', name: 'catalog-verify', at: '19:40', ver: 'f7590c0', avg: 543 },
  { id: 'limit-up-board', name: 'limit-up-board', at: '盘中', ver: 'e1d0fc1', avg: 15 },
  { id: 'service-checks', name: 'service-checks', at: '20:00', ver: 'e1d0fc1', avg: 80 },
  { id: 'daily-digest', name: 'daily-digest', at: '20:30', ver: 'e1d0fc1', avg: 12 },
]

/** 近 30 个自然日，含周末 */
export function serviceDays() {
  const end = new Date('2026-11-16T00:00:00+08:00')
  return Array.from({ length: 30 }, (_, i) => {
    const d = new Date(end.getTime() - (29 - i) * 86400000)
    const wk = d.getDay() === 0 || d.getDay() === 6
    return { label: `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`, weekend: wk }
  })
}

export function serviceMatrix() {
  const days = serviceDays()
  const r = rng(9)
  return services.map((s) =>
    days.map((d, di) => {
      if (d.weekend) return 'closed' as Status
      if (di === 29 && PM) return (s.id === 'catalog-verify' ? 'warn' : 'ok') as Status // 20:40：今天全部跑完，catalog-verify 仍是已知上游警告
      if (di === 29) return (s.id === 'premarket-limits' ? 'ok' : s.id === 'limit-up-board' ? 'running' : 'idle') as Status // 10:42：只有盘前任务跑过，盘中采集进行中，晚间任务未到
      const v = r()
      if (s.id === 'catalog-verify') return (di > 24 ? 'warn' : v > 0.4 ? 'warn' : 'ok') as Status
      if (s.id === 'tushare-sync' && di === 24) return 'bad' as Status
      if (s.id === 'limit-up-shadow' && di === 24) return 'bad' as Status
      return (v > 0.97 ? 'bad' : v > 0.9 ? 'warn' : 'ok') as Status
    }),
  )
}

export const gantt = [
  { name: 'daily-archive', start: 15.67, end: 15.9, status: 'ok' as Status },
  { name: 'tushare-sync', start: 17.0, end: 17.1, status: 'ok' as Status },
  { name: 'limit-up-shadow', start: 19.58, end: 19.6, status: 'ok' as Status },
  { name: 'catalog-verify', start: 19.67, end: 19.82, status: 'warn' as Status },
  { name: 'service-checks', start: 20.0, end: 20.03, status: 'ok' as Status },
  { name: 'daily-digest', start: 20.5, end: 20.52, status: 'ok' as Status },
]

/* ---------- 回测 ---------- */
/** 回测清单：每次回测一份确定的结果；策略页与回测页都从这里取数，保证同一份回测数字一致 */
export type BtCfg = { id: string; strat: string; label: string; seed: number; start: string; n: number; target?: number; sharpe: number; done: string; warn?: string }
export const BTS: BtCfg[] = [
  { id: 's6-2024-2026', strat: 's6', label: '首板竞价 S6 · 全区间', seed: 17, start: '2024-01-02', n: 690, sharpe: 1.21, done: '11-02' },
  { id: 's6-no-st', strat: 's6', label: '首板竞价 S6（去 ST）', seed: 23, start: '2024-01-02', n: 690, target: 0.968, sharpe: 1.08, done: '11-01' },
  { id: 's6-2025h2', strat: 's6', label: '首板竞价 S6 · 2025 下半年', seed: 29, start: '2025-07-01', n: 125, target: 0.127, sharpe: 0.94, done: '10-30' },
  { id: 's6-stress-2024q1', strat: 's6', label: '首板竞价 S6 · 2024Q1 压力区间', seed: 31, start: '2024-01-02', n: 60, target: -0.083, sharpe: -0.62, done: '11-12', warn: '区间内 3 个交易日分钟数据缺口，相关信号按缺失跳过（未填充）' },
  { id: 'lgb-2025', strat: 'lgb', label: 'lightgbm_price · 2025 全年', seed: 37, start: '2025-01-02', n: 242, target: 0.061, sharpe: 0.41, done: '10-08' },
  { id: 'nxo-2025', strat: 'nxo', label: 'next_open · 2025 全年', seed: 41, start: '2025-01-02', n: 242, target: -0.034, sharpe: -0.2, done: '09-30' },
]
export function backtest(cfg: BtCfg = BTS[0]) {
  const start = new Date(`${cfg.start}T00:00:00+08:00`)
  const days: string[] = []
  for (let d = new Date(start); days.length < cfg.n; d = new Date(d.getTime() + 86400000)) {
    if (d.getDay() === 0 || d.getDay() === 6) continue
    days.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`)
  }
  const r = rng(cfg.seed)
  let eq = 1
  let bm = 1
  const equity: number[] = []
  const bench: number[] = []
  days.forEach(() => {
    eq *= 1 + (r() - 0.47) * 0.026
    bm *= 1 + (r() - 0.49) * 0.018
    equity.push(+eq.toFixed(4))
    bench.push(+bm.toFixed(4))
  })
  if (cfg.target != null) { const f = Math.pow((1 + cfg.target) / equity[equity.length - 1], 1 / equity.length); equity.forEach((v, i) => (equity[i] = +(v * Math.pow(f, i + 1)).toFixed(4))) }
  let peak = -1
  const dd = equity.map((v) => ((peak = Math.max(peak, v)), +(((v - peak) / peak) * 100).toFixed(2)))
  // 找前 5 段回撤
  const periods: { s: number; t: number; e: number; depth: number }[] = []
  let i = 0
  while (i < dd.length) {
    if (dd[i] < 0) {
      const s = i - 1 < 0 ? 0 : i - 1
      let t = i
      while (i < dd.length && dd[i] < 0) {
        if (dd[i] < dd[t]) t = i
        i++
      }
      periods.push({ s, t, e: Math.min(i, dd.length - 1), depth: dd[t] })
    } else i++
  }
  const top5 = periods.sort((a, b) => a.depth - b.depth).slice(0, 5)
  // 月度收益
  const monthly: Record<string, number> = {}
  let prevMonthEnd = 1
  days.forEach((d, k) => {
    const m = d.slice(0, 7)
    const next = days[k + 1]?.slice(0, 7)
    if (next !== m) {
      monthly[m] = +(((equity[k] / prevMonthEnd - 1) * 100).toFixed(1))
      prevMonthEnd = equity[k]
    }
  })
  const trades = Array.from({ length: 48 }, (_, k) => {
    const rr = rng(1000 + k * 7919 + (cfg.seed - 17) * 31)
    rr(); rr()
    const ret = (rr() - 0.44) * 14
    const d = days[Math.floor((k / 48) * days.length)]
    return {
      key: k,
      date: d,
      code: (() => { const sh = rr() > 0.5; rr(); return `${sh ? '60' : '00'}${String(Math.floor(rr() * 9000 + 1000)).slice(0, 2)}xx.${sh ? 'SH' : 'SZ'}` })(),
      name: `示意${'甲乙丙丁戊己庚辛壬癸'[k % 10]}${k}`,
      entry: +(8 + rr() * 30).toFixed(2),
      ret: +ret.toFixed(2),
      hold: 1 + Math.floor(rr() * 3),
      reason: ['首板竞价入档', '情绪闸门通过', '封单比达标'][k % 3],
    }
  })
  const total = equity[equity.length - 1] - 1
  const years = days.length / 244
  return {
    days, equity, bench, dd, top5, monthly, trades,
    stats: {
      annual: (Math.pow(1 + total, 1 / years) - 1) * 100,
      total: total * 100,
      maxdd: Math.min(...dd),
      sharpe: cfg.sharpe,
      win: cfg.seed === 17 ? 54 : Math.round(46 + (cfg.target ?? 0) * 8 + (cfg.seed % 5)),
      n: cfg.seed === 17 ? 412 : Math.round(cfg.n * 0.6),
      fee: cfg.seed === 17 ? 18432 : Math.round(cfg.n * 0.6 * 44.7),
    },
  }
}
