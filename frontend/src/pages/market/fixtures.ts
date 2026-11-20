import type { MarketOverview, MarketSnapshot, RegimeDay } from '../../api/market'
import sample from '../../api/samples/market.json'

/** 后端契约的真实输出（tests/webapp/test_webapp_market.py 的夹具经 /api/app/market 导出），每次深拷贝。 */
const base = () => structuredClone(sample) as unknown as MarketOverview

export function snapshot(over: Partial<MarketSnapshot> = {}): MarketSnapshot {
  return { ...base().live!.snapshot!, ...over }
}

export function regime(): RegimeDay[] {
  return base().regime!
}

export function payload(over: Partial<MarketOverview> = {}): MarketOverview {
  return { ...base(), ...over }
}
