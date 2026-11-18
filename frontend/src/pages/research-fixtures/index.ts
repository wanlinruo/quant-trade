import type { BacktestPending, BacktestReport, BacktestList, ResearchJobList, StrategyDetail, StrategyList } from '../../api/research'
import backtestRunning from './backtest-running.sample.json'
import backtest from './backtest.sample.json'
import backtests from './backtests.sample.json'
import research from './research-jobs.sample.json'
import strategies from './strategies.sample.json'
import strategy from './strategy.sample.json'

/** 后端契约的真实输出（tests/webapp/test_webapp_research.py 的夹具经新接口导出），每次深拷贝。 */
const clone = <T,>(x: unknown) => structuredClone(x) as unknown as T
export const fx = {
  strategies: () => clone<StrategyList>(strategies),
  strategy: () => clone<StrategyDetail>(strategy),
  backtests: () => clone<BacktestList>(backtests),
  backtest: () => clone<BacktestReport>(backtest),
  backtestRunning: () => clone<BacktestPending>(backtestRunning),
  research: () => clone<ResearchJobList>(research),
}
