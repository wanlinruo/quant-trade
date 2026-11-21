import { useCallback } from 'react'
import { domainLabel } from './labels'
import { Result, Table } from 'antd'
import { fetchOverview, type DataOverview, type Health } from '../../api/data'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { usePolling } from '../../page/usePolling'
import { HealthMatrix } from './HealthMatrix'
import { LineageGraph } from './LineageGraph'
import { usePalette } from '../../theme/context'
import { HEALTH_ORDER, HEALTH_SHORT, HEALTH_STATUS, worst } from './healthStatus'

export function DataOverviewPage() {
  const p = usePalette()
  const fetcher = useCallback((s: AbortSignal) => fetchOverview(s), [])
  const poll = usePolling(fetcher, { intervalMs: 60_000, enabled: false })
  const d = poll.data as DataOverview | null
  if (!d) return poll.error ? <Hero s="bad" title="数据接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />
  if (!d.enabled) return <Result status="info" title="数据资产未启用" subTitle="当前部署没有接入数据资产服务。" />
  const count = (h: Health) => d.tables.filter((t) => t.status === h).length
  const w = worst(d.tables.map((t) => t.status))
  return (
    <>
      <Hero s={w ? HEALTH_STATUS[w] : 'idle'}
        title={`${d.tables.length} 张核心表 · ${count('green')} 健康${count('red') ? ` · ${count('red')} 张故障` : ''}${count('yellow') ? ` · ${count('yellow')} 张有风险` : ''}`}
        subtitle="灰 = 缺证据（没有可用的质检结果），不等于健康。原始层不计入核心表。"
        kpis={HEALTH_ORDER.map((h) => ({
          k: { red: '故障（红）', yellow: '有风险（黄）', gray: '缺证据（灰）', green: '健康（绿）' }[h], v: count(h),
          color: count(h) ? { red: p.bad, yellow: p.warn, gray: p.muted, green: p.ok }[h] : undefined,
          sub: h === 'gray' ? '没有可用质检结果' : undefined,
        }))} />
      <Panel title="核心表健康吗？" info="五维：时效、完整、结构、真实、血缘；任一维缺证据即为灰。数据来自 dataset_checks 的最新有效证据。">
        <HealthMatrix tables={d.tables} />
      </Panel>
      <Panel title="数据从哪来、被谁用？" info="数据源 → 各层数据表 → 消费者；边来自表契约中的来源、消费者与跨表依赖。悬停高亮上下游，可拖动缩放。">
        <LineageGraph nodes={d.lineage.nodes} edges={d.lineage.edges} />
      </Panel>
      <Panel title="各数据域怎样？">
        <Table size="small" rowKey={(r) => domainLabel(r.domain)} pagination={false} dataSource={d.domains} scroll={{ x: 'max-content' }}
          columns={[
            { title: '数据域', key: 'domain', render: (_: unknown, r: (typeof d.domains)[number]) => domainLabel(r.domain) }, { title: '表', dataIndex: 'tables', align: 'right' },
            { title: '数据源', dataIndex: 'sources', align: 'right' },
            ...HEALTH_ORDER.map((h) => ({ title: HEALTH_SHORT[h], key: h, align: 'right' as const, render: (_: unknown, r: (typeof d.domains)[number]) => r.status_counts[h] ?? 0 })),
          ]} />
      </Panel>
    </>
  )
}
