import { Progress, Result, Table } from 'antd'
import { useMemo } from 'react'
import { fetchLive, type RiskLimit } from '../../api/trading'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { pct } from '../../page/format'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { Num } from '../../ui/Num'
import { StatusTag } from '../../ui/StatusTag'
import { DaySelect, useTradingDay, yuan } from './common'

const riskValue = (r: RiskLimit) => r.value == null ? <Missing why="当前事实不足，不能计算" /> : r.unit === 'ratio' ? pct(r.value) : yuan(r.value)

export function LivePage() {
  const p = usePalette(); const day = useTradingDay()
  const { data, error } = useFetch((s) => fetchLive(day, s), [day])
  const option = useMemo<EChartsOption | null>(() => data?.enabled && data.equity.length ? ({
    grid: { left: 62, right: 12, top: 12, bottom: 24 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: data.equity.map((x) => x.day), boundaryGap: false },
    yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: p.grid } } },
    series: [{ type: 'line', data: data.equity.map((x) => x.total_value), connectNulls: false, showSymbol: false }],
  }) : null, [data, p])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="实盘查询未启用" />
  const d = data
  const posture = d.posture === 'trading' ? 'ok' : d.posture === 'reduce_only' ? 'bad' : 'idle'
  return <>
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}><DaySelect value={d.day} /></div>
    <Hero s={posture} statusText={d.posture_label} title={d.posture_label}
      subtitle="总资产为券商原始观测；缺完整资金流水时不计算收益。"
      kpis={[{ k: '总资产', v: d.observation.total_value == null ? <Missing why={d.missing.account ?? '缺失'} /> : <Num>{yuan(d.observation.total_value)}</Num> },
        { k: '持仓市值', v: d.observation.market_value == null ? <Missing why="券商未上报" /> : yuan(d.observation.market_value) },
        { k: '收益率', v: <Missing why={d.missing.return_ratio} /> }, { k: '今日委托', v: d.order_count ?? <Missing why="券商未上报" /> },
        { k: '停机闸', v: d.kill.label }]} />
    <Panel title="链路现在通吗？" info="只显示当前直取心跳；读不到为未知。">
      <Table size="small" pagination={false} rowKey="source" dataSource={d.heartbeats} columns={[
        { title: '节点', dataIndex: 'source' }, { title: '状态', key: 'state', render: (_, x) => <StatusTag s={x.fresh == null ? 'idle' : x.fresh ? 'ok' : 'bad'} text={x.fresh == null ? '未知' : x.fresh ? '在线' : '过期'} /> },
        { title: '最后心跳', dataIndex: 'at', render: dateTime }, { title: '年龄', dataIndex: 'age_seconds', render: (v) => v == null ? '—' : `${v.toFixed(1)}s` },
        { title: '阈值', dataIndex: 'threshold_seconds', render: (v) => v == null ? '—' : `${v.toFixed(1)}s` }, { title: '阶段', dataIndex: 'detail', render: (v) => v ?? '—' },
      ]} />
    </Panel>
    <Panel title="风险额度用了多少？" info="70% 提醒；红线是配置中的硬上限。页面只读。">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(210px,1fr))', gap: 12 }}>
        {d.risks.map((r) => <div key={r.id}><div style={{ display: 'flex', justifyContent: 'space-between' }}><b>{r.label}</b><span>{riskValue(r)} / {r.unit === 'ratio' ? pct(r.limit) : yuan(r.limit)}</span></div>
          <Progress percent={r.ratio == null ? 0 : Math.min(100, r.ratio * 100)} status={r.state === 'blocked' ? 'exception' : 'normal'} strokeColor={r.state === 'warning' ? p.warn : undefined} showInfo={false} /></div>)}
      </div>
    </Panel>
    <Panel title="账户总资产原始观测怎样变化？" info="不是收益曲线；资金划转也会改变总资产。">{option ? <EChart option={option} height={210} /> : <Missing why={d.missing.equity ?? '没有历史观测'} />}</Panel>
    <Panel title="现在持有什么？">
      <Table size="small" pagination={false} rowKey="symbol" dataSource={d.positions} scroll={{ x: 'max-content' }} columns={[
        { title: '标的', dataIndex: 'symbol' }, { title: '总量', dataIndex: 'total_qty', align: 'right' }, { title: '可卖', dataIndex: 'available_qty', align: 'right' },
        { title: '本系统预留', dataIndex: 'reserved_qty', align: 'right', render: (v) => v ?? <Missing why="状态库不可读" /> },
        { title: '成本', dataIndex: 'avg_cost', align: 'right', render: (v) => v?.toFixed(2) ?? '—' }, { title: '直取市值', dataIndex: 'market_value', align: 'right', render: yuan },
      ]} />
    </Panel>
  </>
}
