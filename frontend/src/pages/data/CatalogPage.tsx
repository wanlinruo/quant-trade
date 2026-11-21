import { useMemo } from 'react'
import { Descriptions, Result, Select, Table } from 'antd'
import { fetchCatalog, fetchMetric, type CatalogList, type MetricRow } from '../../api/data'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { setSearch, useSearch } from '../../shell/router'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { KIND_LABEL as KIND, METRIC_STATUS, METRIC_STATUS_ORDER, lineageText } from './labels'
import { MONO } from '../../ui/Num'


function MetricDetail({ id }: { id: string }) {
  const p = usePalette()
  const { data: d, error } = useFetch((s) => fetchMetric(id, s), [id])
  const option = useMemo<EChartsOption | null>(() => {
    if (!d || !d.enabled || d.history.length === 0) return null
    const rows = [...d.history].sort((a, b) => String(a.checked_at).localeCompare(String(b.checked_at)))
    return {
      grid: { left: 40, right: 12, top: 12, bottom: 24 },
      xAxis: { type: 'category', data: rows.map((r) => dateTime(r.checked_at).slice(0, 16)) },
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
        <Descriptions size="small" bordered column={1} items={[
          { key: 'm', label: '含义', children: r.meaning ?? '含义待查' }, { key: 'k', label: '来源', children: `${KIND[r.kind] ?? r.kind} · ${lineageText(r)}` },
          { key: 'v', label: '有效性', children: r.validity }, { key: 's', label: '状态', children: `${METRIC_STATUS[r.status].label}${r.issues.length ? ` · ${r.issues.join('；')}` : ''}` },
          { key: 'f', label: '公式', children: d.formula ? <span style={{ fontFamily: MONO }}>{d.formula}</span> : '—' },
          { key: 'pa', label: '参数', children: Object.keys(d.params).length ? Object.entries(d.params).map(([k, v]) => `${k}=${String(v)}`).join(', ') : '—' },
          { key: 'u', label: '上游', children: join(d.upstream) }, { key: 'r', label: '根数据源', children: join(r.roots) },
          { key: 'd', label: '下游', children: join(d.downstream) }, { key: 'e', label: '外部消费者', children: join(d.ext_consumers) },
        ]} />
      </Panel>
      <Panel title="最近检查">
        {d.checks.length === 0 ? <div style={{ color: p.muted }}>没有检查结果</div> : (
          <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={d.checks} scroll={{ x: 'max-content' }}
            columns={['check_id', 'checked_at', 'passed', 'severity', 'known_issue', 'violations', 'checked_rows', 'detail'].map((k) => ({ title: k, dataIndex: k, render: (v: unknown) => (k === 'checked_at' ? dateTime(v as string | null) : String(v ?? '')) }))} />
        )}
        {option && <><div style={{ fontSize: 12, color: p.muted, margin: '8px 0 0' }}>违规数历史</div><EChart option={option} height={140} /></>}
      </Panel>
      <Panel title="人工抽样验真" info="维护者用同花顺等外部来源抽样核对的记录（metric_evidence）。">
        {d.evidence.length === 0 ? <div style={{ color: p.muted }}>暂无人工抽样记录</div> : (
          <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={d.evidence} scroll={{ x: 'max-content' }}
            columns={[
              { title: '标的', dataIndex: 'symbol' }, { title: '日期', dataIndex: 'dt' }, { title: '本系统值', dataIndex: 'our_value' },
              { title: '参考值', dataIndex: 'reference_value' }, { title: '参考来源', dataIndex: 'reference_source' }, { title: '结论', dataIndex: 'verdict' },
              { title: '核验人', dataIndex: 'checked_by' }, { title: '时间', dataIndex: 'checked_at', render: (v: string | null) => dateTime(v) }, { title: '备注', dataIndex: 'note' },
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
  // 原型：默认展开第一个指标（列表已按问题优先排序）；URL 指定 id 时以 URL 为准
  const shown = id ?? d.rows[0]?.id ?? null
  return (
    <>
      <Hero s={!s.latest_run ? 'idle' : s.pending ? 'warn' : 'ok'} title={`${s.total} 个指标 · ${s.pending ? `${s.pending} 个待验证` : '全部已覆盖验证'}`}
        subtitle={`每个指标标明直取或纯派生及来源字段 · 最近一次质检 ${s.latest_run ? dateTime(s.latest_run) : '无记录'}`}
        kpis={[
          ...Object.entries(s.by_kind).map(([k, n]) => ({ k: KIND[k] ?? k, v: n })),
          { k: '已覆盖 / 待验证', v: `${s.covered} / ${s.pending}`, color: s.pending ? p.warn : p.ok },
        ]} />
      <div className="qs-master-detail">
      <Panel title="有哪些指标？" extra={
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, maxWidth: '100%', minWidth: 0 }}>
          <Select size="small" allowClear showSearch placeholder="按表" style={{ width: 180, maxWidth: '100%' }} value={f.dataset ?? undefined} onChange={(v) => setSearch({ dataset: v ?? null })} options={d.datasets.map((x) => ({ value: x, label: x }))} />
          <Select size="small" allowClear placeholder="按来源" style={{ width: 120, maxWidth: '100%' }} value={f.kind ?? undefined} onChange={(v) => setSearch({ kind: v ?? null })} options={d.kinds.map((x) => ({ value: x, label: KIND[x] ?? x }))} />
          <Select size="small" allowClear placeholder="按状态" style={{ width: 120, maxWidth: '100%' }} value={f.status ?? undefined} onChange={(v) => setSearch({ status: v ?? null })} options={METRIC_STATUS_ORDER.map((v) => ({ value: v, label: METRIC_STATUS[v].label }))} />
        </div>}>
        <Table size="small" rowKey="id" dataSource={d.rows} scroll={{ x: 'max-content' }} pagination={{ pageSize: 30, hideOnSinglePage: true, showSizeChanger: false }}
          rowClassName={(r) => (r.id === shown ? 'ant-table-row-selected' : '')}
          onRow={(r) => ({ onClick: () => setSearch({ id: r.id }), style: { cursor: 'pointer' } })}
          columns={[
            // 主从布局：列表只留指标、类型、状态；含义与问题在右侧详情
            { title: '指标', dataIndex: 'id', render: (v: string, r) => <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: MONO, color: p.head, fontSize: 12 }}>{v}</div>
              <div style={{ color: p.muted, fontSize: 11, maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.meaning ?? undefined}>{r.meaning ?? '含义待查'}</div></div> },
            { title: '类型', key: 'k', render: (_, r) => <span title={lineageText(r)} style={{ fontSize: 12 }}>{KIND[r.kind] ?? r.kind}</span> },
            { title: '状态', dataIndex: 'status', render: (v: MetricRow['status'], r) => { const pill = METRIC_STATUS[v].pill; return <span title={r.issues.join('；') || undefined} style={{ color: pill === 'bad' ? p.bad : pill === 'warn' ? p.warn : pill === 'ok' ? p.ok : p.muted, whiteSpace: 'nowrap' }}>{METRIC_STATUS[v].label}{r.issues.length ? ' ⓘ' : ''}</span> } },
          ]} />
      </Panel>
      <div className="qs-detail-pane" style={{ minWidth: 0 }}>{shown ? <MetricDetail key={shown} id={shown} /> : null}</div>
      </div>
    </>
  )
}
