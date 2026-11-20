import { useMemo, useState } from 'react'
import { Collapse, Drawer, Table, Button, Space, App, Descriptions } from 'antd'
import { WarningFilled, CopyOutlined, DownloadOutlined } from '@ant-design/icons'
import type { Palette } from '../theme'
import type { Status } from '../mock'
import { EChart, Num, Panel, StatusTag, axisStyle, statusColor, tooltipStyle } from '../ui'
import { DrawerNav, Hero } from './common'
import { runPoints } from './tradingMock'
import { PM } from '../scene'

const h = (t: string) => { const [a, b, c] = t.split(':').map(Number); return a + b / 60 + c / 3600 }
const hm = (v: number) => `${String(Math.floor(v)).padStart(2, '0')}:${String(Math.round((v % 1) * 60)).padStart(2, '0')}`
type Point = (typeof runPoints)[number]
const CANDS: Record<string, { c: string; step: string; why: string; out: string }[]> = {
  竞价: [
    { c: '603xxx.SH', step: '入档', why: '首板 · 封单比 3.1', out: 'q-1116-0001 买 300' },
    { c: '002xxx.SZ', step: '入档', why: '首板 · 封单比 2.4', out: 'q-1116-0002 买 200' },
    { c: '920xxx.BJ', step: '拦截', why: '北交所禁开仓（#589）', out: '—' },
  ],
  开盘确认: [
    { c: '600xxx.SH', step: '入档', why: '竞价未成交，开盘确认仍满足', out: 'q-1116-0003 买 500' },
    { c: '000xxx.SZ', step: '过滤', why: '开盘涨幅超出区间', out: '—' },
  ],
  盘中调仓: [
    { c: '601xxx.SH', step: '卖出', why: '持仓达到退出条件（示意）', out: 'q-1116-0004 卖 100' },
    { c: '688xxx.SH', step: '拦截', why: '单笔 1.86 万 > 1 万', out: '—' },
  ],
  尾盘检查: [
    { c: '603xxx.SH', step: '持有', why: 'T+1 今日买入不可卖', out: '—' },
    { c: '002xxx.SZ', step: '持有', why: 'T+1 今日买入不可卖', out: '—' },
  ],
}

const REPORT = PM ? `qsys 日报 2026-11-16（示意 · 20:30 已发送）
策略 two_stage_limit_hold · 版本 f7590c0
竞价：候选 12 → 入档 3 → 闸门 0 → 意图 2
委托：成交 3（含收盘对账确认 1）· 撤单 1 · 未知 0 · 风控拦截 2
对账：15:30:12 资金 / 持仓 / 委托成交 三层一致
持仓：2 只 · 敞口 8.8% · 单日未亏损
告警：10:05 盘中调仓迟到 44s（阈值 30s，未处理）
晚间任务：8 / 8 完成 · catalog-verify 34 条已知警告` : `qsys 日报草稿 2026-11-16（示意 · 20:30 发送）
策略 two_stage_limit_hold · 版本 f7590c0
竞价：候选 12 → 入档 3 → 闸门 0 → 意图 2
委托：成交 2 · 撤单 1 · 未知 1 · 风控拦截 2
对账：待收盘（15:30）
告警：10:05 盘中调仓迟到 44s（阈值 30s）`

