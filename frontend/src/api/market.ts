import { getJson } from './client'
import type { MarketDisabled, MarketOverview } from './contracts.gen'

export type {
  ExchangeCoverage, IndexQuote, LimitCheck, LimitCheckSample, LimitPair, LiveMarket, MarketCoverage,
  MarketDisabled, MarketOverview, MarketSchedule, MarketSnapshot, RegimeDay,
} from './contracts.gen'

export type MarketResponse = MarketOverview | MarketDisabled

export const fetchMarket = (signal: AbortSignal) => getJson<MarketResponse>('/api/app/market', signal)
