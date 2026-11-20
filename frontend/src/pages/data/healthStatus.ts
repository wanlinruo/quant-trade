import type { Health } from '../../api/data'
import type { Status } from '../../theme/status'

/** 表级健康 → §7 状态。灰是“缺证据”，映射为待运行，绝不当作正常。 */
export const HEALTH_STATUS: Record<Health, Status> = { red: 'bad', yellow: 'warn', green: 'ok', gray: 'idle' }
export const HEALTH_SHORT: Record<Health, string> = { red: '红', yellow: '黄', green: '绿', gray: '灰' }
export const HEALTH_LABEL: Record<Health, string> = { red: '红·阻断', yellow: '黄·有风险', green: '绿·健康', gray: '灰·缺证据' }
export const HEALTH_ORDER: Health[] = ['red', 'yellow', 'gray', 'green']

export function worst(statuses: Health[]): Health | null {
  for (const h of HEALTH_ORDER) if (statuses.includes(h)) return h
  return null
}
