import { useEffect, useMemo, useState } from 'react'
import { Input, Modal } from 'antd'
import { usePalette } from '../theme/context'
import { PAGES } from './nav'
import { fetchSearchIndex, type SearchItem } from '../api/search'

interface PaletteItem { kind: string; key: string; label: string; hint: string; page: string; params: Record<string, string> }

/** ⌘K 全局搜索：页面与只读业务索引分组展示。 */
export function SearchPalette({ open, recent, onClose, onPick }: { open: boolean; recent: string[]; onClose(): void; onPick(key: string, params?: Record<string, string>): void }) {
  const p = usePalette()
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  const [remote, setRemote] = useState<SearchItem[]>([])
  const [missing, setMissing] = useState<string[]>([])
  useEffect(() => {
    if (!open) return
    setQ(''); setI(0); setRemote([]); setMissing([])
    const ctl = new AbortController()
    fetchSearchIndex(ctl.signal).then((x) => { setRemote(x.items); setMissing(Object.values(x.missing)) }, () => setMissing(['业务索引不可访问']))
    return () => ctl.abort()
  }, [open])
  const pages: PaletteItem[] = PAGES.map((x) => ({ kind: '页面', key: x.key, label: x.label, hint: x.group, page: x.key, params: {} }))
  const items = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return recent.map((k) => pages.find((x) => x.key === k)).filter((x): x is PaletteItem => x !== undefined)
    return [...pages, ...remote].filter((x) => `${x.kind} ${x.label} ${x.hint} ${x.key}`.toLowerCase().includes(s)).slice(0, 80)
  }, [q, recent, remote])
  const pick = (x: PaletteItem) => { onPick(x.page, x.params); onClose() }
  return (
    <Modal open={open} onCancel={onClose} footer={null} closable={false} width={520} destroyOnHidden styles={{ body: { padding: 0 } }}>
      <Input autoFocus size="large" variant="borderless" placeholder="搜索页面…" value={q}
        onChange={(e) => { setQ(e.target.value); setI(0) }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setI((n) => Math.min(n + 1, items.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setI((n) => Math.max(n - 1, 0)) }
          else if (e.key === 'Enter' && items[i]) pick(items[i])
        }} />
      <div style={{ borderTop: `1px solid ${p.border}`, maxHeight: 360, overflow: 'auto', padding: 4 }}>
        <div style={{ color: p.muted, fontSize: 11, padding: '6px 10px' }}>{q.trim() ? '页面、委托、服务、策略、数据表、指标、告警、节点' : '最近访问'}</div>
        {missing.length > 0 && <div style={{ color: p.warn, fontSize: 11, padding: '2px 10px' }}>部分索引缺失：{missing.join('；')}</div>}
        {items.length === 0 && <div style={{ color: p.muted, padding: '8px 10px' }}>{q.trim() ? '没有匹配的页面' : '还没有访问记录'}</div>}
        {items.map((x, n) => (
          <div key={`${x.kind}-${x.key}`} role="option" aria-selected={n === i} onMouseEnter={() => setI(n)} onClick={() => pick(x)}
            style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 10px', borderRadius: 3, cursor: 'pointer', background: n === i ? 'rgba(127,127,127,.14)' : undefined }}>
            <span style={{ color: p.head }}><small style={{ color: p.muted, marginRight: 8 }}>{x.kind}</small>{x.label}</span><span style={{ color: p.muted, fontSize: 12 }}>{x.hint}</span>
          </div>
        ))}
      </div>
    </Modal>
  )
}
