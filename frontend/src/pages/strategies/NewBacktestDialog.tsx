import { useState } from 'react'
import { Checkbox, Input, InputNumber, Modal, Select, Switch } from 'antd'
import { createBacktest, type ParameterSpec } from '../../api/research'
import { useConfirmAction } from '../../page/ConfirmAction'
import { goTo } from '../../shell/router'
import { usePalette } from '../../theme/context'

const DAY = /^\d{4}-\d{2}-\d{2}$/

function ParamInput({ spec, value, onChange }: { spec: ParameterSpec; value: unknown; onChange(v: unknown): void }) {
  const label = spec.name
  if (spec.choices.length) return <Select aria-label={label} style={{ width: '100%' }} value={value as string} onChange={onChange} options={spec.choices.map((c) => ({ value: c as string, label: String(c) }))} />
  if (spec.type === 'bool') return <Switch aria-label={label} checked={!!value} onChange={onChange} />
  if (spec.type === 'int' || spec.type === 'float') {
    return <InputNumber aria-label={label} style={{ width: '100%' }} value={value as number} precision={spec.type === 'int' ? 0 : undefined}
      min={spec.minimum ?? undefined} max={spec.maximum ?? undefined} onChange={(v) => onChange(v)} />
  }
  return <Input aria-label={label} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />
}

/** 新建回测：参数按策略声明的 ParameterSpec 生成；提交前确认，成功后跳到回测页。 */
export function NewBacktestDialog({ open, onClose, strategyId, strategyName, specs }: { open: boolean; onClose(): void; strategyId: string; strategyName: string; specs: ParameterSpec[] }) {
  const p = usePalette()
  const { confirm, holder } = useConfirmAction()
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [cash, setCash] = useState<number | null>(100000)
  const [entry, setEntry] = useState<'open' | 'continuous'>('continuous')
  const [jq, setJq] = useState(false)
  const [params, setParams] = useState<Record<string, unknown>>(() => Object.fromEntries(specs.map((s) => [s.name, s.default])))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!DAY.test(start) || !DAY.test(end) || start > end) { setError('请填写有效的起止日期（YYYY-MM-DD，开始不晚于结束）'); return }
    if (!cash || cash <= 0) { setError('初始资金必须大于 0'); return }
    setError(null)
    if (!(await confirm('提交回测', `将为「${strategyName}」排队一次回测：${start} ~ ${end}，初始资金 ${cash}。`))) return
    setBusy(true)
    try {
      const r = await createBacktest({ strategy_id: strategyId, start, end, cash, params, entry_fill: entry, jq_compat: jq })
      onClose()
      goTo('backtest', { id: r.id })
    } catch (e) {
      setError((e as Error).message)
    } finally { setBusy(false) }
  }

  const row = { display: 'grid', gridTemplateColumns: '96px minmax(0, 1fr)', gap: 8, alignItems: 'center', marginBottom: 8 }
  return (
    <>
      <Modal open={open} title={`新建回测 · ${strategyName}`} onCancel={onClose} okText="提交回测" onOk={submit} confirmLoading={busy} destroyOnHidden>
        <div style={row}><label htmlFor="bt-start">开始日期</label><Input id="bt-start" aria-label="开始日期" placeholder="YYYY-MM-DD" value={start} onChange={(e) => setStart(e.target.value)} /></div>
        <div style={row}><label htmlFor="bt-end">结束日期</label><Input id="bt-end" aria-label="结束日期" placeholder="YYYY-MM-DD" value={end} onChange={(e) => setEnd(e.target.value)} /></div>
        <div style={row}><span>初始资金</span><InputNumber aria-label="初始资金" style={{ width: '100%' }} min={1} value={cash} onChange={setCash} /></div>
        <div style={row}><span>竞价入场</span>
          <Select aria-label="竞价入场口径" value={entry} onChange={setEntry} options={[{ value: 'continuous', label: '连续竞价判定（09:31 分钟线）' }, { value: 'open', label: '开盘价成交（原口径）' }]} />
        </div>
        <div style={row}><span>费用模型</span><Checkbox checked={jq} onChange={(e) => setJq(e.target.checked)}>聚宽兼容（不代表实盘档位）</Checkbox></div>
        {specs.length > 0 && <div style={{ margin: '12px 0 6px', color: p.muted, fontSize: 12 }}>策略参数</div>}
        {specs.map((s) => (
          <div key={s.name} style={row} title={s.description}>
            <span>{s.label || s.name}</span>
            <ParamInput spec={s} value={params[s.name]} onChange={(v) => setParams((x) => ({ ...x, [s.name]: v }))} />
          </div>
        ))}
        {error && <div role="alert" style={{ color: p.bad }}>{error}</div>}
      </Modal>
      {holder}
    </>
  )
}
