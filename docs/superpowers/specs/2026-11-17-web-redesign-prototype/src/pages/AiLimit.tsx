import { useMemo, useState } from 'react'
import { Segmented, Table, Tag, Alert, Tooltip } from 'antd'
import { RobotOutlined } from '@ant-design/icons'
import type { Palette } from '../theme'
import { rng, walk } from '../mock'
import { EChart, Num, Panel, StatusTag, axisStyle, tooltipStyle } from '../ui'
import { Hero } from './common'
import { tradingDays } from './marketMock'
import { PM } from '../scene'
/** 晚间：17:10 limit_list_d 定版后记录 11-16 实际结果 */
const ACT = (r: number) => (r % 4 === 1 || r % 7 === 3 ? 'sealed' : r % 5 === 2 ? 'touched' : 'none')

function ranking() {
  const r = rng(909)
  return Array.from({ length: 20 }, (_, i) => {
    const touch = +(0.92 - i * 0.025 - r() * 0.03).toFixed(3)
    const seal = +(0.55 + r() * 0.35).toFixed(3)
    return { rank: i + 1, code: `${['60', '00', '30'][i % 3]}${String(2000 + i * 53).slice(-4)}xx`, name: `示意${String.fromCharCode(65 + i)}`, board: ['主板', '主板', '创业板'][i % 3],
      touch, seal, close: +(touch * seal).toFixed(3), state: i % 7 === 3 ? '一字' : '正常',
      actual: i < 20 ? (r() > 0.45 ? (r() > 0.3 ? 'sealed' : 'touched') : 'none') : null }
  })
}

const BAR = ({ v, c, p }: { v: number; c: string; p: Palette }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
    <div style={{ flex: 1, height: 6, background: p.idle, borderRadius: 2 }}><div style={{ width: `${v * 100}%`, height: '100%', background: c, borderRadius: 2 }} /></div>
    <Num size={12}>{v.toFixed(3)}</Num>
  </div>
)

