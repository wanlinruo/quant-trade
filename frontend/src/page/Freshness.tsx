import { useEffect, useState } from 'react'
import { Button, Space } from 'antd'
import { hms } from '../shell/time'
import { usePalette } from '../theme/context'

/** 数据时间与刷新控制。刷新失败时明确标出“显示的是上一次结果”，不把旧数据当新数据。 */
export function Freshness({ at, paused, onTogglePause, onRefresh, error }: { at: Date | null; paused: boolean; onTogglePause(): void; onRefresh(): void; error?: string | null }) {
  const p = usePalette()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  const ago = at ? Math.max(0, Math.round((now - at.getTime()) / 1000)) : null
  return (
    <Space size={8} wrap style={{ fontSize: 12, color: p.muted }}>
      {error && <span role="status" style={{ color: p.warn }}>▲ 刷新失败，显示的是上一次结果</span>}
      <span>{at ? `数据截至 ${hms(at)} · ${ago} 秒前` : '尚未取得数据'}</span>
      <Button size="small" onClick={onTogglePause}>{paused ? '继续刷新' : '暂停'}</Button>
      <Button size="small" onClick={onRefresh}>刷新</Button>
    </Space>
  )
}
