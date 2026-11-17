import { Button, Select, Slider } from 'antd'
import type { BoardData } from '../../api/board'
import { hms, parseLocal } from '../../shell/time'
import { setSearch } from '../../shell/router'
import { usePalette } from '../../theme/context'

/** 回放到某个快照时刻：URL 里 at 只存 HH:MM:SS，与 day 一起出现。 */
export function replayTo(day: string, iso: string): void {
  const t = parseLocal(iso)
  if (t) setSearch({ day, at: hms(t) })
}

/** 交易日选择、回看提示与盘中回放滑杆（11-11 §5.3 / 设计文档 §5.5）。 */
export function DayControls({ data, urlDay, urlAt }: { data: BoardData; urlDay: string | null; urlAt: string | null }) {
  const p = usePalette()
  const days = data.days ?? []
  const times = data.times ?? []
  const day = data.day ?? null
  const idx = Math.max(0, data.at ? times.indexOf(data.at) : times.length - 1)
  const canReplay = !!day && times.length > 1 && data.mode !== 'live'
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10, marginBottom: 8 }}>
      <Select size="small" style={{ width: 136 }} value={day ?? undefined} placeholder="交易日"
        options={days.map((d) => ({ value: d, label: d }))}
        onChange={(d: string) => setSearch({ day: d === days[0] ? null : d, at: null })} />
      {data.mode === 'history' && <span style={{ color: p.warn, fontSize: 12 }}>▲ 正在回看 · 只读</span>}
      {(urlDay || urlAt) && <Button size="small" onClick={() => setSearch({ day: null, at: null })}>回到今天</Button>}
      {canReplay && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '1 1 220px', minWidth: 0 }}>
          <span style={{ fontSize: 12, color: p.muted, whiteSpace: 'nowrap' }}>盘中回放</span>
          <Slider style={{ flex: 1, minWidth: 80 }} min={0} max={times.length - 1} value={idx}
            tooltip={{ formatter: (i) => (i === undefined ? '' : times[i]?.slice(11, 19)) }}
            onChangeComplete={(i: number) => replayTo(day!, times[i])} />
          <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{times[idx]?.slice(11, 19)}</span>
        </div>
      )}
    </div>
  )
}
