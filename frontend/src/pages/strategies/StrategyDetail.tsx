import { useEffect, useState } from 'react'
import { Button, Input, Space, Table } from 'antd'
import { createStrategy, fetchStrategy, saveStrategy, type StrategyDetail as Detail } from '../../api/research'
import { useConfirmAction } from '../../page/ConfirmAction'
import { Panel } from '../../page/Panel'
import { goTo, setSearch } from '../../shell/router'
import { usePalette } from '../../theme/context'
import { MONO } from '../../ui/Num'
import { NewBacktestDialog } from './NewBacktestDialog'
import { pctPoints } from './format'

/** 策略详情：源码、参数、版本、回测记录。使用中的版本只读，只能另存为新策略（§6.3、§8.10）。 */
export function StrategyDetail({ id, onChanged }: { id: string; onChanged(): void }) {
  const p = usePalette()
  const { confirm, holder } = useConfirmAction()
  const [d, setD] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [source, setSource] = useState('')
  const [name, setName] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [newBt, setNewBt] = useState(false)

  useEffect(() => {
    const c = new AbortController()
    setD(null); setMsg(null)
    fetchStrategy(id, c.signal).then((x) => {
      if (!x.enabled) { setError('策略工作台未启用'); return }
      if (!Array.isArray(x.parameters) || typeof x.source !== 'string') { setError('策略接口格式不符'); return }
      setD(x); setSource(x.source); setName(x.name)
    }, (e: Error) => { if (e.name !== 'AbortError') setError(e.message) })
    return () => c.abort()
  }, [id])

  if (error) return <Panel title="策略详情"><div style={{ color: p.bad }}>{error}</div></Panel>
  if (!d) return <Panel title="策略详情"><div style={{ color: p.muted }}>加载中</div></Panel>

  const save = async () => {
    if (!(await confirm('保存策略', `原地保存「${d.name}」的草稿（版本 ${d.version_id.slice(0, 8)} 之后的修改）。`))) return
    try { await saveStrategy(d.id, name, source); setMsg({ ok: true, text: '已保存' }); onChanged() } catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
  }
  const saveAs = async () => {
    const newName = `${name} 副本`
    if (!(await confirm('另存为新策略', `以当前编辑内容新建策略「${newName}」，原策略与其正在使用的版本保持不变。`))) return
    try { const r = await createStrategy(newName, source); onChanged(); setSearch({ id: r.id }) } catch (e) { setMsg({ ok: false, text: (e as Error).message }) }
  }

  return (
    <>
      <Panel title={`策略「${d.name}」`} info="版本号 = 源码哈希前 16 位；回测与模拟按版本冻结源码，草稿修改不会改动已冻结的版本。"
        extra={<Space wrap>
          {!d.in_use && <Button size="small" type="primary" onClick={save}>保存</Button>}
          <Button size="small" onClick={saveAs}>另存为新策略</Button>
          <Button size="small" onClick={() => setNewBt(true)}>新建回测</Button>
        </Space>}>
        {d.in_use && <div style={{ color: p.warn, marginBottom: 8 }}>▲ 该版本正被{d.used_by.join('、')}使用，只读；修改请另存为新策略，再按 回测 → 模拟 → 实盘 推进。</div>}
        {msg && <div role="status" style={{ color: msg.ok ? p.ok : p.bad, marginBottom: 8 }}>{msg.text}</div>}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
          <span style={{ color: p.muted, fontSize: 12 }}>名称</span>
          <Input size="small" style={{ maxWidth: 280 }} value={name} readOnly={d.in_use} onChange={(e) => setName(e.target.value)} />
          <span style={{ color: p.muted, fontSize: 12 }}>版本 <span style={{ fontFamily: MONO }}>{d.version_id}</span></span>
        </div>
        <Input.TextArea aria-label="策略源码" value={source} readOnly={d.in_use} onChange={(e) => setSource(e.target.value)}
          autoSize={{ minRows: 12, maxRows: 32 }} spellCheck={false} style={{ fontFamily: MONO, fontSize: 12 }} />
      </Panel>
      <Panel title="有哪些参数？">
        {d.parameters.length === 0 ? <div style={{ color: p.muted }}>该策略没有声明参数</div> : (
          <Table size="small" pagination={false} rowKey="name" dataSource={d.parameters} scroll={{ x: 'max-content' }}
            columns={[
              { title: '名称', dataIndex: 'name' }, { title: '类型', dataIndex: 'type' },
              { title: '默认值', dataIndex: 'default', render: (v) => String(v) },
              { title: '范围', key: 'r', render: (_, s) => s.choices.length ? s.choices.join(' / ') : `${s.minimum ?? '—'} ~ ${s.maximum ?? '—'}` },
              { title: '说明', key: 'd', render: (_, s) => s.description || s.label },
            ]} />
        )}
      </Panel>
      <Panel title="跑过哪些回测？">
        {d.backtests.length === 0 ? <div style={{ color: p.muted }}>还没有回测</div> : (
          <Table size="small" pagination={{ pageSize: 10, hideOnSinglePage: true }} rowKey="id" dataSource={d.backtests} scroll={{ x: 'max-content' }}
            onRow={(r) => ({ onClick: () => goTo('backtest', { id: r.id }), style: { cursor: 'pointer' } })}
            columns={[
              { title: '回测', dataIndex: 'id', render: (v: string) => <span style={{ fontFamily: MONO }}>{v.slice(0, 8)}</span> },
              { title: '区间', key: 'r', render: (_, r) => `${r.start} ~ ${r.end}` },
              { title: '状态', dataIndex: 'status' },
              { title: '收益', dataIndex: 'return_pct', render: (v) => pctPoints(v) ?? '—' },
              { title: '最大回撤', dataIndex: 'max_drawdown_pct', render: (v) => pctPoints(v) ?? '—' },
            ]} />
        )}
        <div style={{ fontSize: 12, color: p.muted, marginTop: 6 }}>版本 {d.versions.length} 个：{d.versions.map((v) => v.id.slice(0, 8)).join('、') || '—'}</div>
      </Panel>
      <NewBacktestDialog open={newBt} onClose={() => setNewBt(false)} strategyId={d.id} strategyName={d.name} specs={d.parameters} />
      {holder}
    </>
  )
}
