import { useCallback, useMemo } from 'react'
import { dateTime } from '../../shell/time'
import { WARNING_SOURCE_LABEL } from './labels'
import { Descriptions, Drawer, Result, Table } from 'antd'
import { fetchQuality, fetchRule, qualitySampleUrl, type QualitySummary, type RuleRow } from '../../api/data'
import { EChart, type EChartsOption } from '../../page/EChart'
import { DrawerNav } from '../../page/DrawerNav'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { usePolling } from '../../page/usePolling'
import { setSearch, useSearch } from '../../shell/router'
import { usePalette } from '../../theme/context'
import type { Status } from '../../theme/status'
import { MONO } from '../../ui/Num'
import { StatusTag } from '../../ui/StatusTag'

export const ISSUE_BASE = 'https://github.com/wanlinruo/quant-trade/issues/'
const RULE_STATUS: Record<RuleRow['latest_status'], [Status, string]> = { blocking: ['bad', '阻断'], warning: ['warn', '警告'], unknown: ['idle', '无结果'], passed: ['ok', '通过'] }
const RANK: Record<RuleRow['latest_status'], number> = { blocking: 0, warning: 1, unknown: 2, passed: 3 }

function RuleDrawer({ id, rows, onPick, onClose }: { id: string; rows: RuleRow[]; onPick(id: string): void; onClose(): void }) {
  const p = usePalette()
  const { data: d, error } = useFetch((s) => fetchRule(id, s), [id])
  const current = rows.find((x) => x.id === id)
  return (
    <Drawer open onClose={onClose} size="large" title={`规则 ${id}`} destroyOnHidden
      extra={current ? <DrawerNav rows={rows} current={current} identify={(x) => x.id} onPick={(x) => onPick(x.id)} copyText={id} /> : null}>
      {error && <div style={{ color: p.bad }}>{error}</div>}
      {!d && !error && <div style={{ color: p.muted }}>加载中</div>}
      {d && d.enabled && (
        <>
          <Descriptions size="small" bordered column={1} items={[
            { key: 'n', label: '名称', children: d.name }, { key: 'c', label: '类别', children: d.category },
            { key: 'cond', label: '通过条件', children: d.condition }, { key: 'l', label: '限制', children: d.limitation },
            { key: 'i', label: '实现', children: <span style={{ fontFamily: MONO, fontSize: 11 }}>{d.implementation}</span> },
            { key: 't', label: '目标', children: d.targets.join('、') || '—' },
          ]} />
          <div style={{ margin: '12px 0 6px', fontWeight: 600 }}>最新结果</div>
          {d.latest.length === 0 ? <div style={{ color: p.muted }}>没有结果</div> : (
            <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={d.latest} scroll={{ x: 'max-content' }}
              columns={[
                ...['metric_id', 'dataset_id', 'checked_at', 'passed', 'status', 'severity', 'known_issue', 'violations', 'checked_rows', 'detail']
                  .filter((k) => d.latest.some((r) => k in r)).map((k) => ({ title: k, dataIndex: k, render: (v: unknown) => String(v ?? '') })),
                { title: '样本', key: 's', render: (_: unknown, r: (typeof d.latest)[number]) => r.sample_file ? <a href={qualitySampleUrl(r.sample_file)}>下载违规样本</a> : '—' },
              ]} />
          )}
          <div style={{ marginTop: 8, fontSize: 12, color: p.muted }}>历史结果 {d.history.length} 条</div>
        </>
      )}
    </Drawer>
  )
}

