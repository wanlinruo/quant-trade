import type { ComponentType } from 'react'
import { AiluPage } from './ailu/AiluPage'
import { AlertsPage } from './alerts/AlertsPage'
import { BacktestPage } from './backtest/BacktestPage'
import { BoardPage } from './board/BoardPage'
import { CatalogPage } from './data/CatalogPage'
import { DataOverviewPage } from './data/DataOverviewPage'
import { MarketPage } from './market/MarketPage'
import { QualityPage } from './data/QualityPage'
import { TablesPage } from './data/TablesPage'
import { StrategiesPage } from './strategies/StrategiesPage'
import { LivePage } from './trading/LivePage'
import { OrdersPage } from './trading/OrdersPage'
import { PaperPage } from './trading/PaperPage'
import { ReconciliationPage } from './trading/ReconciliationPage'
import { RunsPage } from './trading/RunsPage'
import { ServicesPage } from './operations/ServicesPage'
import { NodesPage } from './operations/NodesPage'
import { ReleasesPage } from './operations/ReleasesPage'
import { TodayPage } from './today/TodayPage'

/** 已上线的新页面；没有登记的页面显示占位并链接旧页。 */
export const PAGE_COMPONENTS: Partial<Record<string, ComponentType>> = {
  today: TodayPage,
  alerts: AlertsPage,
  market: MarketPage,
  ailu: AiluPage,
  board: BoardPage,
  strategies: StrategiesPage,
  backtest: BacktestPage,
  live: LivePage,
  orders: OrdersPage,
  recon: ReconciliationPage,
  runs: RunsPage,
  paper: PaperPage,
  dataov: DataOverviewPage,
  tables: TablesPage,
  catalog: CatalogPage,
  quality: QualityPage,
  services: ServicesPage,
  nodes: NodesPage,
  releases: ReleasesPage,
}
