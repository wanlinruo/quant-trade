import { useState } from 'react'
import { Table, Tabs, Button, Form, InputNumber, Select, Tag, Space, Tooltip, App } from 'antd'
import { CodeOutlined, PlayCircleOutlined, BranchesOutlined } from '@ant-design/icons'
import type { Palette } from '../theme'
import { BTS, backtest, walk } from '../mock'
import { Num, Panel, Spark, StatusTag } from '../ui'
import { Hero, signed } from './common'

const STRATS = [
  { id: 's6', name: 'two_stage_limit_hold', desc: 'S6 两段式打板持有', ver: 'v7', sha: '7c1e0a42', upd: '11-14 21:03', bt: { ret: 112.2, sharpe: 1.21, dd: -9.0 }, stage: { bt: true, paper: 2, live: true }, seed: 61 },
  { id: 'lgb', name: 'lightgbm_price', desc: 'LightGBM 次日价格', ver: 'v2', sha: 'b20f6e19', upd: '10-08 18:40', bt: { ret: 6.1, sharpe: 0.41, dd: -14.9 }, stage: { bt: true, paper: 0, live: false }, seed: 62 },
  { id: 'nxo', name: 'next_open', desc: '次日开盘冲高', ver: 'v3', sha: '4e8d1c07', upd: '09-30 11:12', bt: { ret: -3.4, sharpe: -0.2, dd: -18.1 }, stage: { bt: true, paper: 0, live: false }, seed: 63 },
  { id: 'draft', name: 'sector_rotation', desc: '板块轮动（草稿）', ver: '草稿', sha: '—', upd: '11-15 22:47', bt: null, stage: { bt: false, paper: 0, live: false }, seed: 64 },
]
// 策略表的回测指标取该策略“主回测”（清单中第一份），与回测页同源
const MAIN = Object.fromEntries(['s6', 'lgb', 'nxo'].map((id) => { const b = backtest(BTS.find((x) => x.strat === id)!); return [id, { ret: +b.stats.total.toFixed(1), sharpe: b.stats.sharpe, dd: +b.stats.maxdd.toFixed(1), eq: b.equity }] }))
STRATS.forEach((s) => { if (MAIN[s.id]) s.bt = { ret: MAIN[s.id].ret, sharpe: MAIN[s.id].sharpe, dd: MAIN[s.id].dd } })
const openBt = (id: string) => { sessionStorage.setItem('qs-bt', id); dispatchEvent(new CustomEvent('qs-bt', { detail: id })); location.hash = 'backtest' }
type S = (typeof STRATS)[number]

const SRC = `from qsys.strategy.base import Strategy, Param

class TwoStageLimitHold(Strategy):
    """S6：竞价入档 → 开盘确认 → 持有至条件退出"""
    seal_ratio_min = Param(float, default=2.0, minimum=0.5, maximum=10, label="封单比下限")
    max_positions  = Param(int,   default=3,   minimum=1,   maximum=5,  label="最多持仓")
    exit_rule      = Param(str,   default="t+1_open", choices=["t+1_open", "t+1_close"], label="退出规则")

    def on_auction(self, ctx):
        pool = ctx.limit_up_first_board(include_st=True)   # ST 只认 stock_st 直取
        pool = [s for s in pool if not s.is_bj]            # 北交所禁开仓（#589）
        ...`

function Pipeline({ p, s }: { p: Palette; s: S }) {
  const step = (on: boolean, label: string, tip: string) => (
    <Tooltip title={tip}><span style={{ fontSize: 11, padding: '0 6px', borderRadius: 3, border: `1px solid ${on ? p.info + '88' : p.border}`, color: on ? p.info : p.muted }}>{label}</span></Tooltip>
  )
  return (
    <Space size={2} style={{ whiteSpace: 'nowrap' }}>
      {step(s.stage.bt, '回测', s.stage.bt ? '已有回测' : '尚未回测')}<span style={{ color: p.muted }}>→</span>
      {step(s.stage.paper > 0, `模拟${s.stage.paper ? ` ×${s.stage.paper}` : ''}`, `${s.stage.paper} 个模拟账户在用`)}<span style={{ color: p.muted }}>→</span>
      {s.stage.live ? <span style={{ fontSize: 11, padding: '0 6px', borderRadius: 3, border: `1px solid ${p.accent}`, color: p.accent }}>● 实盘</span> : step(false, '实盘', '未上实盘')}
    </Space>
  )
}

