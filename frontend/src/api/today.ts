import { getJson } from './client'
import type { TodayDisabled, TodayView } from './contracts.gen'

export type { TodayConcern, TodayEvent, TodayLink, TodayPhase, TodayView } from './contracts.gen'
const day = (d: string | null) => d ? `?day=${encodeURIComponent(d)}` : ''
export const fetchToday = (d: string | null, s?: AbortSignal) => getJson<TodayView | TodayDisabled>(`/api/app/today${day(d)}`, s)
