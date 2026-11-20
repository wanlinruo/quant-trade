import type { StrategyRow } from '../../api/research'
import { usePalette } from '../../theme/context'

/** 推进位置：草稿 → 回测 → 模拟 → 实盘/影子盘，当前段高亮（aria-current="step"）。 */
export function StageSteps({ stage, coreLabel }: { stage: StrategyRow['stage']; coreLabel: string }) {
  const p = usePalette()
  const steps: [StrategyRow['stage'], string][] = [['draft', '草稿'], ['backtest', '回测'], ['paper', '模拟'], ['live', coreLabel]]
  const at = steps.findIndex(([k]) => k === stage)
  return (
    <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', fontSize: 12 }}>
      {steps.map(([k, label], i) => (
        <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
          {i > 0 && <span style={{ color: p.muted }}>→</span>}
          <span aria-current={i === at ? 'step' : undefined}
            style={{ padding: '0 5px', borderRadius: 3, color: i === at ? p.head : i < at ? p.text : p.muted,
              background: i === at ? `${p.accent}33` : 'transparent', fontWeight: i === at ? 600 : 400 }}>{label}</span>
        </span>
      ))}
    </span>
  )
}