/** 数据质量（§8.13）：近 30 次质检结构、警告来源归类、规则表与规则抽屉。 */
export function QualityPage() {
  const p = usePalette()
  const q = useSearch()
  const rule = q.get('rule')
  const fetcher = useCallback((s: AbortSignal) => fetchQuality(s), [])
  const poll = usePolling(fetcher, { intervalMs: 60_000, enabled: false })
  const d = poll.data as QualitySummary | null
  const option = useMemo<EChartsOption | null>(() => {
    if (!d || !d.enabled || d.runs.length === 0) return null
    const runs = [...d.runs].reverse()
    const bar = (name: string, key: 'passed' | 'warning' | 'blocking', color: string) => ({ name, type: 'bar' as const, stack: 'fields', data: runs.map((r) => r.fields[key]), itemStyle: { color } })
    return {
      grid: { left: 40, right: 12, top: 28, bottom: 40 }, legend: { top: 0, textStyle: { color: p.muted } },
      tooltip: { trigger: 'axis', formatter: (x: unknown) => {
        const items = x as { dataIndex: number }[]; const r = runs[items[0]?.dataIndex ?? 0]
        if (!r) return ''
        const t = r.tables ? `表级 红${r.tables.red} 黄${r.tables.yellow} 灰${r.tables.gray} 绿${r.tables.green}` : '无表级记录'
        return `${r.run_id}<br/>${dateTime(r.checked_at)}<br/>字段 通过${r.fields.passed} 警告${r.fields.warning} 阻断${r.fields.blocking}<br/>${t}`
      } },
      xAxis: { type: 'category', data: runs.map((r) => String(r.checked_at).slice(5, 16).replace('T', ' ')), axisLabel: { color: p.muted, rotate: 30 } },
      yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: p.grid } } },
      series: [bar('通过', 'passed', p.ok), bar('警告', 'warning', p.warn), bar('阻断', 'blocking', p.bad)],
    }
  }, [d, p])
  if (!d) return poll.error ? <Hero s="bad" title="数据质量接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />
  if (!d.enabled) return <Result status="info" title="数据资产未启用" />
  const last = d.runs[0]
  const unclassified = d.warning_sources.find((w) => w.key === 'unclassified')?.count ?? 0
  const missingTables = d.runs.filter((r) => r.tables === null).length
  const rules = [...d.rules].sort((a, b) => RANK[a.latest_status] - RANK[b.latest_status] || a.id.localeCompare(b.id))
  return (
    <>
      <Hero s={!last ? 'idle' : last.fields.blocking ? 'bad' : last.fields.warning ? 'warn' : 'ok'}
        title={last ? `最近一次质检：阻断 ${last.fields.blocking} · 警告 ${last.fields.warning} · 通过 ${last.fields.passed}` : '还没有质检记录'}
        subtitle={last ? `${last.checked_at ? dateTime(last.checked_at) : '时间未记录'} · ${last.run_id}` : ''}
        kpis={last ? [
          { k: '字段级 阻断 / 警告', v: `${last.fields.blocking} / ${last.fields.warning}`, color: last.fields.blocking ? p.bad : last.fields.warning ? p.warn : p.ok, sub: `共 ${last.fields.total} 条` },
          { k: '表级 红 / 黄', v: last.tables ? `${last.tables.red ?? 0} / ${last.tables.yellow ?? 0}` : '—', sub: last.tables ? undefined : '本次没有表级记录' },
          { k: `近 ${d.runs.length} 次有阻断`, v: `${d.runs.filter((r) => r.fields.blocking > 0).length} 次`, color: d.runs.some((r) => r.fields.blocking > 0) ? p.bad : p.ok },
          { k: '待归类警告', v: unclassified, color: unclassified ? p.warn : p.ok, sub: unclassified ? '未登记 Issue 或已知问题' : '均已归类' },
        ] : []} />
      <Panel title="近 30 次质检结构" info="按 metric_checks.run_id 汇总：通过、警告（未通过且非阻断）、阻断；表级按同一 run 的 dataset_checks 统计。">
        {option ? <EChart option={option} height={220} /> : <div style={{ color: p.muted }}>还没有质检记录</div>}
        {missingTables > 0 && <div style={{ fontSize: 12, color: p.muted }}>其中 {missingTables} 次质检没有表级记录（不按 0 计）。</div>}
      </Panel>
      <Panel title="警告从哪来？" info="只看最近一次质检中未通过且非阻断的记录：上游自相矛盾（#653）、已登记缺口（#594）、待归类。">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 8 }}>
          {d.warning_sources.map((g) => (
            <div key={g.key} data-source={g.key} style={{ border: `1px solid ${p.border}`, borderRadius: 4, padding: 10, minWidth: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ color: p.head }}>{WARNING_SOURCE_LABEL[g.key]}</span>
                {g.issue && <a href={`${ISSUE_BASE}${g.issue.slice(1)}`} target="_blank" rel="noreferrer">{g.issue}</a>}
              </div>
              <div style={{ fontSize: 20, color: g.key === 'unclassified' && g.count ? p.warn : p.text }}>{g.count}</div>
              {g.samples.map((s, i) => <div key={i} style={{ fontSize: 11, color: p.muted, overflowWrap: 'anywhere' }}>{s.metric_id} · {s.check_id}</div>)}
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="有哪些规则？">
        <Table size="small" rowKey="id" dataSource={rules} scroll={{ x: 'max-content' }} pagination={{ pageSize: 30, hideOnSinglePage: true, showSizeChanger: false }}
          onRow={(r) => ({ onClick: () => setSearch({ rule: r.id }), style: { cursor: 'pointer' } })}
          columns={[
            { title: '规则', dataIndex: 'id', render: (v: string) => <span style={{ fontFamily: MONO }}>{v}</span> },
            { title: '名称', dataIndex: 'name' }, { title: '类别', dataIndex: 'category' }, { title: '范围', dataIndex: 'scope' },
            { title: '最新', dataIndex: 'latest_status', render: (v: RuleRow['latest_status']) => <StatusTag s={RULE_STATUS[v][0]} text={RULE_STATUS[v][1]} /> },
            { title: '已登记问题', dataIndex: 'known_issues', render: (v: string[]) => v.join('；') || '—' },
          ]} />
      </Panel>
      {rule && <RuleDrawer key={rule} id={rule} rows={rules} onPick={(id) => setSearch({ rule: id })} onClose={() => setSearch({ rule: null })} />}
    </>
  )
}
