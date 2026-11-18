import { useMemo } from 'react'
import { Descriptions, Result, Select, Table } from 'antd'
import { fetchCatalog, fetchMetric, type CatalogList } from '../../api/data'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { setSearch, useSearch } from '../../shell/router'
import { usePalette } from '../../theme/context'
import { MONO } from '../../ui/Num'

const STATUS_COLOR: Record<string, 'bad' | 'warn' | 'ok' | 'muted'> = { failed: 'bad', known_issue: 'warn', ok: 'ok' }
const KIND: Record<string, string> = { direct: '直取', derived: '纯派生', factor: '因子' }

function MetricDetail({ id }: { id: string }) {
  const p = usePalette()
  const { data: d, error } = useFetch((s) => fetchMetric(id, s), [id])
  const option = useMemo<EChartsOption | null>(() => {
    if (!d || !d.enabled || d.history.length === 0) return null
    const rows = [...d.history].sort((a, b) => String(a.checked_at).localeCompare(String(b.checked_at)))
    return {
      grid: { left: 40, right: 12, top: 12, bottom: 24 },
      xAxis: { type: 'category', data: rows.map((r) => String(r.checked_at).slice(0, 16).replace('T', ' ')) },
      yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: p.grid } } },
      series: [{ name: '违规数', type: 'line', connectNulls: false, data: rows.map((r) => (typeof r.violations === 'number' ? r.violations : null)), lineStyle: { color: p.bad } }],
    }
  }, [d, p])
  if (error) return <Panel title="指标详情"><div style={{ color: p.bad }}>{error}</div></Panel>
  if (!d || !d.enabled) return <Panel title="指标详情"><div style={{ color: p.muted }}>加载中</div></Panel>
  const r = d.row
  const join = (v: string[]) => (v.length ? v.join('、') : '—')
  return (
    <>
      <Panel title={`指标「${r.id}」的口径`} info="来源：直取 = 数据源接口原值；纯派生 = 直取列的确定性算术（数据铁律）。">
        <Descriptions size="small" bordered column={{ xs: 1, md: 2 }} items={[
          { key: 'm', label: '含义', children: r.meaning }, { key: 'k', label: '来源', children: `${KIND[r.kind] ?? r.kind} · ${r.source_label}` },
          { key: 'v', label: '有效性', children: r.validity }, { key: 's', label: '状态', children: `${r.label}${r.issues.length ? ` · ${r.issues.join('；')}` : ''}` },
          { key: 'f', label: '公式', children: d.formula ? <span style={{ fontFamily: MONO }}>{d.formula}</span> : '—' },
          { key: 'pa', label: '参数', children: d.params.length ? d.params.map(([k, v]) => `${k}=${String(v)}`).join(', ') : '—' },
          { key: 'u', label: '上游', children: join(d.upstream) }, { key: 'r', label: '根数据源', children: join(d.roots) },
          { key: 'd', label: '下游', children: join(d.downstream) }, { key: 'e', label: '外部消费者', children: join(d.ext_consumers) },
        ]} />
      </Panel>
      <Panel title="最近检查">
        {d.checks.length === 0 ? <div style={{ color: p.muted }}>没有检查结果</div> : (
          <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={d.checks} scroll={{ x: 'max-content' }}
            columns={['check_id', 'checked_at', 'passed', 'severity', 'known_issue', 'violations', 'checked_rows', 'detail'].map((k) => ({ title: k, dataIndex: k, render: (v: unknown) => String(v ?? '') }))} />
        )}
        {option && <><div style={{ fontSize: 12, color: p.muted, margin: '8px 0 0' }}>违规数历史</div><EChart option={option} height={140} /></>}
      </Panel>
      <Panel title="人工抽样验真" info="维护者用同花顺等外部来源抽样核对的记录（metric_evidence）。">
        {d.evidence.length === 0 ? <div style={{ color: p.muted }}>暂无人工抽样记录</div> : (
          <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={d.evidence} scroll={{ x: 'max-content' }}
            columns={[
              { title: '标的', dataIndex: 'symbol' }, { title: '日期', dataIndex: 'dt' }, { title: '本系统值', dataIndex: 'our_value' },
              { title: '参考值', dataIndex: 'reference_value' }, { title: '参考来源', dataIndex: 'reference_source' }, { title: '结论', dataIndex: 'verdict' },
              { title: '核验人', dataIndex: 'checked_by' }, { title: '时间', dataIndex: 'checked_at', render: (v: string) => v?.replace('T', ' ') }, { title: '备注', dataIndex: 'note' },
            ]} />
        )}
      </Panel>
    </>
  )
}

