import { Table } from 'antd'
import type { MarketCoverage } from '../../api/market'
import { int } from '../../page/format'
import { usePalette } from '../../theme/context'

const NAME = { SH: '沪市', SZ: '深市', BJ: '北交所' } as const

function Unknown() { return <span>未知</span> }
function at(v: string | null) { return v ? <span>{v.replace('T', ' ')}</span> : <Unknown /> }

/** 覆盖信息只照实展示；没有绑定或应有数未知时写明，绝不显示 0（#588）。 */
export function CoverageTable({ coverage, hasSnapshot }: { coverage: MarketCoverage | null; hasSnapshot: boolean }) {
  const p = usePalette()
  if (!hasSnapshot) return <div style={{ color: p.muted }}>无实时快照，无法判断覆盖是否完整。</div>
  if (!coverage) return <div style={{ color: p.warn }}>▲ 生产者未绑定覆盖信息，无法判断覆盖是否完整。</div>
  return (
    <>
      <div style={{ color: coverage.status === 'complete' ? p.muted : p.warn, marginBottom: 8, fontSize: 12 }}>
        {coverage.status === 'complete' ? '请求集合已覆盖；完整数据质量仍须独立验收。' : '▲ 覆盖不完整或范围待核验，温度只统计已返回有效报价的标的。'}
      </div>
      <Table size="small" pagination={false} rowKey="market" scroll={{ x: 'max-content' }}
        dataSource={coverage.by_market}
        columns={[
          { title: '市场', dataIndex: 'market', render: (m: keyof typeof NAME) => NAME[m] ?? m },
          { title: '应有', dataIndex: 'expected', align: 'right', render: (v: number | null) => int(v) ?? <Unknown /> },
          { title: '有效', dataIndex: 'valid', align: 'right', render: (v: number) => int(v) },
          { title: '缺失', dataIndex: 'missing', align: 'right', render: (v: number) => int(v) },
          { title: '待核验', key: 'pending', align: 'right', render: (_, r) => int(r.invalid + r.other_session + r.suspended) },
          { title: '源时间', key: 'src', render: (_, r) => <span>{at(r.source_at_min)} 至 {at(r.source_at_max)}</span> },
        ]} />
    </>
  )
}
