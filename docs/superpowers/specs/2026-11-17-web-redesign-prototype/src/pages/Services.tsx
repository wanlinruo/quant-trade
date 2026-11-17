import { useMemo, useState } from 'react'
import { Drawer, Segmented, Table, Descriptions, Steps, Typography, Tag, Button } from 'antd'
import { WarningFilled } from '@ant-design/icons'
import type { Palette } from '../theme'
import { gantt, serviceDays, serviceMatrix, services, statusText, walk, type Status } from '../mock'
import { PM } from '../scene'
import { EChart, Num, Panel, Spark, StatusTag, axisStyle, statusColor, tooltipStyle } from '../ui'

type Run = { svc: string; day: string; status: Status }
const ORDER: Status[] = ['ok', 'warn', 'bad', 'idle', 'closed', 'running']

export function Services({ p }: { p: Palette }) {
  const [view, setView] = useState<'grid' | 'gantt'>('grid')
  const [run, setRun] = useState<Run | null>(null)
  const days = serviceDays()
  const matrix = serviceMatrix()

  /* 服务 × 日期矩阵（Airflow Grid 做法） */
  const gridOption = useMemo(() => {
    const data: unknown[] = []
    matrix.forEach((row, yi) => row.forEach((s, xi) => data.push({ value: [xi, yi, ORDER.indexOf(s)], status: s })))
    return {
      animation: false,
      grid: { left: 120, right: 10, top: 30, bottom: 24 },
      tooltip: {
        ...tooltipStyle(p),
        formatter: (q: { value: [number, number, number]; data: { status: Status } }) =>
          `<b>${services[q.value[1]].name}</b>　${days[q.value[0]].label}<br/>${statusText[q.data.status]}${q.data.status === 'closed' ? '（不运行）' : q.data.status === 'idle' ? `（计划 ${services[q.value[1]].at}）` : '<br/><span style="opacity:.7">点击打开这次运行</span>'}`,
      },
      xAxis: { type: 'category', data: days.map((d) => d.label), ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, interval: 2 }, splitLine: { show: false } },
      yAxis: { type: 'category', data: services.map((s) => s.name), inverse: true, ...axisStyle(p), axisLabel: { color: p.text, fontSize: 12 } },
      visualMap: {
        type: 'piecewise', show: true, orient: 'horizontal', right: 10, top: 0, itemWidth: 12, itemHeight: 10, textStyle: { color: p.muted, fontSize: 11 },
        pieces: [
          { value: 0, label: '● 正常', color: p.ok }, { value: 1, label: '▲ 警告', color: p.warn }, { value: 2, label: '✕ 失败', color: p.bad },
          { value: 3, label: '○ 待运行', color: p.idle }, { value: 4, label: '— 休市', color: p.panel2 },
        ],
      },
      series: [{ type: 'heatmap', data, itemStyle: { borderColor: p.panel, borderWidth: 2, borderRadius: 2 }, emphasis: { itemStyle: { borderColor: p.head, borderWidth: 1 } } }],
    }
  }, [p]) // eslint-disable-line react-hooks/exhaustive-deps

  /* 当晚 Gantt（Airflow Gantt 做法） */
  const ganttOption = useMemo(() => {
    const hm = (h: number) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`
    return {
      animation: false,
      grid: { left: 120, right: 16, top: 8, bottom: 24 },
      tooltip: { ...tooltipStyle(p), formatter: (q: { value: [number, number, number, string] }) => `<b>${gantt[q.value[0]].name}</b><br/>${hm(q.value[1])} – ${hm(q.value[2])}　${statusText[gantt[q.value[0]].status]}` },
      xAxis: { type: 'value', min: 15, max: 21, interval: 1, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => hm(v) } },
      yAxis: { type: 'category', data: gantt.map((g) => g.name), inverse: true, ...axisStyle(p), axisLabel: { color: p.text, fontSize: 12 }, splitLine: { show: false } },
      series: [{
        type: 'custom',
        renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[]; size: (v: number[]) => number[]; style: () => object }) => {
          const y = api.value(0); const s = api.coord([api.value(1), y]); const e = api.coord([api.value(2), y]); const h = api.size([0, 1])[1] * 0.5
          return { type: 'rect', shape: { x: s[0], y: s[1] - h / 2, width: Math.max(5, e[0] - s[0]), height: h, r: 2 }, style: api.style() }
        },
        encode: { x: [1, 2], y: 0 },
        data: gantt.map((g, i) => ({ value: [i, g.start, g.end, g.status], itemStyle: { color: statusColor(p, g.status) } })),
      }],
    }
  }, [p])

  const lastIdx = matrix.map((row) => row.length - 1 - [...row].reverse().findIndex((x) => x !== 'closed' && x !== 'idle'))
  const tableData = services.map((s, i) => ({
    ...s, key: s.id, status: matrix[i][lastIdx[i]], lastDay: days[lastIdx[i]].label,
    trend: walk(20, 40 + i, s.avg, s.avg * 0.15),
    rate: Math.round((matrix[i].filter((x) => x === 'ok' || x === 'warn').length / matrix[i].filter((x) => x !== 'closed' && x !== 'idle').length) * 1000) / 10,
  }))

  const kpi = [
    PM ? { k: '今天', v: '8 / 8', sub: '全部跑完 · catalog-verify 已知警告', c: p.ok } : { k: '今天', v: '1 / 8', sub: '盘前 1 完成 · 盘中采集运行中 · 晚间 6 待运行', c: p.text },
    { k: '近 30 日成功率', v: '96.8%', sub: '失败 3 次，均已补跑', c: p.ok },
    { k: '常驻进程', v: '9 / 9', sub: '全部 active', c: p.ok },
    { k: '最慢任务', v: '9m03s', sub: 'catalog-verify', c: p.head },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <section style={{ display: 'flex', background: p.panel, border: `1px solid ${p.border}`, borderLeft: `3px solid ${p.warn}`, borderRadius: 4 }}>
        <div style={{ padding: '12px 18px', minWidth: 320, display: 'flex', alignItems: 'center', gap: 10 }}>
          <WarningFilled style={{ color: p.warn, fontSize: 24 }} />
          <div>
            <div style={{ color: p.head, fontSize: 20, fontWeight: 600 }}>{PM ? '今晚全部跑完 · 1 项已知警告' : '上一交易日 1 项已知警告'}</div>
            <div style={{ color: p.muted, fontSize: 12 }}>{PM ? '19:40' : '11-13 晚间'} catalog-verify：28 个历史字段缺口（#594）+ 上游自相矛盾（#653），无新增</div>
          </div>
        </div>
        {kpi.map((k) => (
          <div key={k.k} style={{ flex: 1, borderLeft: `1px solid ${p.border}`, padding: '10px 14px' }}>
            <div style={{ color: p.muted, fontSize: 12 }}>{k.k}</div>
            <Num size={18} weight={600} color={k.c}>{k.v}</Num>
            <div style={{ color: p.muted, fontSize: 11 }}>{k.sub}</div>
          </div>
        ))}
      </section>

      <Panel
        p={p}
        title={view === 'grid' ? '近 30 天每个任务都跑成功了吗？' : PM ? '今晚的时间花在哪？' : '11-13 晚间的时间花在哪？'}
        info={view === 'grid' ? '每列一天、每行一个服务；休市日不运行。点击格子打开那次运行的步骤与证据。' : '当晚各任务的实际运行区间；用于发现排队、超时与重叠。'}
        extra={<Segmented size="small" value={view} onChange={(v) => setView(v as 'grid' | 'gantt')} options={[{ label: '30 日矩阵', value: 'grid' }, { label: PM ? '今晚时间线' : '上一交易日时间线', value: 'gantt' }]} />}
      >
        {view === 'grid' ? (
          <EChart height={320} option={gridOption} onClick={(q) => {
            const d = q.data as { value: [number, number]; status: Status }
            if (d.status === 'closed' || d.status === 'idle' || d.status === 'running') return
            setRun({ svc: services[d.value[1]].name, day: days[d.value[0]].label, status: d.status })
          }} />
        ) : (
          <EChart height={240} option={ganttOption} />
        )}
      </Panel>

      <Panel p={p} title="服务清单" info="可排序；点击行展开最近 5 次运行。耗时趋势为近 20 次。" bodyStyle={{ padding: '4px 0 0' }}>
        <Table
          size="small" pagination={false} dataSource={tableData}
          className="qs-click" onRow={(r) => ({ onClick: () => setRun({ svc: r.name, day: r.lastDay, status: r.status }) })}
          rowClassName={(r) => (r.name === run?.svc ? 'qs-row-sel' : '')}
          expandable={{
            expandedRowRender: (r) => (
              <Table size="small" pagination={false} rowKey="d"
                dataSource={(PM ? ['11-16', '11-13', '11-12', '11-11', '11-10'] : ['11-13', '11-12', '11-11', '11-10', '11-07']).map((d) => ({ d, s: matrix[services.findIndex((x) => x.id === r.id)][days.findIndex((x) => x.label === d)] }))}
                columns={[{ title: '交易日', dataIndex: 'd', width: 90 }, { title: '结果', dataIndex: 's', render: (s: Status) => <StatusTag s={s} p={p} /> }, { title: '', render: (_, x) => <a onClick={(e) => { e.stopPropagation(); setRun({ svc: r.name, day: x.d, status: x.s }) }}>打开这次运行</a> }]}
              />
            ),
          }}
          columns={[
            { title: '服务', dataIndex: 'name', sorter: (a, b) => a.name.localeCompare(b.name), render: (v) => <span style={{ color: p.head }}>{v}</span> },
            { title: '计划', dataIndex: 'at', width: 80, render: (v) => <Num color={p.muted}>{v}</Num> },
            { title: '最近结果', dataIndex: 'status', width: 150, filters: [{ text: '正常', value: 'ok' }, { text: '警告', value: 'warn' }, { text: '失败', value: 'bad' }], onFilter: (v, r) => r.status === v, render: (s, r) => <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}><StatusTag s={s} p={p} text={s === 'running' ? '运行中' : undefined} /><Num size={11} color={p.muted}>{r.lastDay}</Num></span> },
            { title: '30 日完成率', dataIndex: 'rate', width: 110, sorter: (a, b) => a.rate - b.rate, align: 'right', render: (v) => <Num color={v < 90 ? p.warn : p.text}>{v}%</Num> },
            { title: '耗时趋势', dataIndex: 'trend', width: 160, render: (d: number[]) => <Spark data={d} color={p.line} height={22} area={false} /> },
            { title: '平均耗时', dataIndex: 'avg', width: 96, sorter: (a, b) => a.avg - b.avg, align: 'right', render: (v: number) => <Num>{v >= 60 ? `${Math.floor(v / 60)}m${String(v % 60).padStart(2, '0')}s` : `${v}s`}</Num> },
            { title: '版本', dataIndex: 'ver', width: 90, render: (v) => <Tag style={{ fontFamily: 'monospace' }}>{v}</Tag> },
          ]}
        />
      </Panel>

      <Drawer open={!!run} onClose={() => setRun(null)} size={500} title={run && `${run.svc} · ${run.day}`} extra={run && <StatusTag s={run.status} p={p} />}>
        {run && (
          <>
            <Descriptions column={1} size="small" bordered items={[
              ...(() => {
                const sv = services.find((x) => x.name === run.svc)!
                if (!/^\d/.test(sv.at)) return [{ key: 'a', label: '运行区间', children: <Num>{run.day} 09:15 – 15:05（盘中常驻采集）</Num> }]
                const [h, m] = sv.at.split(':').map(Number); const t0 = h * 3600 + m * 60 + 1; const t1 = t0 + sv.avg
                const f = (t: number) => [t / 3600, (t % 3600) / 60, t % 60].map((x) => String(Math.floor(x)).padStart(2, '0')).join(':')
                return [{ key: 'a', label: '开始', children: <Num>{run.day} {f(t0)}</Num> }, { key: 'b', label: '结束', children: <Num>{run.day} {f(t1)}</Num> }]
              })(),
              { key: 'c', label: 'InvocationID', children: <Num>3e1c9a…（示意）</Num> },
              { key: 'd', label: '版本', children: <Num>{services.find((x) => x.name === run.svc)?.ver}</Num> },
            ]} />
            <Typography.Title level={5} style={{ marginTop: 16 }}>步骤</Typography.Title>
            <Steps
              orientation="vertical" size="small"
              items={run.svc === 'catalog-verify' ? [
                { title: '准备输入', content: '读取 47 张表，输入指纹已绑定', status: 'finish' },
                { title: '字段级检查', content: '186 条规则；警告 34（历史缺口 28 · 上游自相矛盾 4 · 其他 2）', status: run.status === 'bad' ? 'error' : 'finish' },
                { title: '表级 / 关系级', content: '全部通过', status: 'finish' },
                { title: '写入 ServiceRun', content: '退出码 0；warning 已传播到服务检查', status: 'finish' },
              ] : [
                { title: '前置检查', content: '交易日历 · 锁 · 磁盘余量', status: 'finish' },
                { title: '执行', content: run.status === 'bad' ? '失败：上游接口超时（示意），失败记录保留' : '完成', status: run.status === 'bad' ? 'error' : 'finish' },
                { title: '产物校验', content: run.status === 'warn' ? '有警告（见证据）' : '通过', status: run.status === 'bad' ? 'wait' : 'finish' },
                { title: '写入 ServiceRun', content: run.status === 'bad' ? '退出码 1；补跑须按消费者顺序重跑下游' : '退出码 0', status: run.status === 'bad' ? 'wait' : 'finish' },
              ]}
            />
            {run.status === 'bad' && <Button style={{ marginTop: 8 }} disabled>补跑（正式版需确认，原型不可用）</Button>}
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>原型：正式版接真实 ServiceRun、journal 片段与质量收据。</Typography.Paragraph>
          </>
        )}
      </Drawer>
    </div>
  )
}