export function AiLimit({ p }: { p: Palette }) {
  const [tab, setTab] = useState<'shadow' | 'research'>(() => { const t = sessionStorage.getItem('qs-ailu-tab'); sessionStorage.removeItem('qs-ailu-tab'); return t === 'research' ? 'research' : 'shadow' })
  const rows = useMemo(() => ranking(), [])
  const days = PM ? [...tradingDays(39), '11-16'] : tradingDays(40)
  const p20 = walk(40, 71, 0.42, 0.07).map((x) => +Math.min(0.8, Math.max(0.1, x)).toFixed(2))
  if (PM) p20[39] = rows.filter((r) => ACT(r.rank) === 'sealed').length / 20 // 今天的命中与榜单一致
  const base = walk(40, 72, 0.18, 0.03).map((x) => +Math.max(0.05, x).toFixed(2))
  const roll = p20.map((_, i) => +(p20.slice(Math.max(0, i - 9), i + 1).reduce((a, b) => a + b, 0) / Math.min(10, i + 1)).toFixed(3))

  const precOpt = useMemo(() => ({
    animation: false, grid: { left: 40, right: 12, top: 26, bottom: 22 },
    legend: { top: 0, right: 0, textStyle: { color: p.muted, fontSize: 11 }, itemWidth: 14, itemHeight: 2 },
    tooltip: { ...tooltipStyle(p), trigger: 'axis' },
    xAxis: { type: 'category', data: days, ...axisStyle(p), splitLine: { show: false } },
    yAxis: { type: 'value', min: 0, max: 1, ...axisStyle(p) },
    series: [
      { name: '当日 P@20', type: 'bar', data: p20, itemStyle: { color: p.line, opacity: 0.35 } },
      { name: '10 日滚动', type: 'line', data: roll, symbol: 'none', lineStyle: { color: p.line, width: 2 }, itemStyle: { color: p.line } },
      { name: '基线（随机首板）', type: 'line', data: base, symbol: 'none', lineStyle: { color: p.muted, type: 'dashed' }, itemStyle: { color: p.muted } },
    ],
  }), [p]) // eslint-disable-line react-hooks/exhaustive-deps

  const calOpt = useMemo(() => {
    const bins = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    const act = bins.map((b, i) => +(b + (i % 2 ? 0.04 : -0.06) * (i / 4)).toFixed(2))
    return {
      animation: false, grid: { left: 40, right: 12, top: 12, bottom: 30 },
      tooltip: { ...tooltipStyle(p), trigger: 'axis' },
      xAxis: { type: 'value', min: 0, max: 1, name: '预测触板概率', nameLocation: 'middle', nameGap: 20, nameTextStyle: { color: p.muted, fontSize: 11 }, ...axisStyle(p) },
      yAxis: { type: 'value', min: 0, max: 1, name: '实际触板率', nameTextStyle: { color: p.muted, fontSize: 11 }, ...axisStyle(p) },
      series: [
        { type: 'line', data: [[0, 0], [1, 1]], symbol: 'none', lineStyle: { color: p.muted, type: 'dashed' }, silent: true },
        { type: 'line', name: '实际', data: bins.map((b, i) => [b, act[i]]), symbolSize: 7, lineStyle: { color: p.accent }, itemStyle: { color: p.accent } },
      ],
    }
  }, [p])

  const monthly = ['2025-05', '2025-06', '2025-07', '2025-08', '2025-09', '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10']
  const mOpt = useMemo(() => ({
    animation: false, grid: { left: 40, right: 12, top: 26, bottom: 30 },
    legend: { top: 0, right: 0, textStyle: { color: p.muted, fontSize: 11 } },
    tooltip: { ...tooltipStyle(p), trigger: 'axis' },
    xAxis: { type: 'category', data: monthly, ...axisStyle(p), splitLine: { show: false } },
    yAxis: { type: 'value', max: 1, ...axisStyle(p) },
    series: [
      { name: '封板 P@20', type: 'bar', data: walk(18, 81, 0.33, 0.06).map((x) => +x.toFixed(2)), itemStyle: { color: p.up } },
      { name: '触板 P@20', type: 'bar', data: walk(18, 82, 0.5, 0.06).map((x) => +x.toFixed(2)), itemStyle: { color: p.line2 } },
    ],
  }), [p]) // eslint-disable-line react-hooks/exhaustive-deps

  const actual = (a: string | null) => a == null ? <span style={{ color: p.muted }}>收盘后</span> : a === 'sealed' ? <span style={{ color: p.up }}>✓ 封板</span> : a === 'touched' ? <span style={{ color: p.line2 }}>◐ 触板未封</span> : <span style={{ color: p.muted }}>✗ 未触板</span>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Alert type="info" showIcon banner message="AI 涨停只是观察与研究，不是交易信号；不进入实盘或模拟账户的决策。" />
      <div><Segmented value={tab} onChange={(v) => setTab(v as 'shadow' | 'research')} options={[{ label: '今日影子榜单', value: 'shadow' }, { label: '研究回测', value: 'research' }]} /></div>

      {tab === 'shadow' ? (
        <>
          <Hero p={p} s="ok" icon={<RobotOutlined />} title={PM ? `11-16 结果已记录 · 封板命中 ${rows.filter((r) => ACT(r.rank) === 'sealed').length} / 20` : '11-16 影子榜单已生成 · Top 20'} sub={PM ? '目标交易日 11-16 · 实际结果取 17:10 limit_list_d 定版 · 11-17 的新榜单已于 19:35:04 生成（切到明天的榜单正式版提供）' : '预测日 11-13 收盘后 → 目标交易日 11-16 · 生成 11-13 19:35:08 · 模型 lgbm-limit v4'}
            kpis={[
              { k: '近 10 日 P@20', v: roll.at(-1)!.toFixed(2), sub: `基线 ${base.at(-1)}` },
              { k: '校准误差', v: '0.041', sub: '< 0.08 正常' },
              { k: '特征漂移', v: '0.12', sub: '< 0.25 正常' },
              { k: '候选池', v: '1,284', sub: '可交易首板候选' },
            ]} />
          <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 10 }}>
            <Panel p={p} title="模型最近准不准？" info="Precision@20：榜单前 20 中当日实际涨停的比例。虚线为随机抽首板的基线。">
              <EChart height={220} option={precOpt} />
            </Panel>
            <Panel p={p} title="概率可信吗？" info="把预测概率分档，对比每档实际触板率；越贴近对角线越可信（校准曲线）。">
              <EChart height={220} option={calOpt} />
            </Panel>
          </div>
          <Panel p={p} title="今日榜单" info="触板概率 × 触板后封板概率 ＝ 收盘封板概率。盘中进展取打板看板的实时快照，只作观察；实际结果收盘后以直取 limit_list_d 记录。" bodyStyle={{ padding: '4px 0 0' }}>
            <Table size="small" pagination={false} rowKey="rank" dataSource={rows} scroll={{ y: 360 }}
              columns={[
                { title: '#', dataIndex: 'rank', width: 44, render: (v) => <Num color={p.muted}>{v}</Num> },
                { title: '标的', render: (_, r) => <span><Num color={p.head}>{r.code}</Num> <span style={{ color: p.muted }}>{r.name}</span></span> },
                { title: '板块', dataIndex: 'board', width: 80 },
                { title: '触板概率', dataIndex: 'touch', width: 170, sorter: (a, b) => a.touch - b.touch, render: (v) => <BAR v={v} c={p.line2} p={p} /> },
                { title: '触板后封板', dataIndex: 'seal', width: 170, render: (v) => <BAR v={v} c={p.line} p={p} /> },
                { title: '收盘封板', dataIndex: 'close', width: 170, sorter: (a, b) => a.close - b.close, render: (v) => <BAR v={v} c={p.up} p={p} /> },
                { title: '市场状态', dataIndex: 'state', width: 90, render: (v) => (v === '一字' ? <Tag>一字</Tag> : <span style={{ color: p.muted }}>正常</span>) },
                ...(PM ? [] : [{ title: <Tooltip title="10:42 盘中快照，不是最终结果">盘中进展 ⓘ</Tooltip>, dataIndex: 'rank', width: 130, render: (r: number) => r % 4 === 1 ? <span style={{ color: p.up }}>● 封 09:{String(30 + r * 2).padStart(2, '0')}</span> : r % 5 === 2 ? <span style={{ color: p.line2 }}>◐ 触板后开板</span> : <span style={{ color: p.muted }}>○ 未触板</span> }]),
                { title: PM ? <Tooltip title="17:10 limit_list_d 直取定版">实际结果 ⓘ</Tooltip> : '实际结果', dataIndex: 'rank', width: 100, render: (r: number) => actual(PM ? ACT(r) : null) },
              ]} />
          </Panel>
        </>
      ) : (
        <>
          <Hero p={p} s="ok" icon={<RobotOutlined />} title={<>测试集 P@20 <span style={{ color: p.up }}>0.41</span> · 提升 2.3×</>} sub="2025-05-06 ~ 2026-10-30 · 测试 112 日 + 观察 41 日 · 数据 sha 61ad09fe · 源码 sha c47b2e8a（示意）"
            kpis={[
              { k: '测试 P@20', v: '0.41', sub: '112 个交易日' }, { k: '观察 P@20', v: '0.38', sub: '41 个交易日' },
              { k: '基线 P@20', v: '0.17', sub: '随机首板' }, { k: '提升', v: '2.3×', c: p.ok },
            ]} />
          <Panel p={p} title="每个月都稳定吗？" info="按目标交易日所在月份汇总；封板与触板分别统计。">
            <EChart height={240} option={mOpt} />
          </Panel>
          <Panel p={p} title="逐日结果" bodyStyle={{ padding: '4px 0 0' }}>
            <Table size="small" pagination={{ pageSize: 8, size: 'small' }} rowKey="d"
              dataSource={tradingDays(30).reverse().map((d, i) => ({ d, split: i < 20 ? '观察' : '测试', hits: 4 + (i * 7) % 7, n: 20, tp: +(0.3 + ((i * 13) % 30) / 100).toFixed(2) }))}
              columns={[
                { title: '目标交易日', dataIndex: 'd', render: (v) => <Num>{v}</Num> },
                { title: '样本', dataIndex: 'split', render: (v) => <Tag>{v}</Tag> },
                { title: '命中 / 预测', render: (_, r) => <Num>{r.hits} / {r.n}</Num> },
                { title: '封板 P@20', render: (_, r) => <Num>{(r.hits / r.n).toFixed(2)}</Num> },
                { title: '触板 P@20', dataIndex: 'tp', render: (v) => <Num>{v.toFixed(2)}</Num> },
                { title: '', render: () => <StatusTag p={p} s="ok" text="有收据" /> },
              ]} />
          </Panel>
        </>
      )}
    </div>
  )
}
