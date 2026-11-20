// 示意场景：同一个交易日（2026-11-16 周一）的三种情形：正常盘中、晚间、异常日盘中。切换后整页重载，所有示意数据按场景重新生成。
export type Scene = 'am' | 'pm' | 'bad'
const q = new URLSearchParams(location.search).get('scene')
export const SCENE: Scene = q === 'pm' || q === 'am' || q === 'bad' ? q : ((localStorage.getItem('qs-scene') as Scene) || 'am')
/** 晚间 20:40：已收盘、对账完成、晚间任务大多跑完，日报 20:30 已发。 */
export const PM = SCENE === 'pm'
/** 异常日 10:48：10:31:05 Linux⇄Windows Redis 隧道中断，心跳超 90s 后 watchdog 10:32:35 触发停机闸；
 *  隧道 10:36:40 恢复，但停机闸不自动解除，等维护者经受管入口核对后解除。其余与正常盘中相同。 */
export const BAD = SCENE === 'bad'
export const NOW_HMS = PM ? '20:40:05' : BAD ? '10:48:20' : '10:42:15'
export const NOW_SEC = PM ? 20 * 3600 + 40 * 60 + 5 : BAD ? 10 * 3600 + 48 * 60 + 20 : 10 * 3600 + 42 * 60 + 15
export const NOW_H = NOW_SEC / 3600
export function setScene(s: Scene) {
  localStorage.setItem('qs-scene', s)
  const u = new URL(location.href)
  if (u.searchParams.has('scene')) { u.searchParams.delete('scene'); location.replace(u.toString()) } else location.reload()
}
/** 未处理告警数（初始值）；告警中心确认后用 publishAlertsOpen 同步到侧栏和顶栏。 */
export const ALERTS_OPEN0 = PM ? 1 : BAD ? 4 : 2
export function publishAlertsOpen(n: number) {
  sessionStorage.setItem('qs-alerts-open-' + SCENE, String(n))
  dispatchEvent(new CustomEvent('qs-alerts', { detail: n }))
}

const loadedAt = Date.now()
/** 示意时钟当前时刻 hh:mm:ss（从场景时刻起走秒）。 */
export function fmtNow() {
  const s = NOW_SEC + Math.floor((Date.now() - loadedAt) / 1000)
  return [s / 3600, (s % 3600) / 60, s % 60].map((x) => String(Math.floor(x)).padStart(2, '0')).join(':')
}
/** 异常日的中断窗口（分钟序号，09:30 为 0）：10:31–10:36 行情快照缺失 */
export const GAP = BAD ? [61, 66] as const : null
export const KILL_AT = '10:32:35'
