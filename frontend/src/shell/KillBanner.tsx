import type { ShellStatus } from '../api/shell'
import { usePalette } from '../theme/context'
import { Link } from './router'
import { hms, parseLocal } from './time'

/** 停机闸触发时常驻、不可关闭（设计文档 §5.2，#433 回归项）。只展示读到的事实，缺的写“未上报”。 */
export function KillBanner({ status }: { status: ShellStatus | null }) {
  const p = usePalette()
  if (status?.kill.state !== 'stopped') return null
  const stopped = status.kill.sources.filter((s) => s.state === 'stopped')
  const agent = status.kill.sources.find((s) => s.id === 'agent')
  const link = agent?.state === 'unknown' ? '链路：agent 心跳不可用' : '链路：agent 心跳正常'
  return (
    <div role="alert" className="qs-kill-banner"
      style={{ background: p.bad, color: '#fff', padding: '8px 18px', display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
      <b>✕ 停机闸已触发</b>
      {stopped.map((s) => {
        const at = parseLocal(s.recorded_at)
        return <span key={s.id}>{s.name} · {at ? hms(at) : '时间未上报'} · {s.reason}</span>
      })}
      <span>{status.kill.message}</span>
      <span>{link} · 停机闸不自动解除</span>
      <span style={{ flex: 1 }} />
      <Link to="recon" style={{ color: '#fff', textDecoration: 'underline' }}>查看解除前核对清单 →</Link>
    </div>
  )
}
