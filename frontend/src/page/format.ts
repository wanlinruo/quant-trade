const ok = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)

/** 比例转百分比：0.623 → "62.3%"。 */
export function pct(v: number | null | undefined, digits = 1): string | null {
  return ok(v) ? `${(v * 100).toFixed(digits)}%` : null
}

/** 带符号百分比：0.0123 → "+1.23%"。 */
export function signedPct(v: number | null | undefined, digits = 2): string | null {
  if (!ok(v)) return null
  const s = (v * 100).toFixed(digits)
  return v > 0 ? `+${s}%` : `${s}%`
}

/** 元 → 亿元（取整）。 */
export function yi(v: number | null | undefined): string | null {
  return ok(v) ? `${Math.round(v / 1e8)} 亿` : null
}

/** 整数千分位。 */
export function int(v: number | null | undefined): string | null {
  return ok(v) ? Math.round(v).toLocaleString('en-US') : null
}
