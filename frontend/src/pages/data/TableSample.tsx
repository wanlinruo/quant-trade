import { useEffect, useState } from 'react'
import { SAMPLE_REASON_TEXT } from './labels'
import { Table } from 'antd'
import { fetchSample, type TableSample as Sample } from '../../api/data'
import { usePalette } from '../../theme/context'
import { MONO } from '../../ui/Num'

/** 表样本：只读抽样，只在切到“样本”标签时请求；null 原样显示为灰色 null。 */
export function TableSample({ id }: { id: string }) {
  const p = usePalette()
  const [s, setS] = useState<{ data: Sample | null; error: string | null }>({ data: null, error: null })
  useEffect(() => {
    const c = new AbortController()
    fetchSample(id, c.signal).then((data) => setS({ data, error: null }), (e: Error) => { if (e.name !== 'AbortError') setS({ data: null, error: e.message }) })
    return () => c.abort()
  }, [id])
  if (s.error) return <div style={{ color: p.bad }}>{s.error}</div>
  if (!s.data) return <div style={{ color: p.muted }}>加载中</div>
  if (!s.data.enabled) return <div style={{ color: p.muted }}>数据资产未启用</div>
  const d = s.data
  if (!d.available) return <div style={{ color: p.muted }}>{SAMPLE_REASON_TEXT[d.reason]}</div>
  return (
    <>
      <div style={{ fontSize: 12, color: p.muted, marginBottom: 8 }}>
        最新分区最后一个行组的前 {d.limit ?? 20} 行，只读抽样，不代表全表（文件 {d.file}，行组 {d.row_group}）。{SAMPLE_REASON_TEXT[d.reason]}
      </div>
      <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={d.rows} scroll={{ x: 'max-content' }}
        columns={d.columns.map((c) => ({
          title: c, dataIndex: c,
          render: (v: unknown) => v === null || v === undefined
            ? <span style={{ color: p.muted, fontFamily: MONO }}>null</span>
            : <span style={{ fontFamily: MONO }}>{String(v)}</span>,
        }))} />
    </>
  )
}
