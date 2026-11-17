import { Missing } from '../ui/Missing'
import { Num } from '../ui/Num'

/** 值为 null 时显示“缺失”与原因；0 是真实值，照常显示。 */
export function Value({ v, why, color, size }: { v: string | number | null | undefined; why: string; color?: string; size?: number }) {
  if (v === null || v === undefined) return <Missing why={why} />
  return <Num color={color} size={size}>{v}</Num>
}
