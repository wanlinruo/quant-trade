import { Button, Space, Tooltip } from 'antd'
import { CopyOutlined, DownOutlined, UpOutlined } from '@ant-design/icons'
import { useEffect } from 'react'

export function DrawerNav<T>({ rows, current, identify, onPick, copyText }: {
  rows: T[]; current: T; identify(row: T): string; onPick(row: T): void; copyText?: string
}) {
  const index = rows.findIndex((x) => identify(x) === identify(current))
  const pick = (delta: number) => { const row = rows[index + delta]; if (row) onPick(row) }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target
      if (target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"]')) return
      if (e.key === 'ArrowUp' || e.key.toLowerCase() === 'k') { e.preventDefault(); pick(-1) }
      if (e.key === 'ArrowDown' || e.key.toLowerCase() === 'j') { e.preventDefault(); pick(1) }
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [index, rows, current, identify, onPick])
  return <Space size={4}>
    <Tooltip title="上一条（↑ / K）"><Button size="small" aria-label="上一条" icon={<UpOutlined />} disabled={index <= 0} onClick={() => pick(-1)} /></Tooltip>
    <Tooltip title="下一条（↓ / J）"><Button size="small" aria-label="下一条" icon={<DownOutlined />} disabled={index < 0 || index >= rows.length - 1} onClick={() => pick(1)} /></Tooltip>
    {copyText && <Tooltip title="复制编号"><Button size="small" aria-label="复制编号" icon={<CopyOutlined />} onClick={() => navigator.clipboard.writeText(copyText)} /></Tooltip>}
  </Space>
}