const camel = (n: string) => n.split('_').map((w) => w[0].toUpperCase() + w.slice(1)).join('')
const stub = (s: S) => s.id === 's6' ? SRC : s.id === 'draft'
  ? `# 草稿：尚未回测，不能进入模拟或实盘\nfrom qsys.strategy.base import Strategy, Param\n\nclass SectorRotation(Strategy):\n    top_n = Param(int, default=3, minimum=1, maximum=10, label="轮动板块数")\n    ...`
  : `from qsys.strategy.base import Strategy, Param\n\nclass ${camel(s.name)}(Strategy):\n    """${s.desc}（示意源码）"""\n    ...`

export function Strategies({ p }: { p: Palette }) {
  const [sel, setSel] = useState<S>(STRATS[0])
  const { message } = App.useApp()
  const live = sel.stage.live
  const src = stub(sel)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s="ok" icon={<BranchesOutlined />} title="4 个策略 · 1 个在实盘" sub="实盘：two_stage_limit_hold v7（源码 7c1e0a42，与冻结哈希一致 ✓）"
        kpis={[{ k: '在实盘', v: '1', c: p.accent, sub: '同时有模拟账户 ×2' }, { k: '仅回测', v: '2' }, { k: '草稿', v: '1', c: p.muted }]} />

      <Panel p={p} title="策略走到哪一步了？" info="每个策略从回测 → 模拟 → 实盘的推进位置；指标取最近一次完整回测。" bodyStyle={{ padding: '4px 0 0' }}>
        <Table size="small" pagination={false} rowKey="id" dataSource={STRATS}
          className="qs-click" onRow={(r) => ({ onClick: () => setSel(r) })} rowClassName={(r) => (r.id === sel.id ? 'qs-row-sel' : '')}
          columns={[
            { title: '策略', dataIndex: 'name', render: (v, r) => <div><Num color={p.head}>{v}</Num><div style={{ color: p.muted, fontSize: 12 }}>{r.desc}</div></div> },
            { title: '版本', dataIndex: 'ver', render: (v, r) => <span><Tag>{v}</Tag><Num color={p.muted} size={11}>{r.sha}</Num></span> },
            { title: '推进', render: (_, r) => <Pipeline p={p} s={r} /> },
            { title: '累计收益（最近回测）', align: 'right', render: (_, r) => (r.bt ? <Num color={r.bt.ret >= 0 ? p.up : p.down}>{signed(r.bt.ret, 1)}%</Num> : <span style={{ color: p.muted }}>—</span>) },
            { title: '夏普', align: 'right', render: (_, r) => <Num>{r.bt ? r.bt.sharpe.toFixed(2) : '—'}</Num> },
            { title: '最大回撤', align: 'right', render: (_, r) => (r.bt ? <Num color={p.down}>{r.bt.dd.toFixed(1)}%</Num> : '—') },
            { title: '回测净值', width: 140, render: (_, r) => (r.bt ? <Spark data={MAIN[r.id]?.eq.filter((_, i, a) => i % Math.ceil(a.length / 60) === 0) ?? walk(60, r.seed, 1, 0.02)} color={r.bt.ret >= 0 ? p.up : p.down} height={22} area={false} /> : null) },
            { title: '更新', dataIndex: 'upd', render: (v) => <Num color={p.muted}>{v}</Num> },
          ]} />
      </Panel>

      <Panel p={p} title={<><CodeOutlined /> {sel.name}</>} extra={<Space>{live && <Tag color="orange">实盘使用中 · {sel.ver} 只读</Tag>}<Button size="small" onClick={() => message.info(`原型：将另存为新版本，不改动正在使用的 ${sel.ver}`)}>保存为新版本</Button><Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => { location.hash = 'backtest'; message.success(`已带上 ${sel.name} ${sel.ver} 去新建回测（原型）`) }}>用此版本回测</Button></Space>}>
        <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 12 }}>
          <div style={{ border: `1px solid ${p.border}`, borderRadius: 4, background: p.bg, display: 'flex', fontFamily: 'Menlo, monospace', fontSize: 12, lineHeight: '19px', overflow: 'auto', maxHeight: 320 }}>
            <div style={{ padding: '8px 8px', color: p.muted, textAlign: 'right', userSelect: 'none', borderRight: `1px solid ${p.border}` }}>{src.split('\n').map((_, i) => <div key={i}>{i + 1}</div>)}</div>
            <pre style={{ margin: 0, padding: 8, color: p.text, whiteSpace: 'pre' }}>{src}</pre>
          </div>
          <Tabs size="small" items={[
            { key: 'p', label: '参数', children: (
              <Form layout="vertical" size="small" disabled={live}>
                <Form.Item label="封单比下限" extra="0.5 – 10，默认 2.0"><InputNumber defaultValue={2.0} min={0.5} max={10} step={0.1} style={{ width: '100%' }} /></Form.Item>
                <Form.Item label="最多持仓" extra="1 – 5，默认 3"><InputNumber defaultValue={3} min={1} max={5} style={{ width: '100%' }} /></Form.Item>
                <Form.Item label="退出规则"><Select defaultValue="t+1_open" options={[{ value: 't+1_open', label: 't+1_open' }, { value: 't+1_close', label: 't+1_close' }]} /></Form.Item>
                <div style={{ color: p.muted, fontSize: 12, lineHeight: '18px' }}>{live ? '实盘正在使用的版本参数只读；要调整请另存为新版本，再按 回测 → 模拟 → 实盘 推进。' : '修改后需另存为新版本才会生效。'}冻结阈值（S6_*）只读，不在此修改。</div>
              </Form>) },
            { key: 'v', label: '版本', children: (
              <Table size="small" pagination={false} rowKey="v" dataSource={[
                { v: 'v7', sha: '7c1e0a42', at: '11-14 21:03', use: '实盘 · 模拟×2' }, { v: 'v6', sha: '9e01c2aa', at: '10-29 20:11', use: '—' }, { v: 'v5', sha: '41bd0f73', at: '10-12 09:40', use: '—' },
              ]} columns={[{ title: '版本', dataIndex: 'v' }, { title: '源码哈希', dataIndex: 'sha', render: (v) => <Num size={12}>{v}</Num> }, { title: '创建', dataIndex: 'at', render: (v) => <Num size={12} color={p.muted}>{v}</Num> }, { title: '使用中', dataIndex: 'use' }]} />) },
            { key: 'r', label: '回测记录', children: (
              <Table size="small" pagination={false} rowKey="id" locale={{ emptyText: '还没有回测' }} dataSource={BTS.filter((b) => b.strat === sel.id).map((b) => ({ id: b.id, ret: +backtest(b).stats.total.toFixed(1), s: (b.warn ? 'warn' : 'ok') as 'ok' | 'warn', done: b.done }))}
              columns={[{ title: '回测', dataIndex: 'id', render: (v) => <a onClick={() => openBt(v)}>{v}</a> }, { title: '完成', dataIndex: 'done', render: (v) => <Num size={12} color={p.muted}>{v}</Num> }, { title: '收益', dataIndex: 'ret', align: 'right', render: (v: number) => <Num color={v >= 0 ? p.up : p.down}>{signed(v, 1)}%</Num> }, { title: '', dataIndex: 's', render: (s) => <StatusTag p={p} s={s} text={s === 'ok' ? '完成' : '有警告'} /> }]} />) },
          ]} />
        </div>
      </Panel>
    </div>
  )
}
