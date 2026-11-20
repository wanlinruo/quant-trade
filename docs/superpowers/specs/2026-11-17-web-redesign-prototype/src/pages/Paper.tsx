import { useMemo, useState } from 'react'
import { Table, Tabs, Dropdown, Button, Modal, Empty, Segmented } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, MoreOutlined } from '@ant-design/icons'
import type { Palette } from '../theme'
import type { Status } from '../mock'
import { EChart, Num, Panel, Spark, StatusTag, axisStyle, tooltipStyle } from '../ui'
import { Hero, signed } from './common'
import { PM, BAD } from '../scene'
import { paperAccounts, paperBook, paperCurve, runPoints } from './tradingMock'

const ACCS = paperAccounts.map((a) => {
  const c = paperCurve(a.seed)
  return { ...a, ret: +((c.nav.at(-1)! - 1) * 100).toFixed(2), dd: Math.min(...c.dd) }
})
type Acc = (typeof ACCS)[number]

export function Paper({ p }: { p: Palette }) {
  const [sel, setSel] = useState<Acc>(ACCS[0])
  const book = useMemo(() => paperBook(sel.id), [sel])
  const [confirm, setConfirm] = useState<string | null>(null)
  const [confirmKind, setConfirmKind] = useState<'toggle' | 'recover'>('toggle')
  const [range, setRange] = useState<number>(40)
  const c = useMemo(() => { const f = paperCurve(sel.seed); const k = f.days.length - range; return { days: f.days.slice(k), nav: f.nav.slice(k), bt: f.bt.slice(k), dd: f.dd.slice(k) } }, [sel, range])

  const navOpt = useMemo(() => ({
    animation: false, grid: { left: 50, right: 16, top: 26, bottom: 22 },
    legend: { top: 0, right: 0, textStyle: { color: p.muted, fontSize: 11 }, itemWidth: 14, itemHeight: 2 },
    tooltip: { ...tooltipStyle(p), trigger: 'axis', formatter: (qs: { dataIndex: number; axisValue: string }[]) => {
      const i = qs[0].dataIndex; const gap = (c.nav[i] - c.bt[i]) * 10000
      return `<b>${qs[0].axisValue}</b><br/>模拟净值　${c.nav[i].toFixed(4)}<br/>来源回测　${c.bt[i].toFixed(4)}<br/>偏离　<b style="color:${Math.abs(gap) > 30 ? p.warn : p.text}">${gap >= 0 ? '+' : ''}${gap.toFixed(0)} bp</b>`
    } },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    xAxis: { type: 'category', data: c.days, ...axisStyle(p), axisLabel: { show: false }, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => v.toFixed(3) } },
    series: [
      { name: '模拟净值', type: 'line', data: c.nav, symbol: 'none', lineStyle: { color: p.line, width: 2 }, itemStyle: { color: p.line } },
      { name: '来源回测同区间', type: 'line', data: c.bt, symbol: 'none', lineStyle: { color: p.muted, width: 1.2, type: 'dashed' }, itemStyle: { color: p.muted } },
    ],
  }), [p, c])
  const ddOpt = useMemo(() => ({
    animation: false, grid: { left: 50, right: 16, top: 6, bottom: 22 },
    tooltip: { ...tooltipStyle(p), trigger: 'axis', valueFormatter: (v: number) => `${v}%` },
    xAxis: { type: 'category', data: c.days, ...axisStyle(p), splitLine: { show: false } },
    yAxis: { type: 'value', max: 0, splitNumber: 2, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: '{value}%' } },
    series: [{ name: '回撤', type: 'line', data: c.dd, symbol: 'none', lineStyle: { color: p.down, width: 1 }, areaStyle: { color: p.down, opacity: 0.25 } }],
  }), [p, c])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s={BAD ? 'bad' : 'ok'} icon={BAD ? <CloseCircleFilled /> : <CheckCircleFilled />} title={BAD ? '2 个模拟账户出错 · 已停止撮合' : PM ? '2 个模拟账户运行中 · 今日已结算' : '2 个模拟账户运行中'} sub={BAD ? '10:31:20 Redis 隧道断连，Paper worker 停止撮合并把运行中的账户标为出错；隧道 10:36:40 已恢复，但账户不会自动恢复，要走备份回读与精确状态转换' : PM ? '已收盘 · Paper worker ● 正常 · 今日事件 7 / 7 按时 · 收盘结算已完成' : '行情源 ● 正常 · Paper worker ● 正常 · 今日已到点事件 4 / 4 按时'}
        kpis={[{ k: '运行中', v: BAD ? '0' : '2', c: BAD ? p.muted : p.ok }, { k: '已停用', v: '1' }, { k: '出错', v: BAD ? '2' : '0', c: BAD ? p.bad : p.ok, sub: BAD ? '10:31:20 起' : undefined }, { k: '最好 / 最差收益', v: `${signed(Math.max(...ACCS.map((a) => a.ret)))}% / ${signed(Math.min(...ACCS.map((a) => a.ret)))}%` }]} />

      <Panel p={p} title="账户列表" info="收益率是模拟账户自身口径（无入出金）。点击行切换下方详情。" bodyStyle={{ padding: '4px 0 0' }}>
        <Table size="small" pagination={false} rowKey="id" dataSource={ACCS}
          className="qs-click" onRow={(r) => ({ onClick: () => setSel(r) })} rowClassName={(r) => (r.id === sel.id ? 'qs-row-sel' : '')}
          columns={[
            { title: '账户', dataIndex: 'name', render: (v) => <span style={{ color: p.head }}>{v}</span> },
            { title: '策略 / 版本', render: (_, r) => <span style={{ color: p.muted }}>{r.strat} · {r.ver}</span> },
            { title: '状态', dataIndex: 's', width: 100, render: (s: Status) => <StatusTag s={s} p={p} text={s === 'closed' ? '已停用' : s === 'bad' ? '出错' : undefined} /> },
            { title: '收益', dataIndex: 'ret', align: 'right', sorter: (a, b) => a.ret - b.ret, render: (v: number) => <Num color={v >= 0 ? p.up : p.down}>{signed(v)}%</Num> },
            { title: '最大回撤', dataIndex: 'dd', align: 'right', render: (v: number) => <Num color={p.down}>{v.toFixed(2)}%</Num> },
            { title: '持仓', dataIndex: 'n', align: 'right', render: (v) => <Num>{v}</Num> },
            { title: '近 40 日净值', width: 150, render: (_, r) => <Spark data={paperCurve(r.seed).nav} color={r.ret >= 0 ? p.up : p.down} height={22} area={false} /> },
            { title: '启用日', dataIndex: 'since', render: (v) => <Num color={p.muted}>{v}</Num> },
            { title: '', width: 40, render: (_, r) => (
              <Dropdown trigger={['click']} menu={{ items: [{ key: 'x', label: r.s === 'running' ? '停用账户…' : r.s === 'bad' ? '恢复账户…' : '启用账户…', danger: r.s === 'running' }], onClick: () => { setConfirmKind(r.s === 'bad' ? 'recover' : 'toggle'); setConfirm(r.name) } }}>
                <Button size="small" type="text" icon={<MoreOutlined />} onClick={(e) => e.stopPropagation()} />
              </Dropdown>) },
          ]} />
      </Panel>

      <Panel p={p} title={`${sel.name}：净值和来源回测对得上吗？`} info="同区间叠加：实线＝模拟净值，虚线＝来源回测；两图共用十字光标，提示框给出偏离（bp，超过 30bp 标黄）。偏离持续扩大说明成交假设或数据有差异。"
        extra={<Segmented size="small" value={range} onChange={(v) => setRange(v as number)} options={[{ label: '近 10 日', value: 10 }, { label: '近 20 日', value: 20 }, { label: '近 40 日', value: 40 }]} />}>
        <EChart height={220} option={navOpt} group="paper" />
        <EChart height={90} option={ddOpt} group="paper" />
      </Panel>

      <Panel p={p} title="账户明细" bodyStyle={{ padding: '0 12px 8px' }}>
        <Tabs size="small" items={[
          { key: 'pos', label: `持仓 ${book.positions.length}`, children: book.positions.length ? <Table size="small" pagination={false} rowKey="code" dataSource={book.positions}
            columns={[{ title: '代码', dataIndex: 'code', render: (v) => <Num>{v}</Num> }, { title: '持仓', dataIndex: 'qty', align: 'right' }, { title: '可用', dataIndex: 'avail', align: 'right' },
              { title: '成本', dataIndex: 'cost', align: 'right', render: (v: number) => <Num>{v.toFixed(2)}</Num> }, { title: '现价', dataIndex: 'last', align: 'right', render: (v: number) => <Num>{v.toFixed(2)}</Num> },
              { title: '浮动盈亏', align: 'right', render: (_, r) => { const v = (r.last - r.cost) * r.qty; return <Num color={v >= 0 ? p.up : p.down}>{signed(v)}</Num> } }]} /> : <Empty description={sel.s === 'closed' ? '账户已停用，持仓已清空' : '无持仓'} /> },
          { key: 'ord', label: `委托 ${book.orders.length}`, children: book.orders.length ? <Table size="small" pagination={false} rowKey="id" dataSource={book.orders}
            columns={[{ title: '时间', dataIndex: 'at', render: (v) => <Num>{v}</Num> }, { title: '代码', dataIndex: 'code', render: (v) => <Num>{v}</Num> },
              { title: '方向', dataIndex: 'side', render: (v) => <span style={{ color: v === '买' ? p.up : p.down }}>{v}</span> }, { title: '限价', dataIndex: 'px', align: 'right', render: (v: number) => <Num>{v.toFixed(2)}</Num> },
              { title: '数量', dataIndex: 'qty', align: 'right' }, { title: '模拟委托号', dataIndex: 'id', render: (v) => <Num size={12} color={p.muted}>{v}</Num> },
              { title: '结果', render: (_, o) => <StatusTag s={o.status} p={p} text={o.statusText} /> }]} /> : <Empty description="今日无委托" /> },
          { key: 'fill', label: `成交 ${book.fills.length}`, children: book.fills.length ? <>
            <Table size="small" pagination={false} rowKey="id" dataSource={book.fills}
              columns={[{ title: '时间', dataIndex: 'at', render: (v) => <Num>{v}</Num> }, { title: '代码', dataIndex: 'code', render: (v) => <Num>{v}</Num> },
                { title: '方向', dataIndex: 'side', render: (v) => <span style={{ color: v === '买' ? p.up : p.down }}>{v}</span> }, { title: '数量', dataIndex: 'qty', align: 'right' },
                { title: '参考价', dataIndex: 'ref', align: 'right', render: (v: number) => <Num color={p.muted}>{v.toFixed(2)}</Num> },
                { title: '模拟成交价', dataIndex: 'px', align: 'right', render: (v: number) => <Num>{v.toFixed(2)}</Num> },
                { title: '滑点', dataIndex: 'bp', align: 'right', render: (v: number) => <Num color={Math.abs(v) > 10 ? p.warn : p.muted}>{v > 0 ? '+' : ''}{v} bp</Num> },
                { title: '撮合口径', dataIndex: 'rule', render: (v: string) => <span style={{ color: v.includes('已撤') ? p.warn : p.muted, fontSize: 12 }}>{v}</span> }]} />
            <div style={{ color: p.muted, fontSize: 12, marginTop: 6 }}>参考价＝信号生成时的委托限价；滑点＝(模拟成交价 − 参考价) ÷ 参考价，卖出为负表示少卖。黄色口径说明模拟与实盘结果不同，这是净值偏离的主要来源之一。</div>
          </> : <Empty description="今日无成交" /> },
          { key: 'rej', label: `拒单 ${book.rejects.length}`, children: book.rejects.length ? <Table size="small" pagination={false} rowKey="at" dataSource={book.rejects}
            columns={[{ title: '时间', dataIndex: 'at', render: (v) => <Num>{v}</Num> }, { title: '代码', dataIndex: 'code', render: (v) => <Num>{v}</Num> }, { title: '数量', dataIndex: 'qty', align: 'right' },
              { title: '规则', dataIndex: 'rule', render: (v) => <StatusTag s="bad" p={p} text={v} /> }, { title: '说明', dataIndex: 'detail', render: (v) => <span style={{ color: p.muted }}>{v}</span> },
              { title: '实盘', render: () => <span style={{ color: p.muted, fontSize: 12 }}>同样被拦 ✓</span> }]} /> : <Empty description="今日无拒单" /> },
          { key: 'ev', label: '事件', children: <Table size="small" pagination={false} rowKey="plan" dataSource={runPoints}
            columns={[{ title: '计划', dataIndex: 'plan', render: (v) => <Num color={p.muted}>{v}</Num> }, { title: '实际', dataIndex: 'act', render: (v) => <Num>{v ?? '—'}</Num> },
              { title: '阶段', dataIndex: 'phase' }, { title: '状态', dataIndex: 's', render: (st: Status) => <StatusTag s={st === 'warn' ? 'ok' : st} p={p} text={st === 'idle' ? '待运行' : '按时'} /> }]} /> },
          { key: 'n', label: '通知设置', children: (
            <div style={{ padding: '4px 4px 0' }}>
              <Table size="small" pagination={false} rowKey="k" dataSource={[
                { k: '模拟成交', on: true, how: '逐笔，合并 1 分钟内的多笔' }, { k: '风控拒单', on: true, how: '逐笔' }, { k: '与来源回测偏离 > 30bp', on: true, how: '每日收盘后一次' },
                { k: '账户出错 / 停用', on: true, how: '立即' }, { k: '日终结算摘要', on: false, how: '并入 20:30 日报，不单独发' },
              ]} columns={[{ title: '事件', dataIndex: 'k' }, { title: '通知', dataIndex: 'on', width: 80, render: (v) => <span style={{ color: v ? p.ok : p.muted }}>{v ? '● 开' : '○ 关'}</span> }, { title: '方式', dataIndex: 'how', render: (v) => <span style={{ color: p.muted }}>{v}</span> }]} />
              <div style={{ color: p.muted, fontSize: 12, marginTop: 8 }}>发送到企业微信 webhook <Num size={12}>https://qyapi.weixin.qq.com/…key=****a1f3</Num>（只显示打码值）。修改走配置发布，页面只读。</div>
            </div>) },
        ]} />
      </Panel>

      <Modal open={!!confirm} title={confirmKind === 'recover' ? `恢复「${confirm}」？` : `确认改变「${confirm}」的运行状态？`} okText="确认" okButtonProps={{ danger: true }} onOk={() => setConfirm(null)} onCancel={() => setConfirm(null)}>
        {confirmKind === 'recover' ? <ul style={{ paddingLeft: 18, margin: '8px 0', lineHeight: '22px' }}>
          <li>恢复前先实走一次审计序列化与备份回读，核对出错前最后一笔模拟成交（10:05:44）与持仓。</li>
          <li>只做精确状态转换（出错 → 运行中），不直接改库、不重放 10:31–10:36 之间缺失的行情事件。</li>
          <li>只影响这个模拟账户，<b>不影响实盘</b>，也不解除实盘停机闸。</li>
        </ul> : <ul style={{ paddingLeft: 18, margin: '8px 0', lineHeight: '22px' }}>
          <li>停用后不再接收行情事件、不再产生模拟委托；已有持仓与历史记录保留。</li>
          <li>只影响这个模拟账户，<b>不影响实盘</b>和其他模拟账户。</li>
          <li>操作会写入审计记录（操作人、时间、前后状态）。</li>
        </ul>}
        <div style={{ color: p.muted, fontSize: 12 }}>原型里不会真正执行。</div>
      </Modal>
    </div>
  )
}
