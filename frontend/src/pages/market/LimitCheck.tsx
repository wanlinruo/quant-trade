import type { LimitCheck as LC } from '../../api/market'
import { usePalette } from '../../theme/context'

/** 涨跌停价核对（Tushare stk_limit × QMT 合约详情，D-19 / #460），信息与旧页一致。 */
export function LimitCheck({ check }: { check: LC | null }) {
  const p = usePalette()
  if (!check) return <div style={{ color: p.muted }}>今日核对结果未发布</div>
  const muted = { color: p.muted, fontSize: 12 }
  const split = check.missing_outside_roster !== undefined
  return (
    <div style={{ fontSize: 13, lineHeight: 1.8 }}>
      <div style={{ color: check.mismatched ? p.bad : p.text }}>
        {check.session} 核对 {check.checked ?? '未知'} 只：价格不一致 {check.mismatched ?? '未知'} 只
      </div>
      {check.samples?.map((s) => <div key={s.symbol} style={muted}>{s.symbol} Tushare {String(s.tushare)} / QMT {String(s.qmt)}</div>)}
      {!!check.unbounded_equivalent && <div style={muted}>无涨跌幅限制（两边哨兵值语义一致，不计入不一致）{check.unbounded_equivalent} 只</div>}
      {!!check.missing_in_qmt && split && (
        <>
          <div style={{ color: check.missing_in_roster?.length ? p.warn : p.muted }}>
            QMT 未覆盖 A 股 {check.missing_in_roster?.length ?? 0} 只{check.missing_in_roster?.length ? `：${check.missing_in_roster.join('、')}` : ''}
          </div>
          <div style={muted}>不在 A 股名录 {check.missing_outside_roster!.length} 只（Tushare stock_basic 未收录，不影响交易与研究链路）</div>
          {!check.missing_split_complete && <div style={{ color: p.warn }}>▲ 未覆盖共 {check.missing_in_qmt} 只，仅已列出的 {check.missing_samples?.length ?? 0} 只完成分类。</div>}
        </>
      )}
      {!!check.missing_in_qmt && !split && <div style={{ color: p.warn }}>▲ QMT 未覆盖 {check.missing_in_qmt} 只（合约详情无涨跌停价，是核对覆盖缺口，不是价格异常）</div>}
      {!!check.missing_samples?.length && (
        <details><summary style={muted}>查看未覆盖证券（{check.missing_samples.length} 只）</summary>
          {check.missing_samples.map((s) => <div key={s.symbol} style={muted}>{s.symbol} Tushare {String(s.tushare)}</div>)}
        </details>
      )}
    </div>
  )
}
