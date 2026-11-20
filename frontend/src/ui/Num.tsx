import type { ReactNode } from 'react'

export const MONO = '"SF Mono", "JetBrains Mono", Menlo, Consolas, monospace'

export function Num({ children, color, size = 13, weight = 500 }: { children: ReactNode; color?: string; size?: number; weight?: number }) {
  return <span style={{ fontFamily: MONO, fontVariantNumeric: 'tabular-nums', color, fontSize: size, fontWeight: weight }}>{children}</span>
}
