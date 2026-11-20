import type { Palette } from './palette'

export type Status = 'ok' | 'warn' | 'bad' | 'idle' | 'running' | 'closed'

export const statusSymbol: Record<Status, string> = { ok: '●', warn: '▲', bad: '✕', idle: '○', running: '◐', closed: '—' }

export const statusText: Record<Status, string> = { ok: '正常', warn: '警告', bad: '失败', idle: '待运行', running: '运行中', closed: '休市' }

export function statusColor(p: Palette, s: Status): string {
  switch (s) {
    case 'ok': return p.ok
    case 'warn': return p.warn
    case 'bad': return p.bad
    case 'running': return p.running
    case 'idle': return p.idle
    case 'closed': return p.muted
  }
}
