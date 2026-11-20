import { useEffect, useMemo, useState } from 'react'
import { Input, Modal } from 'antd'
import { usePalette } from '../theme/context'
import { PAGES } from './nav'

/** ⌘K 搜索。本计划只收页面；委托、服务、数据表等类型随对应页面的计划接入。 */
export function SearchPalette({ open, recent, onClose, onPick }: { open: boolean; recent: string[]; onClose(): void; onPick(key: string): void }) {
  const p = usePalette()
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  useEffect(() => { if (open) { setQ(''); setI(0) } }, [open])
  const items = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return recent.map((k) => PAGES.find((x) => x.key === k)).filter((x) => x !== undefined)
    return PAGES.filter((x) => `${x.label} ${x.kw} ${x.key} ${x.group}`.toLowerCase().includes(s))
  }, [q, recent])
  const pick = (k: string) => { onPick(k); onClose() }
  return (
    <Modal open={open} onCancel={onClose} footer={null} closable={false} width={520} destroyOnHidden styles={{ body: { padding: 0 } }}>
      <Input autoFocus size="large" variant="borderless" placeholder="搜索页面…" value={q}
        onChange={(e) => { setQ(e.target.value); setI(0) }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setI((n) => Math.min(n + 1, items.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setI((n) => Math.max(n - 1, 0)) }
          else if (e.key === 'Enter' && items[i]) pick(items[i].key)
        }} />
      <div style={{ borderTop: `1px solid ${p.border}`, maxHeight: 360, overflow: 'auto', padding: 4 }}>
        <div style={{ color: p.muted, fontSize: 11, padding: '6px 10px' }}>{q.trim() ? '页面' : '最近访问'}</div>
        {items.length === 0 && <div style={{ color: p.muted, padding: '8px 10px' }}>{q.trim() ? '没有匹配的页面' : '还没有访问记录'}</div>}
        {items.map((x, n) => (
          <div key={x.key} role="option" aria-selected={n === i} onMouseEnter={() => setI(n)} onClick={() => pick(x.key)}
            style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 10px', borderRadius: 3, cursor: 'pointer', background: n === i ? 'rgba(127,127,127,.14)' : undefined }}>
            <span style={{ color: p.head }}>{x.label}</span><span style={{ color: p.muted, fontSize: 12 }}>{x.group}</span>
          </div>
        ))}
      </div>
    </Modal>
  )
}
