import { useCallback, useEffect, useRef, useState } from 'react'

export type KillState = 'stopped' | 'clear' | 'unknown'

export interface KillSourceView { id: string; name: string; state: string; reason: string; recorded_at: string | null }

export interface ShellStatus {
  schema_version: 1
  checked_at: string
  kill: { state: KillState; label: string; message: string; sources: KillSourceView[] }
  live: { state: 'trading' | 'reduce_only' | 'unknown'; label: string }
  agent_heartbeat_at: string | null
  qmt: null; qmt_reason: string
  alerts_open: number | null; alerts_reason: string
}

export interface ShellResult { data: ShellStatus | null; error: string | null }

const URL_STATUS = '/api/shell/status'

/** 读取失败一律返回 data=null（前端按“未知”显示），绝不沿用上一次成功的结果。 */
export async function fetchShellStatus(signal?: AbortSignal): Promise<ShellResult> {
  try {
    const r = await fetch(URL_STATUS, { cache: 'no-store', signal })
    if (!r.ok) return { data: null, error: `状态接口返回 ${r.status}` }
    const body = await r.json()
    if (!body || body.schema_version !== 1 || !body.kill || !body.live) return { data: null, error: '状态接口格式不符' }
    return { data: body as ShellStatus, error: null }
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    return { data: null, error: '状态接口无法访问' }
  }
}

export function useShellStatus(intervalMs = 5000, paused = false): ShellResult & { refresh(): void } {
  const [res, setRes] = useState<ShellResult>({ data: null, error: null })
  const ctl = useRef<AbortController | null>(null)
  const refresh = useCallback(() => {
    ctl.current?.abort()
    const c = new AbortController()
    ctl.current = c
    fetchShellStatus(c.signal).then(setRes, () => {})
  }, [])
  useEffect(() => {
    if (paused) return
    refresh()
    const t = setInterval(refresh, intervalMs)
    return () => { clearInterval(t); ctl.current?.abort() }
  }, [intervalMs, paused, refresh])
  return { ...res, refresh }
}
