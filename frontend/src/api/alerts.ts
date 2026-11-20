import { getJson, sendJson } from './client'
import type { AckResult, AlertCenter, AlertDetail, AlertsDisabled } from './contracts.gen'

/** /api/app/alerts* 的类型由后端契约生成（src/qsys/webapp/contracts/alerts.py）。 */
export type { AckInfo, AlertCenter, AlertDetail, AlertEventInfo, AlertItem, AlertTrendDay, AlertsDisabled } from './contracts.gen'

const qs = (p: Record<string, string | null | undefined>) => {
  const q = new URLSearchParams(); for (const [k, v] of Object.entries(p)) if (v !== null && v !== undefined) q.set(k, v)
  const s = q.toString(); return s ? `?${s}` : ''
}

export const fetchAlerts = (day: string | null, s?: AbortSignal) => getJson<AlertCenter | AlertsDisabled>(`/api/app/alerts${qs({ day })}`, s)
export const fetchAlertDetail = (key: string, subject: string, day: string | null, s?: AbortSignal) =>
  getJson<AlertDetail>(`/api/app/alerts/detail${qs({ key, subject, day })}`, s)
export const ackAlert = (key: string, subject: string, through: string) =>
  sendJson<AckResult>('POST', '/api/app/alerts/ack', { key, subject, through })
