import { useCallback, useState } from 'react'
import { Button, Input, Modal, Result, Select, Table } from 'antd'
import { createStrategy, fetchStrategies, importTemplate, type StrategyList } from '../../api/research'
import { useConfirmAction } from '../../page/ConfirmAction'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { usePolling } from '../../page/usePolling'
import { setSearch, useSearch } from '../../shell/router'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { MONO } from '../../ui/Num'
import { StageSteps } from './StageSteps'
import { StrategyDetail } from './StrategyDetail'
import { signedPct } from '../../page/format'

const BLANK = 'from qsys.strategy.base import Strategy\n\nPARAMETERS = {}\n\n\nclass MyStrategy(Strategy):\n    pass\n'

export function StrategiesPage() {
  const p = usePalette()
  const q = useSearch()
  const id = q.get('id')
  const fetcher = useCallback((s: AbortSignal) => fetchStrategies(s), [])
  const poll = usePolling(fetcher, { intervalMs: 60_000, enabled: false })
  const { confirm, holder } = useConfirmAction()
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [tpl, setTpl] = useState<string | undefined>()
  const [err, setErr] = useState<string | null>(null)
  const d = poll.data as StrategyList | null

  if (!d) return poll.error ? <Hero s="bad" title="策略接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />
  if (!d.enabled) return <Result status="info" title="策略工作台未启用" subTitle="当前部署没有接入策略工作台。" />

  const count = (k: string) => d.strategies.filter((s) => s.stage === k).length
  const live = d.strategies.filter((s) => s.stage === 'live')
  // 原型：默认展开在实盘的策略（没有则第一个），URL 里指定 id 时以 URL 为准
  const shown = id && d.strategies.some((x) => x.id === id) ? id : (live[0] ?? d.strategies[0])?.id ?? null
  const create = async () => {
    if (!newName.trim()) { setErr('名称不能为空'); return }
    if (!(await confirm('新建策略', `新建空白策略「${newName.trim()}」。`))) return
    try { const r = await createStrategy(newName.trim(), BLANK); setCreating(false); setNewName(''); poll.refresh(); setSearch({ id: r.id }) } catch (e) { setErr((e as Error).message) }
  }
  const doImport = async () => {
    if (!tpl) return
    if (!(await confirm('从模板导入', `从仓库模板 ${tpl} 新建一个策略。`))) return
    try { const r = await importTemplate(tpl); setTpl(undefined); poll.refresh(); setSearch({ id: r.id }) } catch (e) { setErr((e as Error).message) }
  }

  return (
    <>
      <Hero s={d.strategies.length ? 'ok' : 'idle'}
        title={`${d.strategies.length} 个策略 · ${count('live')} 个在${d.core_label}`}
        subtitle={live.length ? `${d.core_label}：${live.map((s) => `${s.name}（${s.version_id.slice(0, 8)}）`).join('、')}` : '推进位置：部署承袭 → 未结束的模拟账户 → 成功回测 → 草稿，命中即停。'}
        kpis={[
          { k: `在${d.core_label}`, v: count('live'), color: count('live') ? p.ok : undefined },
          { k: '模拟中', v: count('paper') },
          { k: '仅回测', v: count('backtest') },
          { k: '草稿', v: count('draft') },
        ]} />
      <Panel title="有哪些策略、走到哪一步？" info="最近回测指标与回测页同一行数据（§9.5）。实盘或模拟正在使用的版本只读。"
        extra={<div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, maxWidth: '100%', minWidth: 0 }}>
          <Button size="small" onClick={() => { setCreating(true); setErr(null) }}>新建策略</Button>
          <Select size="small" style={{ flex: '1 1 120px', width: 180, maxWidth: '100%', minWidth: 0 }} placeholder="从模板导入" value={tpl} onChange={setTpl} options={d.templates.map((t) => ({ value: t, label: t }))} />
          <Button size="small" disabled={!tpl} onClick={doImport}>导入</Button>
        </div>}>
        {err && <div role="alert" style={{ color: p.bad, marginBottom: 8 }}>{err}</div>}
        <Table size="small" rowKey="id" dataSource={d.strategies} pagination={{ pageSize: 20, hideOnSinglePage: true }} scroll={{ x: 'max-content' }}
          rowClassName={(r) => (r.id === shown ? 'ant-table-row-selected' : '')}
          onRow={(r) => ({ onClick: () => setSearch({ id: r.id }), style: { cursor: 'pointer' } })}
          columns={[
            { title: '名称', dataIndex: 'name', render: (v: string, r) => <span>{v}{r.in_use && <span style={{ color: p.warn, fontSize: 11, marginLeft: 6 }}>使用中</span>}</span> },
            { title: '版本', dataIndex: 'version_id', render: (v: string) => <span style={{ fontFamily: MONO }}>{v.slice(0, 8)}</span> },
            { title: '推进位置', key: 's', render: (_, r) => <span title={r.stage_detail}><StageSteps stage={r.stage} coreLabel={d.core_label} /></span> },
            // 原型的夏普与回测净值迷你图：列表接口暂无该事实，不在前端推算（#705）
            { title: '累计收益（最近回测）', key: 'ret', align: 'right', render: (_, r) => !r.latest_backtest ? <span style={{ color: p.muted }}>无回测</span>
              : r.latest_backtest.total_return == null ? <Missing why="回测报告没有累计收益" />
                : <span style={{ fontFamily: MONO, color: r.latest_backtest.total_return >= 0 ? p.up : p.down }}>{signedPct(r.latest_backtest.total_return)}</span> },
            { title: '基准', key: 'bench', align: 'right', render: (_, r) => r.latest_backtest?.benchmark_return == null ? <span style={{ color: p.muted }}>—</span>
              : <span style={{ fontFamily: MONO, color: p.muted }}>{signedPct(r.latest_backtest.benchmark_return)}</span> },
            { title: '最大回撤', key: 'dd', align: 'right', render: (_, r) => r.latest_backtest?.max_drawdown == null ? <span style={{ color: p.muted }}>—</span>
              : <span style={{ fontFamily: MONO }}>{signedPct(r.latest_backtest.max_drawdown)}</span> },
            { title: '更新时间', dataIndex: 'updated_at', render: (v: string | null) => dateTime(v) },
          ]} />
      </Panel>
      {shown && <StrategyDetail key={shown} id={shown} onChanged={poll.refresh} />}
      <Modal open={creating} title="新建策略" okText="创建" onOk={create} onCancel={() => setCreating(false)} destroyOnHidden>
        <Input aria-label="策略名称" placeholder="策略名称" value={newName} onChange={(e) => setNewName(e.target.value)} />
      </Modal>
      {holder}
    </>
  )
}
