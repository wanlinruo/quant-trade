import { useEffect, useState } from 'react'
import { Descriptions, Table, Tabs } from 'antd'
import { fetchTable, type Dependency, type Health, type TableDetail as Detail } from '../../api/data'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { goTo, setSearch } from '../../shell/router'
import { usePalette } from '../../theme/context'
import { MONO } from '../../ui/Num'
import { StatusTag } from '../../ui/StatusTag'
import { CoverageText } from './coverage'
import { HEALTH_LABEL, HEALTH_STATUS } from './healthStatus'
import { TableSample } from './TableSample'

const TABS = ['overview', 'fields', 'quality', 'lineage', 'sample'] as const
type TabKey = (typeof TABS)[number]
const list = (v: string[]) => (v.length ? v.join('、') : '—')

/** 数据表详情：概览、字段、质量、血缘、样本（§8.13）。子标签写进 ?tab=。 */
export function TableDetail({ id, tab }: { id: string; tab: string | null }) {
  const p = usePalette()
  const [d, setD] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const c = new AbortController()
    setD(null); setError(null)
    fetchTable(id, c.signal).then(setD, (e: Error) => { if (e.name !== 'AbortError') setError(e.message) })
    return () => c.abort()
  }, [id])
  if (error) return <Panel title="数据表详情"><div style={{ color: p.bad }}>{error}</div></Panel>
  if (!d) return <Panel title="数据表详情"><div style={{ color: p.muted }}>加载中</div></Panel>
  if (!d.enabled) return null
  const ds = d.row.dataset
  const active: TabKey = (TABS as readonly string[]).includes(tab ?? '') ? (tab as TabKey) : 'overview'
  const health = (h: Health) => <StatusTag s={HEALTH_STATUS[h]} text={HEALTH_LABEL[h]} />

  const overview = (
    <Descriptions size="small" column={{ xs: 1, md: 2 }} bordered items={[
      { key: 'p', label: '用途', children: ds.purpose }, { key: 'g', label: '粒度', children: ds.grain },
      { key: 'k', label: '主键', children: list(ds.primary_key) }, { key: 't', label: '时间列', children: list(ds.time_columns) },
      { key: 'pk', label: '分区键', children: list(ds.partition_keys) },
      { key: 's', label: 'schema', children: ds.schema_id ? `${ds.schema_id} v${ds.schema_version}` : '逻辑视图' },
      { key: 'b', label: '构建入口', children: <span style={{ fontFamily: MONO, fontSize: 11 }}>{ds.builder}</span> },
      { key: 'u', label: '更新方式', children: ds.update_mode }, { key: 'l', label: '生命周期', children: ds.lifecycle },
      { key: 'c', label: '覆盖', children: <CoverageText row={d.row} /> },
    ]} />
  )
  const fields = (
    <Table size="small" pagination={false} rowKey={(r) => r[0]} dataSource={d.fields} scroll={{ x: 'max-content' }}
      columns={[{ title: '字段', render: (_, r) => <span style={{ fontFamily: MONO }}>{r[0]}</span> }, { title: '类型', render: (_, r) => r[1] }, { title: '说明', render: (_, r) => r[2] }]} />
  )
  const quality = (
    <>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        {d.dimensions.map(([label, h]) => <span key={label}>{label} {health(h)}</span>)}
      </div>
      {d.reasons.length > 0 && <ul style={{ margin: '0 0 8px', paddingLeft: 18 }}>{d.reasons.map((r, i) => <li key={i}>{r.message}{r.check_id ? `（${r.check_id}）` : ''}</li>)}</ul>}
      {d.evidence.length === 0 ? <div style={{ color: p.muted, marginBottom: 8 }}>没有可用的质检证据</div> : (
        <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={d.evidence} scroll={{ x: 'max-content' }}
          columns={['check_id', 'dimension', 'status', 'observed_at', 'violations', 'checked_rows'].map((k) => ({ title: k, dataIndex: k, render: (v: unknown) => String(v ?? '') }))} />
      )}
      <div style={{ marginTop: 8, fontSize: 12 }}>适用规则：{d.rules.length === 0 ? '—' : d.rules.map((r) => (
        <a key={r} style={{ marginRight: 10 }} onClick={() => goTo('quality', { rule: r })}>{r}</a>
      ))}</div>
    </>
  )
  const deps = (rows: Dependency[], dir: '上游' | '下游') => rows.length === 0 ? <div style={{ color: p.muted }}>没有{dir}依赖</div> : (
    <Table size="small" pagination={false} rowKey={(r) => `${r.upstream}->${r.downstream}`} dataSource={rows} scroll={{ x: 'max-content' }}
      columns={[
        { title: dir, render: (_, r) => <a onClick={() => setSearch({ id: dir === '上游' ? r.upstream : r.downstream, tab: 'lineage' })}>{dir === '上游' ? r.upstream : r.downstream}</a> },
        { title: '关系', dataIndex: 'kind' }, { title: '连接键', render: (_, r) => r.join_keys.map(([a, b]) => `${a}=${b}`).join(', ') || '—' },
        { title: '基数', dataIndex: 'cardinality' }, { title: '要求', dataIndex: 'requirement' }, { title: '允许滞后', dataIndex: 'allowed_lag' },
      ]} />
  )
  const lineage = (
    <>
      <div style={{ marginBottom: 8 }}>数据源：{d.source_refs.length === 0 ? '—' : d.source_refs.map((s) => <span key={s.label} style={{ marginRight: 10 }}>{s.label}</span>)}</div>
      <div style={{ marginBottom: 8, color: p.muted, fontSize: 12 }}>根数据源：{d.root_refs.map((s) => s.label).join('、') || '—'} · 消费者：{list(ds.consumers)}</div>
      {deps(d.upstream, '上游')}
      <div style={{ height: 8 }} />
      {deps(d.downstream, '下游')}
    </>
  )
  const content: Record<TabKey, React.ReactNode> = { overview, fields, quality, lineage, sample: <TableSample id={ds.id} /> }
  return (
    <>
      <Hero s={HEALTH_STATUS[d.row.status]} statusText={d.status_label} title={`${ds.name} · ${ds.id}`} subtitle={`${d.domain_label} · ${ds.layer} · ${ds.status}`} />
      <Panel title="这张表怎样？">
        <Tabs activeKey={active} onChange={(k) => setSearch({ tab: k === 'overview' ? null : k })}
          items={[['overview', '概览'], ['fields', '字段'], ['quality', '质量'], ['lineage', '血缘'], ['sample', '样本']].map(([k, label]) => ({
            key: k, label, children: active === k ? content[k as TabKey] : null,
          }))} />
      </Panel>
    </>
  )
}
