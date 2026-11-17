import { Button, Result } from 'antd'
import { fetchMarket, type MarketPayload, type MarketSnapshot } from '../../api/market'
import { Freshness } from '../../page/Freshness'
import { Hero, type Kpi } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { usePolling } from '../../page/usePolling'
import { Value } from '../../page/Value'
import { int, pct, signedPct, yi } from '../../page/format'
import { hms, parseLocal } from '../../shell/time'
import type { Palette } from '../../theme/palette'
import { usePalette } from '../../theme/context'
import type { Status } from '../../theme/status'
import { Missing } from '../../ui/Missing'
import { Num } from '../../ui/Num'
import { CoverageTable } from './CoverageTable'
import { LimitCheck } from './LimitCheck'
import { TrendChart } from './TrendChart'

const upDown = (p: Palette, v: number) => (v > 0 ? p.up : v < 0 ? p.down : p.muted)

function heroOf(d: MarketPayload, p: Palette) {
  const live = d.live
  const snap = live?.snapshot ?? null
  if (!snap) {
    const failed = !!d.errors.live
    return { s: (failed ? 'warn' : 'idle') as Status, statusText: undefined, title: failed ? '实时快照读取失败' : '无实时快照',
      subtitle: '行情网关未发布当日快照或未运行；近 60 日趋势仍按收盘后数据显示。', kpis: [] as Kpi[] }
  }
  const closed = d.schedule ? !d.schedule.is_open : false
  const s: Status = closed ? 'closed' : live!.stale ? 'warn' : 'ok'
  const statusText = closed ? '休市 · 最近快照' : live!.stale ? '已过期' : undefined
  const at = parseLocal(snap.published_at)
  const expected = snap.coverage?.expected ?? null
  return {
    s, statusText,
    title: `涨停 ${snap.limit_up_count} · 跌停 ${snap.limit_down_count} · 上涨占比 ${pct(snap.market_advance_ratio)}`,
    subtitle: `快照 ${snap.session} ${at ? hms(at) : '时间未知'} · 有效 ${int(snap.valid_stock_count)} / 应有 ${expected === null ? '未知' : int(expected)}`,
    kpis: kpisOf(snap, p),
  }
}

function kpisOf(s: MarketSnapshot, p: Palette): Kpi[] {
  return [
    { k: '触板 / 炸板', v: <Num>{s.touched_limit_up_count} / {s.broken_board_count}</Num> },
    { k: '炸板率', v: <Num>{pct(s.broken_board_rate)}</Num> },
    { k: '中位涨幅', v: <Num color={upDown(p, s.market_median_return_1d)}>{signedPct(s.market_median_return_1d)}</Num> },
    { k: '成交额', v: <Num>{yi(s.market_total_money)}</Num> },
    { k: '缺涨跌停价', v: <Num>{s.limit_price_missing_count}</Num>, sub: '不计入涨跌停统计' },
    { k: '封板未知 涨 / 跌', v: <span><Value v={s.limit_up_unknown_count} why="旧生产者没有该口径（#647）" /> / <Value v={s.limit_down_unknown_count} why="旧生产者没有该口径（#647）" /></span> },
  ]
}

function Indices({ snap }: { snap: MarketSnapshot | null }) {
  const p = usePalette()
  if (!snap || snap.indices.length === 0) return <div style={{ color: p.muted, marginBottom: 12 }}>行情网关尚未发布指数快照。</div>
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8, marginBottom: 12 }}>
      {snap.indices.map((x) => (
        <div key={x.symbol} style={{ background: p.panel, border: `1px solid ${p.border}`, borderRadius: 4, padding: '8px 10px' }}>
          <div style={{ fontSize: 12, color: p.muted }}>{x.name}</div>
          <Num size={16} color={p.head}>{x.price.toFixed(2)}</Num>{' '}
          <Num color={upDown(p, x.change_pct)}>{signedPct(x.change_pct)}</Num>
        </div>
      ))}
    </div>
  )
}

export function MarketPage() {
  const p = usePalette()
  const poll = usePolling(fetchMarket, { intervalMs: 5000, minIntervalMs: 15000 })
  const fresh = <Freshness at={poll.fetchedAt} paused={poll.paused} onTogglePause={() => poll.setPaused(!poll.paused)} onRefresh={poll.refresh} error={poll.error} />
  const head = <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>{fresh}</div>
  const d = poll.data
  if (!d) {
    return <>{head}{poll.error ? <Hero s="bad" title="市场接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />}</>
  }
  if (!d.enabled) {
    return <Result status="info" title="行情控制台未启用" subTitle="当前部署没有接入行情服务。" extra={<Button href="/market">打开现有页面 →</Button>} />
  }
  const h = heroOf(d, p)
  const snap = d.live?.snapshot ?? null
  return (
    <>
      {head}
      <Hero {...h} />
      <Indices snap={snap} />
      <Panel title="近 60 日市场情绪怎样？" info="数据来自 market_regime_daily：收盘后由直取日线纯派生；缺值的日子图上断开，不连线。">
        {d.regime === null
          ? <Missing why="历史数据读取失败" />
          : d.regime.length === 0 ? <div style={{ color: p.muted }}>暂无历史数据</div> : <TrendChart rows={d.regime} />}
      </Panel>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 12 }}>
        <Panel title="上证分时"><Missing why={d.missing.index_intraday} /></Panel>
        <Panel title="涨跌幅分布"><Missing why={d.missing.return_distribution} /></Panel>
      </div>
      <Panel title="各市场行情覆盖全了吗？" info="应有数来自绑定的股票池（stock_basic）；没有绑定时不推算。待核验 = 无效 + 其他交易日 + 停牌。">
        <CoverageTable coverage={snap?.coverage ?? null} hasSnapshot={!!snap} />
      </Panel>
      <Panel title="涨停价核对" info="Tushare stk_limit 与 QMT 合约详情逐只核对，由行情网关发布（D-19）。">
        <LimitCheck check={d.limit_check} />
      </Panel>
    </>
  )
}
