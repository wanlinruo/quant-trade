import { StatusTag } from '../../ui/StatusTag'

export type OpsState = 'ok' | 'warning' | 'failed' | 'running' | 'fixed' | 'paused' | 'unknown' | 'not_due'
const labels: Record<OpsState, string> = { ok: '正常', warning: '警告', failed: '失败', running: '运行中', fixed: '已修复', paused: '已暂停', unknown: '未知', not_due: '未到期' }
export const stateTag = (s: OpsState) => <StatusTag s={s === 'ok' || s === 'fixed' ? 'ok' : s === 'warning' ? 'warn' : s === 'failed' ? 'bad' : s === 'running' ? 'running' : 'idle'} text={labels[s]} />
export const bytes = (v: number | null) => v == null ? '—' : `${(v / 2 ** 30).toFixed(1)} GiB`
