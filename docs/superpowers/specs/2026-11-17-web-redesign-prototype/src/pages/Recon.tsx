import { useMemo, useState } from 'react'
import { Drawer, Table, Timeline, Descriptions, Tag } from 'antd'
import { CheckCircleFilled, CloseCircleFilled } from '@ant-design/icons'
import type { Palette } from '../theme'
import type { Status } from '../mock'
import { EChart, Num, Panel, StatusTag, axisStyle, tooltipStyle } from '../ui'
import { Hero, Missing } from './common'
import { PM, BAD, KILL_AT } from '../scene'
import { killLog, reconDays, reconLayers, reconMatrix, rules } from './tradingMock'

const ORDER: Status[] = ['ok', 'bad', 'idle']

export function Recon({ p }: { p: Palette }) {
  const [cell, setCell] = useState<{ layer: string; day: string; s: Status; n: number } | null>(null)
  const days = reconDays()
  const m = reconMatrix()

  const option = useMemo(() => {
    const data: unknown[] = []
    m.forEach((row, yi) => row.forEach((c, xi) => data.push({ value: [xi, yi, ORDER.indexOf(c.s)], c, label: { show: c.n > 0, formatter: String(c.n), color: '#fff', fontSize: 11, fontWeight: 600 } })))
    return {
      animation: false, grid: { left: 80, right: 10, top: 30, bottom: 24 },
      tooltip: { ...tooltipStyle(p), formatter: (q: { value: [number, number]; data: { c: { s: Status; n: number } } }) => `<b>${reconLayers[q.value[1]]}</b>　${days[q.value[0]]}<br/>${q.data.c.s === 'ok' ? '● 一致' : q.data.c.s === 'bad' ? `✕ 不一致 ${q.data.c.n} 条` : '○ 未对账（实盘未启动）'}` },
      xAxis: { type: 'category', data: days, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, interval: 2 }, splitLine: { show: false } },
      yAxis: { type: 'category', data: reconLayers, inverse: true, ...axisStyle(p), axisLabel: { color: p.text, fontSize: 12 } },
      visualMap: { type: 'piecewise', orient: 'horizontal', right: 10, top: 0, itemWidth: 12, itemHeight: 10, textStyle: { color: p.muted, fontSize: 11 },
        pieces: [{ value: 0, label: '● 一致', color: p.ok }, { value: 1, label: '✕ 不一致（格内＝条数）', color: p.bad }, { value: 2, label: '○ 未对账', color: p.idle }] },
      series: [
        { type: 'heatmap', data, itemStyle: { borderColor: p.panel, borderWidth: 2, borderRadius: 2 } },
        { type: 'line', data: [], silent: true,
          markArea: { silent: true, itemStyle: { color: 'transparent' }, label: { color: p.muted, fontSize: 12, position: 'inside' }, data: [[{ xAxis: days[0], name: '实盘 11-11 启动前 · 没有可对的账' }, { xAxis: days[PM ? 25 : 26] }]] },
          markLine: { silent: true, symbol: 'none', lineStyle: { color: p.accent, type: 'dashed' }, label: { formatter: '实盘启动', color: p.accent, fontSize: 11, position: 'end' }, data: [{ xAxis: PM ? 25.5 : 26.5 }] } },
      ],
    }
  }, [p]) // eslint-disable-line react-hooks/exhaustive-deps

  const bad = m.flatMap((row, li) => row.map((c, di) => ({ ...c, layer: reconLayers[li], day: days[di] }))).filter((c) => c.s === 'bad')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s={BAD ? 'bad' : 'ok'} icon={BAD ? <CloseCircleFilled /> : <CheckCircleFilled />} title={BAD ? '停机闸已触发 · 解除前还有 2 项要确认' : PM ? '今天 3 层对账全部一致' : '上一交易日 3 层对账全部一致'} sub={BAD ? `${KILL_AT} watchdog 触发（Redis 隧道心跳中断）· 上一交易日 11-13 三层对账一致 · 解除只走受管入口，页面不提供按钮` : PM ? '11-16 15:30:12 收盘对账 · 系统账本 vs QMT 原生查询 · 补确认 q-1116-0004 已成交 100 股（券商全天未推送回报）' : '11-13 15:30:02 收盘对账 · 系统账本 vs QMT 原生查询'}
        kpis={[
          { k: '资金', v: '✓ 一致', c: p.ok }, { k: '持仓', v: '✓ 一致', c: p.ok }, { k: '委托/成交', v: '✓ 一致', c: p.ok },
          { k: '近 30 日不一致', v: `${bad.length} 次`, sub: '均已闭环', c: bad.length ? p.warn : p.ok },
          PM ? { k: '下一次对账', v: '11-17 15:30', sub: '收盘后自动运行', c: p.muted } : { k: '今天的对账', v: '15:30', sub: '收盘后自动运行', c: p.muted },
          BAD ? { k: '停机闸', v: '已触发', c: p.bad, sub: KILL_AT } : { k: '急停', v: '未触发', c: p.ok, sub: '最后清除 11-11 09:12' },
        ]} />

      {BAD && <ReleaseCheck p={p} />}
      <Panel p={p} title="最近 30 个交易日每一层都对上了吗？" info="每列一个交易日、每行一个对账层。红格里的数字是差异条数，点开看逐条差异。实盘启动（11-11）前为未对账。">
        <EChart height={170} option={option} onClick={(q) => { const d = q.data as { value: [number, number]; c: { s: Status; n: number } }; if (d.c.s !== 'idle') setCell({ layer: reconLayers[d.value[1]], day: days[d.value[0]], ...d.c }) }} />
      </Panel>

      <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 10 }}>
        <Panel p={p} title="风控门禁都在吗？" info="只读展示：规则来自已发布的风控配置，当前值来自 live-core 风控快照。页面不提供修改入口。" bodyStyle={{ padding: '4px 0 0' }}
          extra={<Tag>只读 · 修改走配置发布</Tag>}>
          <Table size="small" pagination={false} rowKey="rule" dataSource={rules}
            columns={[
              { title: '规则', dataIndex: 'rule', render: (v) => <span style={{ color: p.head }}>{v}</span> },
              { title: '上限', dataIndex: 'limit', render: (v) => <Num>{v}</Num> },
              { title: '当前', dataIndex: 'used', render: (v) => (v == null ? <Missing p={p} why="live-core 尚未对外暴露该用量" /> : <Num>{v}</Num>) },
              { title: '状态', dataIndex: 's', width: 100, render: (s: Status) => <StatusTag s={s} p={p} text={s === 'warn' ? '接近上限' : s === 'idle' ? '无数据' : '正常'} /> },
            ]} />
          <div style={{ color: p.muted, fontSize: 11, padding: '6px 12px' }}>配置版本 risk-2026-11-11 · 1 万试运行额度</div>
        </Panel>
        <Panel p={p} title="急停记录" info="页面只展示状态与历史。清除急停走受管入口，不在网页上提供按钮。" extra={<Tag>只读 · 清除走受管入口</Tag>}>
          <Timeline style={{ marginTop: 8 }} items={killLog.map((k) => ({
            color: k.act === '触发' ? p.bad : p.ok,
            content: <div><Num color={p.muted} size={12}>{k.at}</Num>　<b style={{ color: k.act === '触发' ? p.bad : p.ok }}>{k.act === '触发' ? '✕ 触发' : '● 清除'}</b><div style={{ fontSize: 12 }}>{k.who}：{k.why}</div></div>,
          }))} />
        </Panel>
      </div>

      <Panel p={p} title="不一致记录" info="近 30 日全部不一致事件；每条都要有闭环说明。" bodyStyle={{ padding: '4px 0 0' }}>
        <Table size="small" pagination={false} rowKey={(r) => r.layer + r.day} dataSource={bad} className="qs-click" onRow={(r) => ({ onClick: () => setCell(r) })}
          rowClassName={(r) => (cell && r.layer === cell.layer && r.day === cell.day ? 'qs-row-sel' : '')}
          columns={[
            { title: '交易日', dataIndex: 'day', width: 90, render: (v) => <Num>{v}</Num> },
            { title: '层级', dataIndex: 'layer' },
            { title: '差异条数', dataIndex: 'n', align: 'right', render: (v) => <Num color={p.bad}>{v}</Num> },
            { title: '结论', render: () => <StatusTag s="ok" p={p} text="已闭环" /> },
            { title: '说明', render: () => <span style={{ color: p.muted }}>券商回报晚于收盘对账，次日复核一致（示意）</span> },
          ]} />
      </Panel>

      <Drawer open={!!cell} onClose={() => setCell(null)} size={520} title={cell && `${cell.layer} · ${cell.day}`} extra={cell && <StatusTag s={cell.s} p={p} text={cell.s === 'ok' ? '一致' : `不一致 ${cell.n} 条`} />}>
        {cell && (
          <>
            <Descriptions size="small" column={1} bordered items={[
              { key: 'a', label: '对账时间', children: <Num>{cell.day} {cell.day === '11-16' ? '15:30:12' : '15:30:02'}</Num> },
              { key: 'b', label: '系统侧', children: 'live journal / 业务账本' }, { key: 'c', label: '券商侧', children: 'QMT 原生查询（执行器回传）' },
            ]} />
            {cell.day === '11-16' && cell.layer === '委托/成交' && (
              <div style={{ marginTop: 12, fontSize: 12, color: p.muted }}>券商全天未推送 q-1116-0004 的回报；本次对账从券商当日成交流水确认 10:05:51 成交 100 股，系统账本据此记为终态，两侧一致。</div>
            )}
            {cell.n > 0 && (
              <Table style={{ marginTop: 14 }} size="small" pagination={false} rowKey="k"
                dataSource={[{ k: 1, item: 'q-xxxx 成交数量', sys: '200', broker: '0', note: '次日 09:00 复核：券商侧补回报 200' }].slice(0, cell.n)}
                columns={[{ title: '项', dataIndex: 'item' }, { title: '系统', dataIndex: 'sys' }, { title: '券商', dataIndex: 'broker' }, { title: '闭环', dataIndex: 'note' }]} />
            )}
          </>
        )}
      </Drawer>
    </div>
  )
}

