import type { ShellStatus } from '../api/shell'

export function shellStatus(kill: 'stopped' | 'clear' | 'unknown', over: Partial<ShellStatus> = {}): ShellStatus {
  const live = { stopped: ['reduce_only', '仅允许减仓'], clear: ['trading', '实盘运行'], unknown: ['unknown', '实盘状态未知'] }[kill]
  return {
    schema_version: 1,
    checked_at: '2026-11-17T10:00:00+08:00',
    kill: {
      state: kill,
      label: { stopped: '实盘已停机', clear: '停机闸已解除', unknown: '停机状态未知' }[kill],
      message: 'm',
      sources: [
        { id: 'linux', name: 'Linux 本地停机闸', state: kill, reason: kill === 'stopped' ? '对账不一致' : '未发现停机标记', recorded_at: kill === 'stopped' ? '2026-11-17T10:32:35+08:00' : null },
        { id: 'redis', name: 'Redis 实盘停机闸', state: kill === 'stopped' ? 'clear' : kill, reason: 'r', recorded_at: null },
        { id: 'agent', name: 'Windows 实盘 agent', state: kill === 'stopped' ? 'clear' : kill, reason: 'r', recorded_at: '2026-11-17T09:59:57+08:00' },
      ],
    },
    live: { state: live[0] as ShellStatus['live']['state'], label: live[1] },
    agent_heartbeat_at: '2026-11-17T09:59:57+08:00',
    qmt: null, alerts_open: null,
    missing: { qmt: 'QMT 客户端与执行器状态待计划 03/07 的结构化采集接入', alerts_open: '告警汇总待计划 04 接入' },
    ...over,
  }
}
