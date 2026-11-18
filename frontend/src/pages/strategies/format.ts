/** 回测行里的收益与回撤已是“百分数”（-1.0 表示 -1%），只做显示格式化。 */
export function pctPoints(v: number | null | undefined): string | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}