/** 指标字典（§8.13）：口径、来源（直取/纯派生）、检查与人工抽样验真。筛选写进 URL。 */
export function CatalogPage() {
  const p = usePalette()
  const q = useSearch()
  const f = { dataset: q.get('dataset'), kind: q.get('kind'), status: q.get('status') }
  const id = q.get('id')
  const { data, error } = useFetch((s) => fetchCatalog(f, s), [f.dataset, f.kind, f.status])
  const d = data as CatalogList | null
  if (error) return <Hero s="bad" title="指标字典接口无法访问" subtitle={error} />
  if (!d) return <Hero s="running" title="加载中" />
  if (!d.enabled) return <Result status="info" title="指标字典未启用" />
  const s = d.summary
  return (
    <>
      <Hero s={s.latest_run ? 'ok' : 'idle'} title={`共 ${s.total} 个指标 · 已覆盖 ${s.covered} · 待验证 ${s.pending}`}
        subtitle={`最近一次质检 ${s.latest_run ? s.latest_run.replace('T', ' ') : '无记录'} · ${s.by_kind.map(([k, n]) => `${KIND[k] ?? k} ${n}`).join(' · ')}`} />
      <Panel title="有哪些指标？" extra={
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, maxWidth: '100%', minWidth: 0 }}>
          <Select size="small" allowClear showSearch placeholder="按表" style={{ width: 180, maxWidth: '100%' }} value={f.dataset ?? undefined} onChange={(v) => setSearch({ dataset: v ?? null })} options={d.datasets.map((x) => ({ value: x, label: x }))} />
          <Select size="small" allowClear placeholder="按来源" style={{ width: 120, maxWidth: '100%' }} value={f.kind ?? undefined} onChange={(v) => setSearch({ kind: v ?? null })} options={d.kinds.map((x) => ({ value: x, label: KIND[x] ?? x }))} />
          <Select size="small" allowClear placeholder="按状态" style={{ width: 120, maxWidth: '100%' }} value={f.status ?? undefined} onChange={(v) => setSearch({ status: v ?? null })} options={d.statuses.map(([v, l]) => ({ value: v, label: l }))} />
        </div>}>
        <Table size="small" rowKey="id" dataSource={d.rows} scroll={{ x: 'max-content' }} pagination={{ pageSize: 30, hideOnSinglePage: true, showSizeChanger: false }}
          rowClassName={(r) => (r.id === id ? 'ant-table-row-selected' : '')}
          onRow={(r) => ({ onClick: () => setSearch({ id: r.id }), style: { cursor: 'pointer' } })}
          columns={[
            { title: '指标', dataIndex: 'id', render: (v: string) => <span style={{ fontFamily: MONO }}>{v}</span> },
            { title: '含义', dataIndex: 'meaning' }, { title: '来源', key: 'k', render: (_, r) => `${KIND[r.kind] ?? r.kind} · ${r.source_label}` },
            { title: '状态', dataIndex: 'label', render: (v: string, r) => <span style={{ color: STATUS_COLOR[r.status] === 'bad' ? p.bad : STATUS_COLOR[r.status] === 'warn' ? p.warn : STATUS_COLOR[r.status] === 'ok' ? p.ok : p.muted }}>{v}</span> },
            { title: '问题', dataIndex: 'issues', render: (v: string[]) => v.join('；') },
          ]} />
      </Panel>
      {id && <MetricDetail key={id} id={id} />}
    </>
  )
}
