import { useCallback } from 'react'
import { Checkbox, Result, Select, Table } from 'antd'
import { fetchTables, type TableList } from '../../api/data'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { usePolling } from '../../page/usePolling'
import { setSearch, useSearch } from '../../shell/router'
import { StatusTag } from '../../ui/StatusTag'
import { CoverageText } from './coverage'
import { HEALTH_LABEL, HEALTH_STATUS } from './healthStatus'
import { TableDetail } from './TableDetail'

export function TablesPage() {
  const q = useSearch()
  const raw = q.get('raw') === '1'
  const domain = q.get('domain')
  const id = q.get('id')
  const fetcher = useCallback((s: AbortSignal) => fetchTables(raw, s), [raw])
  const poll = usePolling(fetcher, { intervalMs: 60_000, enabled: false })
  const d = poll.data as TableList | null
  if (!d) return poll.error ? <Hero s="bad" title="数据接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />
  if (!d.enabled) return <Result status="info" title="数据资产未启用" />
  const domains = [...new Set(d.rows.map((r) => r.domain_label))].sort()
  const rows = domain ? d.rows.filter((r) => r.domain_label === domain) : d.rows
  return (
    <>
      <Panel title="有哪些表？" extra={
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', maxWidth: '100%', minWidth: 0 }}>
          <Select size="small" allowClear placeholder="按数据域筛选" style={{ width: 200, maxWidth: '100%' }} value={domain ?? undefined}
            onChange={(v) => setSearch({ domain: v ?? null })} options={domains.map((x) => ({ value: x, label: x }))} />
          <Checkbox checked={raw} onChange={(e) => setSearch({ raw: e.target.checked ? '1' : null })}>含原始层</Checkbox>
        </div>}>
        <Table size="small" rowKey="id" dataSource={rows} scroll={{ x: 'max-content' }} pagination={{ pageSize: 30, hideOnSinglePage: true }}
          rowClassName={(r) => (r.id === id ? 'ant-table-row-selected' : '')}
          onRow={(r) => ({ onClick: () => setSearch({ id: r.id, tab: null }), style: { cursor: 'pointer' } })}
          columns={[
            { title: '表', dataIndex: 'name', render: (v: string, r) => <span>{v} <span style={{ opacity: 0.6, fontSize: 11 }}>{r.id}</span></span> },
            { title: '层', dataIndex: 'layer' }, { title: '域', dataIndex: 'domain_label' },
            { title: '健康', dataIndex: 'status', render: (h: keyof typeof HEALTH_STATUS) => <StatusTag s={HEALTH_STATUS[h]} text={HEALTH_LABEL[h]} /> },
            { title: '覆盖', key: 'c', render: (_, r) => <CoverageText row={r} /> },
          ]} />
      </Panel>
      {id && <TableDetail key={id} id={id} tab={q.get('tab')} />}
    </>
  )
}
