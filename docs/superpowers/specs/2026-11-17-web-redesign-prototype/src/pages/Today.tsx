import { useMemo, useState } from 'react'
import { Button, Drawer, Table, Descriptions, Typography } from 'antd'
import { WarningFilled, RightOutlined } from '@ant-design/icons'
import type { Palette } from '../theme'
import { events, links, phases, walk, type Status } from '../mock'
import { EChart, Num, Panel, Spark, StatusTag, axisStyle, statusColor, tooltipStyle } from '../ui'
import { Intraday } from '../Intraday'
import { PM, BAD } from '../scene'

const hm = (h: number) => { const m = Math.round(h * 60); return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}` }

type Evidence = { title: string; status: Status; rows: [string, string][] }

export function Today({ p }: { p: Palette }) {
  const [ev, setEv] = useState<Evidence | null>(null)

  /* 链路状态时间轴（Grafana state timeline 做法） */
  const linkOption = useMemo(() => {
    const NOW = PM ? 15.5 : BAD ? 10.806 : 10.7
    const data: unknown[] = []
    links.forEach((l, yi) => {
      l.segs.forEach((s) => data.push({ value: [yi, s[0], s[1], s[2], s[3] ?? '', l.name], itemStyle: { color: statusColor(p, s[2]) } }))
      if (!PM) data.push({ value: [yi, NOW, 15.5, 'idle', '尚未到达', l.name], itemStyle: { color: p.idle, opacity: 0.35 } })
    })
    return {
      animation: false,
      grid: { left: 86, right: 12, top: 6, bottom: 22 },
      tooltip: {
        ...tooltipStyle(p),
        formatter: (q: { value: [number, number, number, Status, string, string] }) => {
          const [, a, b, s, note, name] = q.value
          return `<b>${name}</b><br/>${hm(a)} – ${hm(b)}　${s === 'ok' ? '正常' : s === 'warn' ? '警告' : s === 'bad' ? '中断 / 故障' : '未到'}${note ? `<br/>${note}` : ''}${s === 'warn' ? '<br/><span style="opacity:.7">点击查看证据</span>' : ''}`
        },
      },
      xAxis: { type: 'value', min: 9, max: 15.5, interval: 1, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => hm(v) }, splitLine: { show: false } },
      yAxis: { type: 'category', data: links.map((l) => l.name), inverse: true, ...axisStyle(p), axisLabel: { color: p.text, fontSize: 12 } },
      series: [{
        type: 'custom',
        renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[]; size: (v: number[]) => number[]; style: () => object }) => {
          const y = api.value(0)
          const s = api.coord([api.value(1), y])
          const e = api.coord([api.value(2), y])
          const h = api.size([0, 1])[1] * 0.62
          return { type: 'rect', shape: { x: s[0], y: s[1] - h / 2, width: Math.max(2, e[0] - s[0] - 1), height: h, r: 2 }, style: api.style() }
        },
        encode: { x: [1, 2], y: 0 },
        data,
        markLine: PM ? undefined : { silent: true, symbol: 'none', label: { formatter: '现在', position: 'end', color: p.accent, fontSize: 11 }, lineStyle: { color: p.accent, type: 'solid' }, data: [{ xAxis: NOW }] },
      }],
    }
  }, [p])

  const sentiment = [
    { k: '情绪温度', v: PM ? '64' : '62', sub: PM ? '收盘 · 偏热' : '盘中 · 偏热', d: walk(20, 31, 50, 9, 0.06), c: p.warn },
    { k: '涨停家数', v: PM ? '71' : '68', sub: '昨 54', d: walk(20, 3, 50, 10, 0.08), c: p.up },
    { k: '连板高度', v: '5 板', sub: '昨 4 板', d: walk(20, 5, 4, 1.2, 0.03), c: p.up },
    { k: '亏钱效应', v: PM ? '9' : '7', sub: '大面家数', d: walk(20, 8, 10, 4, -0.03), c: p.down },
  ]

  const kpis = [
    { k: '账户总资产', v: PM ? '100,031.40' : '100,011.98', sub: PM ? '收盘观测' : '原始观测', d: walk(30, 2, 100, 0.25), c: p.line, go: 'live' },
    { k: '上证指数', v: PM ? '3,452.18' : '3,441.67', sub: PM ? '收 +0.94%' : '+0.63%', subc: p.up, d: walk(30, 7, 3420, 6, 0.06), c: p.up, go: 'market' },
    { k: '涨停 / 跌停', v: PM ? '71 / 6' : '68 / 4', sub: PM ? '封板率 81%' : '封板率 83%', d: walk(30, 3, 40, 5, 0.15), c: p.up, go: 'board' },
    { k: '链路心跳', v: '3s', sub: PM ? '盘后待机 · 阈值 90s' : BAD ? '今日断 1 次 · 已恢复' : '阈值 90s', d: walk(30, 5, 3, 0.8), c: p.ok, go: 'nodes' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* ① 结论：5 秒内回答“今天正常吗” */}
      <section style={{ display: 'flex', alignItems: 'stretch', background: p.panel, border: `1px solid ${p.border}`, borderLeft: `3px solid ${BAD ? p.bad : p.warn}`, borderRadius: 4 }}>
        <div style={{ padding: '12px 18px', minWidth: 330 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <WarningFilled style={{ color: BAD ? p.bad : p.warn, fontSize: 26 }} />
            <div>
              <div style={{ color: p.head, fontSize: 20, fontWeight: 600 }}>{PM ? '已收盘 · 1 项需要留意' : BAD ? '停机闸已触发 · 只允许减仓' : '运行中 · 2 项需要留意'}</div>
              <div style={{ color: p.muted, fontSize: 12, lineHeight: '20px' }}>
                {BAD ? <>10:31 Redis 隧道中断 5 分 35 秒 · 10:32:35 停机闸触发 · 链路已恢复<br />
                  <a href="#recon" style={{ color: p.bad }}>✕ 解除前还有 2 项要确认</a>　<a href="#paper" style={{ color: p.bad }}>✕ 2 个模拟账户出错</a>　<a href="#orders" style={{ color: p.warn }}>▲ 1 笔委托状态未知</a></>
                : PM ? <>事件 9/9 完成 · 15:30 对账一致 · 晚间任务 8/8 · 日报 20:30 已发<br />
                  <a href="#runs" style={{ color: p.warn }}>▲ 调仓迟到 44s 未处理</a>　<a href="#orders" style={{ color: p.muted }}>✓ 未知委托已由对账确认成交</a></>
                : <>链路 6/6 在线 · 事件 4/9 已到点<br />
                  <a href="#orders" style={{ color: p.warn }}>▲ 1 笔委托状态未知</a>　<a href="#runs" style={{ color: p.warn }}>▲ 调仓迟到 44s</a></>}
              </div>
            </div>
          </div>
        </div>
        {kpis.map((k) => (
          <div key={k.k} className="qs-kpi-link" onClick={() => { location.hash = k.go }} style={{ flex: 1, borderLeft: `1px solid ${p.border}`, padding: '10px 14px', minWidth: 0 }}>
            <div style={{ color: p.muted, fontSize: 12, display: 'flex', justifyContent: 'space-between' }}>{k.k}<span style={{ opacity: .6 }}>›</span></div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <Num size={18} weight={600} color={p.head}>{k.v}</Num>
              <span style={{ color: k.subc ?? p.muted, fontSize: 12 }}>{k.sub}</span>
            </div>
            <Spark data={k.d} color={k.c} height={22} />
          </div>
        ))}
      </section>

      {/* ② 阶段时间线 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 0 }}>
        {phases.map((ph, i) => {
          const c = statusColor(p, ph.status)
          return (
            <div key={ph.title} style={{ background: p.panel, border: `1px solid ${p.border}`, borderLeftWidth: i ? 0 : 1, padding: '7px 12px', boxShadow: ph.status === 'running' ? `inset 0 -2px 0 ${p.info}` : undefined }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: p.head, fontWeight: 600 }}>{ph.title}</span>
                <StatusTag s={ph.status} p={p} text={ph.status === 'running' ? '进行中' : ph.status === 'warn' ? '完成 · 1 迟到' : ph.status === 'bad' ? '停机闸 · 仅减仓' : undefined} />
              </div>
              <Num color={p.muted} size={11}>{ph.time}</Num>
              <div style={{ height: 3, background: p.idle, borderRadius: 2, marginTop: 5 }}>
                <div style={{ height: 3, borderRadius: 2, background: c, width: ph.status === 'ok' || ph.status === 'warn' ? '100%' : ph.status === 'running' ? (PM ? '83%' : '30%') : ph.status === 'bad' ? '33%' : 0 }} />
              </div>
            </div>
          )
        })}
      </div>

      {/* ③ 趋势：主图 + 事件 */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10 }}>
        <Panel p={p} title={PM ? '大盘今天怎么走的？' : '大盘此刻怎么样？'} info="上证指数 1 分钟分时；虚线为昨收，基线上方红、下方绿；蓝色标记为本系统策略事件。盘中 15 秒刷新。" extra={<span style={{ color: p.muted, fontSize: 12 }}>{PM ? '上证 · 全天分时 · 已收盘' : '上证 · 分时 · 15s 刷新'}</span>}>
          <Intraday p={p} height={290} />
        </Panel>
        <Panel p={p} title="策略事件按时完成了吗？" info="live-core 当日 7 个策略事件。点击行查看证据。" extra={PM ? <span style={{ color: p.muted, fontSize: 12 }}>9 / 9 完成 · 下一个 11-17 09:15</span> : <span style={{ color: p.muted, fontSize: 12 }}>下一个 <Num size={12} color={p.info}>11:25</Num> 午盘检查 · {BAD ? 37 : 43} 分钟后</span>} bodyStyle={{ padding: '4px 0 6px' }}>
          <Table
            size="small" pagination={false} rowKey="at" showHeader={false} className="qs-click"
            dataSource={events} scroll={{ y: 300 }}
            rowClassName={(r) => (ev?.title.startsWith(r.at) ? 'qs-row-sel' : '')}
            onRow={(r) => ({ onClick: () => setEv({ title: `${r.at} ${r.name}`, status: r.status, rows: [['状态', r.status === 'ok' ? '已完成' : r.status === 'warn' ? '已完成（迟到）' : '待运行'], ['完成于', r.done || '—'], ['耗时', r.cost || '—'], ['结果', r.note || '—'], ['证据', 'live-core journal · invocation 7f3a…（示意）']] })})}
            columns={[
              { dataIndex: 'at', width: 56, render: (v) => <Num color={p.muted}>{v}</Num> },
              { dataIndex: 'name', render: (v, r) => <div><div style={{ color: p.head }}>{v}</div>{r.note && <div style={{ color: p.muted, fontSize: 11 }}>{r.note}</div>}</div> },
              { dataIndex: 'status', width: 84, align: 'right', render: (s, r) => r.at === '11:25' && !PM ? <StatusTag s="running" p={p} text="下一个" /> : <StatusTag s={s} p={p} text={s === 'ok' ? '完成' : s === 'warn' ? '迟到' : undefined} /> },
            ]}
          />
        </Panel>
      </div>

      {/* ④ 明细：链路 + 情绪 */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10 }}>
        <Panel p={p} title="交易链路一直在线吗？" info="每行一个环节，按数据流从上到下排列；黄色为警告区段，点击查看证据。来源：health-observer 采样。" extra={<span style={{ color: p.muted, fontSize: 12 }}>09:00 – 15:30 · 5s 采样{PM ? ' · 盘后不画' : ''}</span>}>
          <EChart
            height={196}
            option={linkOption}
            onClick={(q) => {
              const v = q.value as [number, number, number, Status, string, string]
              if (v[3] === 'idle') return
              setEv({ title: v[5], status: v[3], rows: [['区间', `${hm(v[1])} – ${hm(v[2])}`], ['状态', v[3] === 'ok' ? '正常' : '警告'], ['说明', v[4] || '—'], ['证据', 'health-observer 时间线（示意）']] })
            }}
          />
        </Panel>
        <Panel p={p} title="短线情绪热不热？" info="情绪温度由涨停家数、连板高度、亏钱效应等合成（参考开盘啦的口径）；趋势为近 20 个交易日。" extra={<span style={{ color: p.muted, fontSize: 12 }}>近 20 日</span>}>
          <div style={{ display: 'grid', gap: 8 }}>
            {sentiment.map((s) => (
              <div key={s.k} style={{ display: 'grid', gridTemplateColumns: '72px 64px 1fr', alignItems: 'center', gap: 8 }}>
                <span style={{ color: p.muted, fontSize: 12 }}>{s.k}</span>
                <div><Num size={15} weight={600} color={p.head}>{s.v}</Num><div style={{ color: p.muted, fontSize: 11 }}>{s.sub}</div></div>
                <Spark data={s.d} color={s.c} height={32} />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Drawer open={!!ev} onClose={() => setEv(null)} size={460} title={ev?.title} extra={ev && <StatusTag s={ev.status} p={p} />}>
        {ev && (
          <>
            <Descriptions column={1} size="small" bordered items={ev.rows.map(([k, v]) => ({ key: k, label: k, children: v }))} />
            <Typography.Paragraph type="secondary" style={{ marginTop: 12, fontSize: 12 }}>原型：正式版这里接真实证据（运行记录、日志片段、收据链接），并可跳到对应页面。</Typography.Paragraph>
            <Button icon={<RightOutlined />} href="#runs">打开运行记录</Button>
          </>
        )}
      </Drawer>
    </div>
  )
}
