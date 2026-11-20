import { useMemo } from 'react'
import { Table, Tag } from 'antd'
import { CheckCircleFilled } from '@ant-design/icons'
import type { Palette } from '../theme'
import { EChart, Num, Panel, StatusTag, axisStyle, tooltipStyle } from '../ui'
import { Intraday } from '../Intraday'
import { Hero, Missing } from './common'
import { coverage, dist, sentiment60 } from './marketMock'
import { PM, BAD } from '../scene'

export function Market({ p }: { p: Palette }) {
  const s = sentiment60()

  const distOpt = useMemo(() => ({
    animation: false, grid: { left: 40, right: 8, top: 22, bottom: 24 },
    tooltip: { ...tooltipStyle(p), trigger: 'axis', formatter: (q: { name: string; value: number }[]) => `<b>${q[0].name}</b>　${q[0].value} 只<br/><span style="opacity:.7">占有效 ${(q[0].value / 5441 * 100).toFixed(1)}%${q[0].name === '涨停' ? ' · 点击去打板看板' : ''}</span>` },
    xAxis: { type: 'category', data: dist.map((d) => d.k), ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, interval: 0, fontSize: 10, hideOverlap: false }, splitLine: { show: false } },
    yAxis: { type: 'value', ...axisStyle(p) },
    series: [{ type: 'bar', barWidth: '70%', data: dist.map((d, i) => ({ value: d.n, itemStyle: { color: i < 5 ? p.down : i === 5 ? p.muted : p.up, borderRadius: [2, 2, 0, 0] } })),
      label: { show: true, position: 'top', color: p.muted, fontSize: 10 } }],
  }), [p])

  const base = (top: number, h: number, idx: number, show: boolean) => ({ left: 52, right: 52, top, height: h, gridIndex: idx, show })
  const trendOpt = useMemo(() => ({
    animation: false,
    legend: { top: 0, right: 0, textStyle: { color: p.muted, fontSize: 11 }, itemWidth: 14, itemHeight: 8 },
    tooltip: { ...tooltipStyle(p), trigger: 'axis' },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [base(26, 130, 0, false), base(186, 70, 1, false), base(286, 60, 2, false)],
    xAxis: [0, 1, 2].map((i) => ({ type: 'category', gridIndex: i, data: s.days, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, show: i === 2 }, splitLine: { show: false } })),
    yAxis: [
      { gridIndex: 0, type: 'value', name: '家数', nameTextStyle: { color: p.muted, fontSize: 10 }, ...axisStyle(p) },
      { gridIndex: 1, type: 'value', name: '上涨占比 %', min: 0, max: 100, nameTextStyle: { color: p.muted, fontSize: 10 }, ...axisStyle(p), splitNumber: 2 },
      { gridIndex: 2, type: 'value', name: 'HS300 MA20 五日斜率', nameTextStyle: { color: p.muted, fontSize: 10 }, ...axisStyle(p), splitNumber: 2 },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1, 2] }],
    series: [
      { name: '涨停', type: 'bar', stack: 'touch', data: s.up, itemStyle: { color: p.up }, barWidth: '60%' },
      { name: '炸板', type: 'bar', stack: 'touch', data: s.brk, itemStyle: { color: p.up + '00', borderColor: p.up, borderWidth: 1, borderType: 'dashed' }, barWidth: '60%' },
      { name: '跌停', type: 'line', data: s.down, symbol: 'none', lineStyle: { color: p.down, width: 1.5 }, itemStyle: { color: p.down } },
      { name: '上涨占比', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: s.adv, symbol: 'none', lineStyle: { color: p.line, width: 1.5 }, itemStyle: { color: p.line },
        areaStyle: { color: p.line, opacity: 0.12 }, markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: p.muted, type: 'dashed' }, data: [{ yAxis: 50 }] } },
      { name: 'MA20 斜率', type: 'bar', xAxisIndex: 2, yAxisIndex: 2, data: s.slope.map((v) => ({ value: v, itemStyle: { color: v >= 0 ? p.up : p.down } })) },
    ],
  }), [p]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s={BAD ? 'warn' : 'ok'} icon={<CheckCircleFilled />} title={PM ? <>收盘 · 涨停 <span style={{ color: p.up }}>71</span> · 跌停 <span style={{ color: p.down }}>6</span> · 上涨 58.4%</> : <>涨停 <span style={{ color: p.up }}>68</span> · 跌停 <span style={{ color: p.down }}>4</span> · 上涨 63.1%</>}
        sub={BAD ? '全市场快照 10:48:14 · 10:31–10:36 隧道中断，缺 6 分钟快照，走势图留空不补 · 有效 5,441 / 5,443 只' : `全市场快照 ${PM ? '15:00:05 收盘' : '10:42:14'} · 有效 5,441 / 5,443 只（缺 1、无效 1，见下方覆盖表）`}
        kpis={[
          { k: '上证指数', v: PM ? '3,452.18' : '3,441.67', sub: <span style={{ color: p.up }}>{PM ? '收 +0.94%' : '+0.63%'}</span> },
          { k: '两市成交', v: PM ? '1.38 万亿' : '6,812 亿', sub: PM ? '较昨 +7.2%' : '较昨同时 +9.4%' },
          { k: '炸板率', v: PM ? '19.3%' : '17.1%', sub: PM ? '炸 17 / 触板 88' : '炸 14 / 触板 82' },
          { k: '连板高度', v: '5 板', sub: '昨 4 板', c: p.up },
        ]} />

      <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 10 }}>
        <Panel p={p} title={PM ? '大盘今天怎么走的？' : '大盘今天怎么走？'} info="上证指数分时，昨收为基线：上方红、下方绿；下方为成交量。">
          <Intraday p={p} height={260} />
        </Panel>
        <Panel p={p} title="今天多数股票在涨还是跌？" info="全市场有效股票按涨跌幅分档计数；涨停/跌停按 stk_limit 直取价判定，缺直取价的不计入并单列。">
          <EChart height={260} option={distOpt} onClick={(q) => { if (q.name === '涨停') location.hash = 'board' }} />
        </Panel>
      </div>

      <Panel p={p} title="近 60 个交易日，短线情绪在升温还是降温？" info="三图共用横轴与十字光标，可滚轮缩放。实心＝收盘仍封住的涨停，虚线框＝炸板（盘中触板未封住），合起来是触板家数。涨停/炸板按直取涨停价判定；HS300 MA20 五日斜率为纯派生。">
        <EChart height={360} option={trendOpt} />
      </Panel>

      <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 10 }}>
        <Panel p={p} title="这份快照覆盖全了吗？" info="按市场列出应有/有效/缺失/无效与源时间范围。没有绑定股票池时“应有”记未知，不从返回结果反推。" bodyStyle={{ padding: '4px 0 0' }}>
          <Table size="small" pagination={false} rowKey="m" dataSource={coverage}
            columns={[
              { title: '市场', dataIndex: 'm', render: (v, r) => <span style={{ color: p.head }}>{v}{r.m === '北交所' && <Tag style={{ marginLeft: 6 }}>仅情绪 · 无交易权限</Tag>}</span> },
              { title: '应有', dataIndex: 'expected', align: 'right', render: (v) => <Num>{v}</Num> },
              { title: '有效', dataIndex: 'valid', align: 'right', render: (v) => <Num>{v}</Num> },
              { title: '缺失', dataIndex: 'missing', align: 'right', render: (v) => <Num color={v ? p.warn : p.muted}>{v}</Num> },
              { title: '无效', dataIndex: 'invalid', align: 'right', render: (v) => <Num color={v ? p.warn : p.muted}>{v}</Num> },
              { title: '源时间', dataIndex: 'src', render: (v) => <Num color={p.muted} size={12}>{v}</Num> },
              { title: '结论', render: (_, r) => <StatusTag p={p} s={r.missing || r.invalid ? 'warn' : 'ok'} text={r.missing || r.invalid ? '有缺口' : '完整'} /> },
            ]} />
        </Panel>
        <Panel p={p} title="涨停价核对通过了吗？" info="盘前 stk_limit 直取涨停价 与 QMT 原生涨停价逐只比对。">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 6 }}>
            {[
              { k: '已核对', v: '5,173', c: p.head }, { k: '不一致', v: '0', c: p.ok },
              { k: 'QMT 缺失', v: '3', c: p.warn }, { k: '名单外缺失', v: '0', c: p.ok },
            ].map((x) => (
              <div key={x.k} style={{ border: `1px solid ${p.border}`, borderRadius: 4, padding: '8px 10px' }}>
                <div style={{ color: p.muted, fontSize: 12 }}>{x.k}</div><Num size={18} weight={600} color={x.c}>{x.v}</Num>
              </div>
            ))}
          </div>
          <div style={{ color: p.muted, fontSize: 12, marginTop: 10 }}>交易日 11-16 · 盘前 08:58 · QMT 缺失样本：68xxxx.SH 等 3 只（新股首日）</div>
          <div style={{ color: p.muted, fontSize: 12, marginTop: 4 }}>跌停未知：<Missing p={p} why="缺直取跌停价时不按板块档位自算" /> 2 只</div>
        </Panel>
      </div>
    </div>
  )
}
