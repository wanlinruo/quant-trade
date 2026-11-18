import type { BacktestDetail, BacktestList, ResearchJobList, StrategyDetail, StrategyList } from '../../api/research'
import backtestRunning from './backtest-running.sample.json'
import backtest from './backtest.sample.json'
import backtests from './backtests.sample.json'
import research from './research-jobs.sample.json'
import strategies from './strategies.sample.json'
import strategy from './strategy.sample.json'

/** 后端测试夹具导出的真实形状（见 tests/web/test_app_research.py），每次深拷贝。 */
const clone = <T,>(x: unknown) => structuredClone(x) as unknown as T
export const fx = {
  strategies: () => clone<StrategyList>(strategies),
  strategy: () => clone<StrategyDetail>(strategy),
  backtests: () => clone<BacktestList>(backtests),
  backtest: () => clone<BacktestDetail>(backtest),
  backtestRunning: () => clone<BacktestDetail>(backtestRunning),
  research: () => clone<ResearchJobList>(research),
}
