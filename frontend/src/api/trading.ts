import { getJson, sendJson } from './client'
import type {
  LiveTradingOverview, PaperAccountDetail, PaperAccounts, PaperActionResult, TradingDisabled,
  TradingOrders, TradingReconciliation, TradingRuns,
} from './contracts.gen'

export type { LiveTradingOverview, PaperAccountDetail, PaperAccountRow, PaperAccounts, RiskLimit,
  StrategyRunInfo, TradingDisabled, TradingOrder, TradingOrders, TradingPosition, TradingReconciliation, TradingRuns } from './contracts.gen'

const day = (d: string | null) => d ? `?day=${encodeURIComponent(d)}` : ''
export const fetchLive = (d: string | null, s?: AbortSignal) => getJson<LiveTradingOverview | TradingDisabled>(`/api/app/trading/live${day(d)}`, s)
export const fetchOrders = (d: string | null, s?: AbortSignal) => getJson<TradingOrders | TradingDisabled>(`/api/app/trading/orders${day(d)}`, s)
export const fetchReconciliation = (d: string | null, s?: AbortSignal) => getJson<TradingReconciliation | TradingDisabled>(`/api/app/trading/reconciliation${day(d)}`, s)
export const fetchRuns = (d: string | null, s?: AbortSignal) => getJson<TradingRuns | TradingDisabled>(`/api/app/trading/runs${day(d)}`, s)
export const reportUrl = (d: string | null) => `/api/app/trading/runs/report.txt${day(d)}`
export const fetchPaperAccounts = (s?: AbortSignal) => getJson<PaperAccounts | TradingDisabled>('/api/app/trading/paper', s)
export const fetchPaper = (id: string, d: string | null, s?: AbortSignal) => getJson<PaperAccountDetail | TradingDisabled>(`/api/app/trading/paper/${encodeURIComponent(id)}${day(d)}`, s)
export const paperAction = (id: string, action: 'pause' | 'resume' | 'end') => sendJson<PaperActionResult>('POST', `/api/app/trading/paper/${encodeURIComponent(id)}/action`, { action })
export const paperNotify = (id: string, webhook: string) => sendJson<PaperActionResult>('POST', `/api/app/trading/paper/${encodeURIComponent(id)}/notify`, { webhook })
