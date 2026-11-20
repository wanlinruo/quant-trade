import { Tooltip } from 'antd'
import { usePalette } from '../theme/context'

/** 数据缺失只显示“缺失”并说明原因，绝不补值（设计文档 §9，数据铁律）。 */
export function Missing({ why }: { why: string }) {
  const p = usePalette()
  return (
    <Tooltip title={why}>
      <span style={{ color: p.muted, borderBottom: `1px dashed ${p.muted}`, cursor: 'help' }}>缺失</span>
    </Tooltip>
  )
}