/** 停机闸解除前核对清单（只读）：每项给出当前真实输出与判定；全部通过且维护者确认后，才经受管入口解除。 */
function ReleaseCheck({ p }: { p: Palette }) {
  const items: { k: string; now: string; s: Status; note?: string }[] = [
    { k: '触发原因已消除', now: 'Redis 隧道 10:36:40 恢复；之后心跳连续 11 分 40 秒，最长间隔 3s', s: 'ok' },
    { k: '交易链路全部在线', now: 'live-core · 执行网关 · QMT 执行器第 5 行策略均在线；队列 0', s: 'ok' },
    { k: '资金 / 持仓 一致', now: '10:40 盘中临时对账：资金一致，持仓 3 只一致（含预留 100 股）', s: 'ok' },
    { k: '券商活动委托 与 实盘账本', now: '券商快照活动委托 2 笔；本系统账本未决 1 笔（q-1116-0004）。多出 1 笔不是本系统委托号', s: 'warn',
      note: '可能是同账户的人工委托（如新债申购）。按 #666 规则：先由维护者确认来源，不自行推断；系统撤单也只撤本系统委托号。' },
    { k: '状态未知委托', now: 'q-1116-0004 卖 100：ACK 后 42 分钟无券商回报', s: 'warn', note: '解除后仍不重发、不释放预留，等收盘对账确认终态；需维护者知悉后再解除。' },
    { k: '维护者确认并经受管入口解除', now: '待确认', s: 'idle', note: '受管入口会用当前真实输出逐项复核，任何一项不符即拒绝（11-16 曾因复核项字段顺序写错而误回落）。' },
  ]
  const icon = (st: Status) => (st === 'ok' ? '✓' : st === 'warn' ? '▲' : st === 'bad' ? '✕' : '○')
  return (
    <Panel p={p} title="解除停机闸之前，还差什么？" info="只读清单：每一项显示当前真实状态和判定。页面不提供解除按钮；解除走受管入口，并以入口自己的复核结果为准。"
      extra={<Tag color="error">只读 · 解除走受管入口</Tag>}>
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {items.map((x, i) => {
          const c = x.s === 'ok' ? p.ok : x.s === 'warn' ? p.warn : x.s === 'bad' ? p.bad : p.muted
          return (
            <div key={x.k} style={{ display: 'grid', gridTemplateColumns: '28px 220px 1fr', gap: 10, padding: '8px 4px', borderTop: i ? `1px solid ${p.border}` : 'none', alignItems: 'start' }}>
              <span style={{ width: 20, height: 20, borderRadius: 10, border: `1.5px solid ${c}`, color: c, fontSize: 11, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{icon(x.s)}</span>
              <b style={{ color: p.head, fontWeight: 500 }}>{x.k}</b>
              <div><div style={{ color: x.s === 'ok' ? p.text : c }}>{x.now}</div>{x.note && <div style={{ color: p.muted, fontSize: 12, marginTop: 2 }}>{x.note}</div>}</div>
            </div>
          )
        })}
      </div>
      <div style={{ color: p.muted, fontSize: 12, marginTop: 6, borderTop: `1px solid ${p.border}`, paddingTop: 8 }}>
        通过 3 · 待确认 2 · 待办 1。停机闸期间：新开仓全部拦截，减仓与撤单保留；模拟账户出错不影响实盘解除，单独恢复。
      </div>
    </Panel>
  )
}
