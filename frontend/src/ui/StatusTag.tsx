import { usePalette } from '../theme/context'
import { statusColor, statusSymbol, statusText, type Status } from '../theme/status'

/** 状态一律“符号 + 文字”，不单靠颜色（设计文档 §7）。 */
export function StatusTag({ s, text }: { s: Status; text?: string }) {
  const p = usePalette()
  const fg = s === 'idle' || s === 'closed' ? p.muted : statusColor(p, s)
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, lineHeight: '18px', padding: '0 7px', borderRadius: 3, border: `1px solid ${fg}66`, color: fg, whiteSpace: 'nowrap' }}>
      {`${statusSymbol[s]} ${text ?? statusText[s]}`}
    </span>
  )
}
