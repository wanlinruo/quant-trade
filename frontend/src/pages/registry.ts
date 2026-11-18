import type { ComponentType } from 'react'
import { AiluPage } from './ailu/AiluPage'
import { BacktestPage } from './backtest/BacktestPage'
import { BoardPage } from './board/BoardPage'
import { DataOverviewPage } from './data/DataOverviewPage'
import { MarketPage } from './market/MarketPage'
import { StrategiesPage } from './strategies/StrategiesPage'

/** 已上线的新页面；没有登记的页面显示占位并链接旧页。 */
export const PAGE_COMPONENTS: Partial<Record<string, ComponentType>> = {
  market: MarketPage,
  ailu: AiluPage,
  board: BoardPage,
  strategies: StrategiesPage,
  backtest: BacktestPage,
  dataov: DataOverviewPage,
}