export function Runs({ p }: { p: Palette }) {
  const [sel, setSel] = useState<Point | null>(null)
  const { message } = App.useApp()
  const done = runPoints.filter((r) => r.act)

  const option = useMemo(() => {
    const planned = runPoints.map((r, i) => ({ value: [h(r.plan), i], r }))
    const actual = runPoints.filter((r) => r.act).map((r) => ({ value: [h(r.act!), runPoints.indexOf(r)], r, itemStyle: { color: statusColor(p, r.s) } }))
    return {
      animation: false, grid: { left: 80, right: 20, top: 10, bottom: 24 },
      tooltip: { ...tooltipStyle(p), formatter: (q: { seriesIndex: number; data: { r: Point } }) => { const r = q.data.r; const late = r.act ? Math.round((h(r.act) - h(r.plan)) * 3600) : null; return `<b>${r.phase}</b><br/>计划 ${r.plan}<br/>实际 ${r.act ?? '未到'}${late != null ? `　迟 ${late}s` : ''}` } },
      xAxis: { type: 'value', min: 9, max: 15, interval: 0.5, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => hm(v) } },
      yAxis: { type: 'category', inverse: true, data: runPoints.map((r) => r.phase), ...axisStyle(p), axisLabel: { color: p.text, fontSize: 12 }, splitLine: { show: false } },
      series: [
        { type: 'line', data: [], silent: true,
          markArea: { silent: true, itemStyle: { color: p.idle, opacity: 0.25 }, label: { color: p.muted, fontSize: 11, position: 'insideTop' }, data: [[{ xAxis: 11.5, name: '午间休市' }, { xAxis: 13 }]] } },
        ...runPoints.filter((r) => r.act && r.s === 'warn').map((r) => ({
          type: 'line', silent: true, symbol: 'none', lineStyle: { color: p.warn, width: 2 },
          data: [[h(r.plan), runPoints.indexOf(r)], [h(r.act!), runPoints.indexOf(r)]],
          endLabel: { show: true, formatter: `迟 ${Math.round((h(r.act!) - h(r.plan)) * 3600)}s`, color: p.warn, fontSize: 11, offset: [6, 0] },
        })),
        { type: 'scatter', name: '计划', data: planned, symbol: 'circle', symbolSize: 11, itemStyle: { color: 'transparent', borderColor: p.muted, borderWidth: 1.5 } },
        { type: 'scatter', name: '实际', data: actual, symbol: 'circle', symbolSize: 8, z: 3,
          markLine: PM ? undefined : { silent: true, symbol: 'none', label: { formatter: '现在', position: 'end', color: p.accent, fontSize: 11 }, lineStyle: { color: p.accent }, data: [{ xAxis: 10.7 }] } },
      ],
    }
  }, [p])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s="warn" icon={<WarningFilled />} title={PM ? '7 个时点全部执行 · 1 个迟到' : '已到点 4 个，全部执行 · 1 个迟到'} sub={PM ? '10:05 盘中调仓实际 10:05:44 触发，迟 44s（阈值 30s），告警仍未处理；收盘与 15:30 对账见今日运行' : '10:05 盘中调仓实际 10:05:44 触发，迟 44s（阈值 30s）；下一个 11:25 午盘检查'}
        kpis={[
          { k: '策略 / 发布', v: 'S6 v7 · f7590c0', c: p.head, go: 'strategies' }, { k: '候选（竞价）', v: '12' }, { k: '发出委托 / 被拦截', v: '4 / 2', go: 'orders' }, { k: '最慢一步', v: '2.4s', sub: '盘中调仓' },
        ]} />

      <Panel p={p} title="今天每个时点按时触发了吗？" info="空心圈＝计划时间，实心点＝实际触发；颜色＝结果。迟到超过 30s 记警告。点击点看这次运行的候选与意图。">
        <EChart height={220} option={option} onClick={(q) => { const r = (q.data as { r?: Point })?.r; if (r?.act) setSel(r) }} />
      </Panel>

      <Panel p={p} title="运行明细" bodyStyle={{ padding: '4px 0 0' }}>
        <Table size="small" pagination={false} rowKey="plan" dataSource={runPoints} onRow={(r) => ({ onClick: () => r.act && setSel(r), style: { cursor: r.act ? 'pointer' : 'default' } })}
          rowClassName={(r) => (r.plan === sel?.plan ? 'qs-row-sel' : '')}
          columns={[
            { title: '计划', dataIndex: 'plan', width: 90, render: (v) => <Num color={p.muted}>{v}</Num> },
            { title: '实际', dataIndex: 'act', width: 90, render: (v) => <Num>{v ?? '—'}</Num> },
            { title: '阶段', dataIndex: 'phase' },
            { title: '结果', dataIndex: 's', width: 100, render: (s: Status) => <StatusTag s={s} p={p} text={s === 'warn' ? '迟到' : undefined} /> },
            { title: '候选', dataIndex: 'cand', align: 'right', render: (v) => <Num>{v ?? '—'}</Num> },
            { title: '发出委托', dataIndex: 'intents', align: 'right', render: (v) => <Num>{v ?? '—'}</Num> },
            { title: '被风控拦截', dataIndex: 'blocked', align: 'right', render: (v) => <Num color={v ? p.bad : undefined}>{v ?? '—'}</Num> },
            { title: '耗时', dataIndex: 'cost', align: 'right', render: (v) => <Num>{v == null ? '—' : `${v}s`}</Num> },
          ]} />
      </Panel>

      <Collapse size="small" items={[{ key: 'r', label: PM ? '今日日报（20:30 已发送）' : '今日日报草稿（20:30 发送）', extra: <Space onClick={(e) => e.stopPropagation()}>
        <Button size="small" icon={<CopyOutlined />} onClick={() => { navigator.clipboard?.writeText(REPORT); message.success('日报已复制') }}>复制</Button>
        <Button size="small" icon={<DownloadOutlined />} onClick={() => { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([REPORT], { type: 'text/plain' })); a.download = 'qsys-日报-2026-11-16.txt'; a.click() }}>下载 .txt</Button></Space>,
        children: <pre style={{ margin: 0, fontSize: 12, color: p.text, whiteSpace: 'pre-wrap' }}>{REPORT}</pre> }]} />

      <Drawer open={!!sel} onClose={() => setSel(null)} size={520} title={sel && `${sel.phase} · ${sel.plan}`} extra={sel && <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}><DrawerNav p={p} list={done} cur={sel} set={setSel} /><StatusTag s={sel.s} p={p} text={sel.s === 'warn' ? '迟到' : undefined} /></span>}>
        {sel && (
          <>
            <Descriptions size="small" column={2} bordered items={[
              { key: 'a', label: '计划', children: <Num>{sel.plan}</Num> }, { key: 'b', label: '实际', children: <Num>{sel.act ?? '—'}</Num> },
              { key: 'c', label: '候选', children: <Num>{sel.cand ?? '—'}</Num> }, { key: 'd', label: '发出 / 拦截', children: <Num>{sel.intents ?? '—'} / {sel.blocked ?? '—'}</Num> },
            ]} />
            <div style={{ margin: '14px 0 6px', color: p.head, fontWeight: 600 }}>候选与过滤（示意）</div>
            <Table size="small" pagination={false} rowKey="c"
              dataSource={CANDS[sel.phase] ?? []}
              locale={{ emptyText: <span style={{ color: p.muted }}>这一步不选股，只做检查</span> }}
              columns={[{ title: '代码', dataIndex: 'c', render: (v) => <Num>{v}</Num> },
                { title: '步骤', dataIndex: 'step', render: (v) => <span style={{ color: v === '拦截' ? p.bad : v === '过滤' ? p.muted : p.text }}>{v}</span> },
                { title: '原因', dataIndex: 'why' }, { title: '产出', dataIndex: 'out', render: (v) => v.startsWith('q-') ? <a href="#orders">{v}</a> : <span style={{ color: p.muted }}>{v}</span> }]} />
            {(sel.cand ?? 0) > (CANDS[sel.phase]?.length ?? 0) && <div style={{ color: p.muted, fontSize: 12, marginTop: 6 }}>其余 {(sel.cand ?? 0) - (CANDS[sel.phase]?.length ?? 0)} 只在闸门前被过滤，正式版可展开。</div>}
          </>
        )}
      </Drawer>
    </div>
  )
}
