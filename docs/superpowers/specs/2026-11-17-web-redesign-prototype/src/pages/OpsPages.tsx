import { useMemo, useState } from 'react'
import { Table, Tag, Drawer, Steps, Descriptions, Segmented, Button, Space, Timeline, App } from 'antd'
import { WarningFilled, RocketFilled, BellFilled, CheckCircleFilled } from '@ant-design/icons'
import type { Palette } from '../theme'
import { rng, walk, type Status } from '../mock'
import { EChart, Num, Panel, Spark, StatusTag, axisStyle, statusColor, tooltipStyle } from '../ui'
import { DrawerNav, Hero } from './common'
import { tradingDays } from './marketMock'
import { PM, BAD, SCENE, fmtNow, publishAlertsOpen } from '../scene'

/** 近 24 小时滚动窗口：盘中 11-15 10:45 → 11-16 10:30；晚间 11-15 20:45 → 11-16 20:30。每 15 分钟一点；不画未来 */
const H0 = PM ? 20 * 60 + 45 : 10 * 60 + 45
const hours = Array.from({ length: 96 }, (_, i) => {
  const m = (H0 + i * 15) % 1440
  const day = H0 + i * 15 >= 1440 ? '' : '昨 '
  return `${day}${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
})

/* ===================== 节点与链路 ===================== */
export function Nodes({ p }: { p: Palette }) {
  const [det, setDet] = useState<{ kind: 'link' | 'unit'; k: string } | null>(null)
  const nodes = [
    { n: 'Linux 核心节点', role: 'live-core · 数据 · Web', seed: 10, disk: [{ k: '系统盘 /', used: 31.2, total: 49, fail: 10 }, { k: '数据盘 /data', used: 1180, total: 2000, fail: 100 }] },
    { n: 'Windows 交易节点', role: 'QMT · 执行网关 · Redis', seed: 20, disk: [{ k: 'C:', used: 71.4, total: 100, fail: 10 }] },
  ]
  const res = (seed: number, base: number, vol: number, max = 100) => walk(96, seed, base, vol).map((x) => +Math.min(max, Math.max(1, x)).toFixed(1))
  const small = (title: string, data: number[], unit: string, warnAt: number, color: string) => ({
    animation: false, title: { text: title, left: 0, top: 0, textStyle: { color: p.muted, fontSize: 11, fontWeight: 'normal' } },
    grid: { left: 34, right: 8, top: 22, bottom: 20 }, tooltip: { ...tooltipStyle(p), trigger: 'axis', valueFormatter: (v: number) => `${v}${unit}` },
    xAxis: { type: 'category', data: hours, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, interval: (i: number) => i === 5 || i === 29 || i === 53 || i === 77 || i === 95, formatter: (v: string, i: number) => (i === 95 ? '现在' : v) }, splitLine: { show: false } },
    yAxis: { type: 'value', max: 100, splitNumber: 2, ...axisStyle(p) },
    series: [{ type: 'line', data, symbol: 'none', lineStyle: { color, width: 1.4 }, areaStyle: { color, opacity: 0.12 },
      markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: p.warn, type: 'dashed' }, data: [{ yAxis: warnAt }] },
      markArea: { silent: true, itemStyle: { color: p.idle, opacity: 0.18 }, label: { show: false }, data: [[{ xAxis: '09:15' }, { xAxis: PM ? '15:00' : '10:30' }]] } }],
  })
  const links = [
    { k: 'Redis 加密隧道', a: 'Linux → Windows', s: (BAD ? 'warn' : 'ok') as Status, lat: 38, last: PM ? '20:40:03' : BAD ? '10:48:18' : '10:42:13', seed: 31, note: BAD ? '10:31:05–10:36:40 中断 5m35s，已自动重连 · live-core / Paper / 健康观察依赖' : 'live-core / Paper / 健康观察依赖' },
    { k: '数据 Gateway（SSH，禁用 multiplex）', a: 'Linux → Windows', s: 'ok' as Status, lat: 120, last: PM ? '20:30:41' : '10:40:02', seed: 32, note: '强制命令；认证前有限重试' },
    { k: 'observer 只读通道', a: 'Linux → Windows', s: 'ok' as Status, lat: 95, last: PM ? '20:39:50' : '10:41:50', seed: 33, note: '已限制来源地址（#122）' },
    { k: 'QMT ⇄ 执行网关 IPC', a: 'Windows 本机', s: 'ok' as Status, lat: 4, last: PM ? '20:40:04' : '10:42:14', seed: 34, note: '' },
    { k: 'Tushare Pro', a: 'Linux → 外网', s: 'ok' as Status, lat: 210, last: PM ? '17:06:40' : '09:05:11', seed: 35, note: PM ? '最近调用：tushare-sync 日线' : '最近调用：suspend_d' },
    { k: '企业微信通知', a: 'Linux → 外网', s: (PM ? 'ok' : 'warn') as Status, lat: PM ? 410 : 640, last: PM ? '20:30:02' : '08:30:00', seed: 36, note: PM ? '20:30 日报已送达 · 08:30 曾超时后重试成功' : '08:30 一次超时后重试成功' },
  ]
  const units = [
    { u: 'qsys-live-core', node: 'Linux', s: 'ok' as Status, pid: '2128485', rs: 0, mem: '412M', up: '5 天' },
    { u: 'qsys-health-observer', node: 'Linux', s: 'ok' as Status, pid: '1983310', rs: 0, mem: '96M', up: '6 天' },
    { u: 'qsys-paper', node: 'Linux', s: (BAD ? 'warn' : 'ok') as Status, pid: '1983377', rs: 0, mem: '188M', up: '6 天' },
    { u: 'qsys-web', node: 'Linux', s: 'ok' as Status, pid: '2101422', rs: 0, mem: '240M', up: '5 天' },
    { u: 'qsys-redis-tunnel', node: 'Linux', s: 'ok' as Status, pid: BAD ? '2240117' : '1873002', rs: BAD ? 2 : 1, mem: '12M', up: BAD ? '11 分钟' : '6 天' },
    { u: 'QMT 客户端', node: 'Windows', s: 'ok' as Status, pid: '7712', rs: 0, mem: '1.9G', up: '5 天' },
    { u: '执行网关', node: 'Windows', s: 'ok' as Status, pid: '8124', rs: 0, mem: '160M', up: '5 天' },
    { u: 'Redis', node: 'Windows', s: 'ok' as Status, pid: '3304', rs: 0, mem: '84M', up: '11 天' },
    { u: 'watchdog', node: 'Windows', s: (PM ? 'ok' : 'warn') as Status, pid: '9020', rs: 0, mem: '40M', up: '5 天' },
  ]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s={PM ? 'ok' : BAD ? 'bad' : 'warn'} icon={PM ? <CheckCircleFilled /> : <WarningFilled />} title={PM ? '2 个节点在线 · 链路畅通 · 无未恢复警告' : BAD ? '链路已恢复 · 今日断线 1 次（5 分 35 秒）' : '2 个节点在线 · 链路畅通 · 2 项轻微警告'} sub={BAD ? 'Redis 隧道 10:31:05 中断、10:36:40 自动重连；期间停机闸触发、Paper 账户被标出错（见告警中心）· 常驻进程 9 / 9 active，Paper 有 2 个账户出错' : PM ? 'watchdog 11:30 后已恢复 30s 巡检 · 企业微信 08:30 曾超时，20:30 日报已送达 · 常驻进程 9 / 9 active' : 'watchdog 巡检偏慢（已确认）· 企业微信 08:30 超时后重试成功 · 常驻进程 9 / 9 active'}
        kpis={[{ k: 'Linux 系统盘余量', v: '17.8 GiB', sub: '失败线 10 GiB' }, { k: 'Windows C: 余量', v: '28.6 GiB', sub: '失败线 10 GiB' }, { k: 'Linux 内存', v: '58%' }, { k: '时钟偏差', v: '0.3s', sub: 'Linux ⇄ Windows', c: p.ok }]} />
      {nodes.map((nd) => (
        <Panel key={nd.n} p={p} title={`${nd.n} 的资源够用吗？`} info="USE 方法：使用率（曲线）、饱和度（虚线＝提醒线）、错误（下方进程表）。灰底＝交易时段。三张图共用十字光标。" extra={<span style={{ color: p.muted, fontSize: 12 }}>{nd.role}</span>}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1.1fr', gap: 10 }}>
            <EChart height={130} group={`node-${nd.seed}`} option={small('CPU %', res(nd.seed, 22, 9), '%', 85, p.line)} />
            <EChart height={130} group={`node-${nd.seed}`} option={small('内存 %', res(nd.seed + 1, 58, 3), '%', 85, p.line2)} />
            <EChart height={130} group={`node-${nd.seed}`} option={small('磁盘 IO 忙 %', res(nd.seed + 2, 8, 12), '%', 70, p.info)} />
            <div>
              <div style={{ color: p.muted, fontSize: 11, marginBottom: 6 }}>磁盘余量（红线＝失败线）</div>
              {nd.disk.map((d) => {
                const free = d.total - d.used
                return (
                  <div key={d.k} style={{ marginBottom: 12 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}><span>{d.k}</span><span><Num color={free < d.fail * 1.5 ? p.warn : p.text}>余 {free.toFixed(1)} GiB</Num><span style={{ color: p.muted }}> / {d.total}</span></span></div>
                    <div style={{ position: 'relative', height: 8, background: p.idle, borderRadius: 2, marginTop: 4 }}>
                      <div style={{ width: `${(d.used / d.total) * 100}%`, height: '100%', background: free < d.fail * 1.5 ? p.warn : p.info, borderRadius: 2 }} />
                      <div style={{ position: 'absolute', left: `${((d.total - d.fail) / d.total) * 100}%`, top: -3, bottom: -3, width: 2, background: p.bad }} />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </Panel>
      ))}
      <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 10 }}>
        <Panel p={p} title="节点之间的链路通吗？" bodyStyle={{ padding: '4px 0 0' }}>
          <Table size="small" pagination={false} rowKey="k" dataSource={links} className="qs-click" onRow={(r) => ({ onClick: () => setDet({ kind: 'link', k: r.k }) })} rowClassName={(r) => (det?.k === r.k ? 'qs-row-sel' : '')} columns={[
            { title: '链路', dataIndex: 'k', render: (v, r) => <div><span style={{ color: p.head }}>{v}</span><div style={{ color: p.muted, fontSize: 11 }}>{r.a}{r.note && ` · ${r.note}`}</div></div> },
            { title: '状态', dataIndex: 's', width: 90, render: (s: Status) => <StatusTag p={p} s={s} /> },
            { title: '延迟', dataIndex: 'lat', align: 'right', width: 80, render: (v) => <Num>{v} ms</Num> },
            { title: '近 24h', width: 130, render: (_, r) => <Spark data={walk(48, r.seed, r.lat, r.lat * 0.2).map(Math.abs)} color={statusColor(p, r.s)} height={22} area={false} /> },
            { title: '最近检查', dataIndex: 'last', width: 90, render: (v) => <Num color={p.muted} size={12}>{v}</Num> },
          ]} />
        </Panel>
        <Panel p={p} title="常驻进程" bodyStyle={{ padding: '4px 0 0' }}>
          <Table size="small" pagination={false} rowKey="u" dataSource={units} className="qs-click" onRow={(r) => ({ onClick: () => setDet({ kind: 'unit', k: r.u }) })} rowClassName={(r) => (det?.k === r.u ? 'qs-row-sel' : '')} columns={[
            { title: '进程', dataIndex: 'u', render: (v) => <Num color={p.head} size={12}>{v}</Num> }, { title: '节点', dataIndex: 'node', width: 70 },
            { title: '状态', dataIndex: 's', width: 80, render: (s: Status, r) => <StatusTag p={p} s={s} text={s === 'warn' ? (r.u === 'qsys-paper' ? '账户出错' : '偏慢') : 'active'} /> },
            { title: '重启', dataIndex: 'rs', align: 'right', width: 50, render: (v) => <Num color={v ? p.warn : p.muted}>{v}</Num> },
            { title: '内存', dataIndex: 'mem', align: 'right', width: 64, render: (v) => <Num>{v}</Num> }, { title: '运行', dataIndex: 'up', width: 56 },
          ]} />
        </Panel>
      </div>
      <Drawer open={!!det} onClose={() => setDet(null)} size={520} title={det?.k}
        extra={det && (() => { const x = det.kind === 'link' ? links.find((l) => l.k === det.k)! : units.find((u) => u.u === det.k)!; return <StatusTag p={p} s={x.s} /> })()}>
        {det?.kind === 'link' && (() => {
          const l = links.find((x) => x.k === det.k)!
          const lat = walk(96, l.seed, l.lat, l.lat * 0.2).map((v) => Math.round(Math.abs(v)))
          return (<>
            <Descriptions size="small" column={1} bordered items={[
              { key: 'a', label: '方向', children: l.a }, { key: 'b', label: '当前延迟', children: <Num>{l.lat} ms</Num> }, { key: 'c', label: '最近检查', children: <Num>{l.last}</Num> },
              { key: 'd', label: '说明', children: l.note || '—' }, { key: 'e', label: '检查方式', children: l.k.includes('Redis') ? 'health-observer 每 5s PING + stream lag' : l.k.includes('IPC') ? '执行网关心跳回执' : l.k.includes('外网') || l.k.includes('Tushare') || l.k.includes('企业微信') ? '调用时记录耗时与结果' : '只读探针，每分钟一次' },
            ]} />
            <div style={{ color: p.head, fontWeight: 600, margin: '14px 0 4px' }}>近 24 小时延迟</div>
            <EChart height={160} option={{ animation: false, grid: { left: 40, right: 10, top: 10, bottom: 22 }, tooltip: { ...tooltipStyle(p), trigger: 'axis', valueFormatter: (v: number) => `${v} ms` },
              xAxis: { type: 'category', data: hours, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, interval: 23 }, splitLine: { show: false } }, yAxis: { type: 'value', ...axisStyle(p) },
              series: [{ type: 'line', data: lat, symbol: 'none', lineStyle: { color: statusColor(p, l.s), width: 1.4 }, areaStyle: { color: statusColor(p, l.s), opacity: 0.1 } }] }} />
            <div style={{ color: p.head, fontWeight: 600, margin: '10px 0 4px' }}>最近异常</div>
            {l.s === 'warn' || l.k.includes('企业微信') ? <Timeline items={[{ content: '08:30:00 发送超时（10s），08:30:12 重试成功', color: p.warn }, ...(PM ? [{ content: '20:30:02 日报发送成功', color: p.ok }] : [])]} />
              : l.k.includes('Redis') ? <Timeline items={[{ content: '11-10 14:03 断开（Windows 重启窗口），14:41 恢复', color: p.bad }]} /> : <div style={{ color: p.muted }}>近 14 天没有异常。</div>}
          </>)
        })()}
        {det?.kind === 'unit' && (() => {
          const u = units.find((x) => x.u === det.k)!
          const linux = u.node === 'Linux'
          return (<>
            <Descriptions size="small" column={2} bordered items={[
              { key: 'a', label: '节点', children: u.node }, { key: 'b', label: 'PID', children: <Num>{u.pid}</Num> },
              { key: 'c', label: '已运行', children: u.up }, { key: 'd', label: '重启次数', children: <Num color={u.rs ? p.warn : undefined}>{u.rs}</Num> },
              { key: 'e', label: '内存', children: <Num>{u.mem}</Num> }, { key: 'f', label: linux ? 'MemoryMax' : '托管', children: linux ? <Num>{u.u === 'qsys-live-core' ? '1G' : '512M'}</Num> : (u.u === 'QMT 客户端' ? '自动恢复（不在此修改）' : '计划任务') },
              { key: 'g', label: linux ? '运行身份' : '运行账户', children: <Num size={12}>{linux ? u.u + '（组件私有账号）' : '专用执行账户'}</Num>, span: 2 },
            ]} />
            {u.rs > 0 && <div style={{ color: p.warn, fontSize: 12, marginTop: 8 }}>▲ 最近一次重启：11-10 14:41（Windows 重启窗口后隧道重连）</div>}
            <div style={{ color: p.head, fontWeight: 600, margin: '14px 0 4px' }}>最近日志（示意，已脱敏）</div>
            <pre style={{ background: p.panel2, border: `1px solid ${p.border}`, borderRadius: 4, padding: 10, fontSize: 11, color: p.text, whiteSpace: 'pre-wrap', margin: 0 }}>
{u.u === 'watchdog' ? `10:06:00 巡检间隔 40s（阈值 30s）\n${PM ? '11:30:10 巡检间隔恢复 30s\n20:40:00 巡检正常' : '10:42:00 巡检间隔 40s'}`
 : u.u === 'qsys-live-core' ? `09:25:10 auction intents=2 blocked=1\n10:05:44 rebalance late=44s intents=1\n${PM ? '15:30:12 recon ok layers=3\n20:40:00 idle heartbeat ok' : '10:42:13 heartbeat ok'}`
 : `${PM ? '20:40:0' : '10:42:1'}0 heartbeat ok\n启动后无错误日志`}
            </pre>
            <div style={{ color: p.muted, fontSize: 12, marginTop: 8 }}>页面只读：重启、停用等操作走受管入口，不在网页提供。</div>
          </>)
        })()}
      </Drawer>
    </div>
  )
}

/* ===================== 发布与版本 ===================== */
/** 组件发布：版本按提交先后 9b3c2e7 < e1d0fc1 < e6966ba < 11234d8 < f7590c0；每个组件最后一次发布＝当前版本 */
const REL: { c: string; ver: string; prev: string; at: string; hist: [string, string, Status?][] }[] = [
  { c: 'live-core', ver: 'f7590c0', prev: '11234d8', at: '11-13 21:10', hist: [['10-20', 'e1d0fc1'], ['11-10', 'e6966ba'], ['11-12', '11234d8'], ['11-13', 'f7590c0']] },
  { c: 'web', ver: '11234d8', prev: 'e6966ba', at: '11-12 21:11', hist: [['10-13', 'e1d0fc1'], ['11-10', 'e6966ba'], ['11-12', '11234d8']] },
  { c: 'paper', ver: 'e6966ba', prev: 'e1d0fc1', at: '11-10 21:12', hist: [['10-22', 'e1d0fc1'], ['11-10', 'e6966ba']] },
  { c: 'health-observer', ver: 'e6966ba', prev: 'e1d0fc1', at: '11-10 21:13', hist: [['10-21', 'e1d0fc1'], ['11-10', 'e6966ba']] },
  { c: 'tushare-sync', ver: '11234d8', prev: 'e6966ba', at: '11-12 21:14', hist: [['10-06', '9b3c2e7', 'bad'], ['10-07', '9b3c2e7'], ['11-04', 'e1d0fc1'], ['11-10', 'e6966ba'], ['11-12', '11234d8']] },
  { c: 'daily-archive', ver: 'e6966ba', prev: 'e1d0fc1', at: '11-10 21:15', hist: [['10-15', 'e1d0fc1'], ['11-10', 'e6966ba']] },
  { c: 'catalog-verify', ver: 'f7590c0', prev: '11234d8', at: '11-13 21:16', hist: [['10-23', 'e1d0fc1'], ['10-30', 'e1d0fc1', 'bad'], ['11-03', 'e1d0fc1'], ['11-10', 'e6966ba'], ['11-12', '11234d8'], ['11-13', 'f7590c0']] },
  { c: 'limit-up-shadow', ver: 'e1d0fc1', prev: '9b3c2e7', at: '11-11 21:17', hist: [['10-08', '9b3c2e7'], ['11-11', 'e1d0fc1']] },
  { c: 'daily-digest', ver: 'e1d0fc1', prev: '9b3c2e7', at: '11-06 21:18', hist: [['10-16', '9b3c2e7'], ['11-06', 'e1d0fc1']] },
]
const COMPONENTS = REL.map((r) => r.c)
export function Releases({ p }: { p: Palette }) {
  const [sel, setSel] = useState<{ c: string; ver: string; s: Status; at: string } | null>(null)
  const days = tradingDays(30)
  const events = useMemo(() => REL.flatMap((r, ci) => r.hist.map(([d, ver, s]) => ({ c: ci, d: days.indexOf(d), s: (s ?? 'ok') as Status, ver }))).filter((e) => e.d >= 0), []) // eslint-disable-line react-hooks/exhaustive-deps
  const opt = useMemo(() => ({
    animation: false, grid: { left: 120, right: 10, top: 10, bottom: 24 },
    tooltip: { ...tooltipStyle(p), formatter: (q: { data: { e: { c: number; d: number; s: Status; ver: string } } }) => `<b>${COMPONENTS[q.data.e.c]}</b>　${days[q.data.e.d]}<br/>${q.data.e.ver}　${q.data.e.s === 'ok' ? '● 成功' : '✕ 失败（已补偿，保留记录）'}${q.data.e.ver === REL[q.data.e.c].ver && q.data.e.s === 'ok' ? '<br/><span style="opacity:.7">当前版本</span>' : ''}` },
    xAxis: { type: 'category', data: days, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, interval: 2 }, splitLine: { show: false } },
    yAxis: { type: 'category', inverse: true, data: COMPONENTS, ...axisStyle(p), axisLabel: { color: p.text, fontSize: 11, fontFamily: 'Menlo, monospace' }, splitLine: { show: true, lineStyle: { color: p.grid } } },
    series: [{ type: 'scatter', symbolSize: 11, data: events.map((e) => ({ value: [e.d, e.c], e, symbol: e.s === 'bad' ? 'triangle' : 'circle',
      itemStyle: { color: statusColor(p, e.s), borderColor: e.ver === REL[e.c].ver && e.s === 'ok' ? p.head : undefined, borderWidth: e.ver === REL[e.c].ver && e.s === 'ok' ? 1.5 : 0 } })) }],
  }), [p, events]) // eslint-disable-line react-hooks/exhaustive-deps
  const rows = REL.map((r) => ({ ...r, wf: true, dep: true, rec: true, s: 'ok' as Status }))
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s="ok" icon={<RocketFilled />} title="main f7590c0 · 9 个组件全部三方一致" sub="workflow 成功 · GitHub Deployment · 节点 record 三者一致才算发布完成；test 领先 main 2 个文档提交"
        kpis={[{ k: '近 30 日发布', v: `${events.length}`, sub: `失败 ${events.filter((e) => e.s === 'bad').length}（已补偿）` }, { k: '回退目标齐全', v: '9 / 9', c: p.ok }, { k: '待清理旧制品', v: '0', sub: '受管 prune 入口' }, { k: '节点白名单', v: '与 main 一致', c: p.ok }]} />
      <Panel p={p} title="最近 30 天每个组件发布了什么？" info="每个点一次组件发布；白圈＝当前在用的那次；▲＝失败（原失败记录保留，重试用新 run-id）。点击看发布记录。">
        <EChart height={280} option={opt} onClick={(q) => { const e = (q.data as { e: { c: number; d: number; s: Status; ver: string } }).e; setSel({ c: COMPONENTS[e.c], ver: e.ver, s: e.s, at: days[e.d] }) }} />
      </Panel>
      <Panel p={p} title="组件当前版本" bodyStyle={{ padding: '4px 0 0' }}>
        <Table size="small" pagination={false} rowKey="c" dataSource={rows} className="qs-click" onRow={(r) => ({ onClick: () => setSel({ c: r.c, ver: r.ver, s: 'ok', at: r.at }) })}
          rowClassName={(r) => (r.c === sel?.c ? 'qs-row-sel' : '')} columns={[
          { title: '组件', dataIndex: 'c', render: (v) => <Num color={p.head}>{v}</Num> },
          { title: '当前', dataIndex: 'ver', render: (v) => <Tag style={{ fontFamily: 'monospace' }} color={v === 'f7590c0' ? 'orange' : undefined}>{v}</Tag> },
          { title: '回退目标', dataIndex: 'prev', render: (v) => <span title="失败补偿用；独立组件只前进不回退"><Num color={p.muted}>{v}</Num></span> },
          { title: '发布时间', dataIndex: 'at', render: (v) => <Num color={p.muted} size={12}>{v}</Num> },
          { title: 'workflow', render: () => <span style={{ color: p.ok }}>✓</span> }, { title: 'Deployment', render: () => <span style={{ color: p.ok }}>✓</span> }, { title: '节点 record', render: () => <span style={{ color: p.ok }}>✓</span> },
          { title: '结论', render: () => <StatusTag p={p} s="ok" text="一致" /> },
        ]} />
      </Panel>
      <Drawer open={!!sel} onClose={() => setSel(null)} size={520} title={sel && `${sel.c} · ${sel.ver}`} extra={sel && <StatusTag p={p} s={sel.s} text={sel.s === 'ok' ? '成功' : '失败'} />}>
        {sel && (<>
          <Descriptions size="small" column={1} bordered items={[{ key: 'a', label: '时间', children: <Num>{sel.at}</Num> }, { key: 'b', label: 'target SHA', children: <Num>{sel.ver}</Num> }, { key: 'c', label: 'run-id', children: <Num>component-deploy-…（示意）</Num> }]} />
          <Steps style={{ marginTop: 16 }} orientation="vertical" size="small" items={[
            { title: 'CI（精确 SHA）', status: 'finish' }, { title: '测试部署', status: 'finish' }, { title: '制品下载与 digest 校验', status: 'finish' },
            { title: '节点预检（白名单指纹 / 容量 / 权限）', status: 'finish' }, { title: '切换 current', status: sel.s === 'bad' ? 'error' : 'finish', content: sel.s === 'bad' ? '验收失败 → 自带补偿已恢复原 current；记录保留' : undefined },
            { title: '三方一致核验', status: sel.s === 'bad' ? 'wait' : 'finish' },
          ]} />
        </>)}
      </Drawer>
    </div>
  )
}

/* ===================== 告警中心 ===================== */
const ALERTS = [
  ...(BAD ? [
    { id: 'a7', sev: 'bad' as Status, st: '未处理', title: '停机闸触发：Redis 隧道心跳中断', src: '实盘 · 停机闸', first: '10:32:35', last: '10:32:35', n: 1, go: 'recon', why: 'live-core 与 Windows 执行网关之间的 Redis 隧道 10:31:05 起无心跳，超过 90s 阈值后 watchdog 触发停机闸', todo: '隧道已恢复，停机闸不自动解除。按“解除前核对清单”逐项确认（含券商多出的 1 笔活动委托来源），再经受管入口解除' },
    { id: 'a8', sev: 'bad' as Status, st: '未处理', title: '模拟账户 pa-01 / pa-02 标记出错', src: '模拟 · Paper worker', first: '10:31:20', last: '10:31:20', n: 2, go: 'paper', why: 'Paper worker 依赖同一条 Redis 隧道取行情，断连后按规则把运行中的账户标记为出错，停止撮合', todo: '不直接改库。恢复走备份回读与精确状态转换，先核对出错前最后一笔成交，再恢复账户' },
    { id: 'a9', sev: 'ok' as Status, st: '已恢复', title: 'Redis 加密隧道中断 5 分 35 秒', src: '节点 · Linux ⇄ Windows', first: '10:31:05', last: '10:36:40', n: 1, go: 'nodes', why: 'SSH 隧道进程退出（原因待查：Windows 侧 sshd 日志 10:31:04 有一次连接重置）', todo: 'systemd 10:36:40 自动重连成功；隧道单元重启计数 +1，原因登记 Issue 跟进' },
    { id: 'a10', sev: 'warn' as Status, st: '已确认', title: '行情快照缺失 10:31–10:36', src: '行情 · 打板采集', first: '10:31:15', last: '10:36:45', n: 23, go: 'market', why: '采集器读不到 Windows Redis 快照，按退避规则停止重试', todo: '缺失区间保持空白，不补、不插值；盘后以 limit_list_d 定版为准' },
  ] : []),
  PM
    ? { id: 'a1', sev: 'ok' as Status, st: '已恢复', title: '委托 q-1116-0004 券商未回报', src: '实盘 · 委托', first: '10:06:44', last: '15:30:12', n: 186, go: 'orders', why: '网关已 ACK 超过 60s 没有券商回报', todo: '15:30 收盘对账从券商快照确认已成交 100 股，预留随成交释放；券商受理时刻仍缺失，保留未知' }
    : { id: 'a1', sev: 'warn' as Status, st: '未处理', title: '委托 q-1116-0004 券商未回报', src: '实盘 · 委托', first: '10:06:44', last: BAD ? '10:48:14' : '10:42:14', n: BAD ? 43 : 37, go: 'orders', why: '网关已 ACK 超过 60s 没有券商回报', todo: '不重发、不释放预留；等 15:30 收盘对账确认终态' },
  { id: 'a2', sev: 'warn' as Status, st: '未处理', title: '盘中调仓时点迟到 44s', src: '实盘 · 运行', first: '10:05:44', last: '10:05:44', n: 1, go: 'runs', why: '实际触发晚于计划 44s（阈值 30s）', todo: '查看 live-core 调度日志；连续 3 次迟到升级为失败' },
  PM
    ? { id: 'a3', sev: 'ok' as Status, st: '已恢复', title: 'watchdog 巡检间隔 40s > 30s', src: '节点 · Windows', first: '10:06:00', last: '11:29:40', n: 25, go: 'nodes', why: '巡检周期变长', todo: '10:08 已确认；11:30 午休后恢复 30s，原因未定位，留在 Issue' }
    : { id: 'a3', sev: 'warn' as Status, st: '已确认', title: 'watchdog 巡检间隔 40s > 30s', src: '节点 · Windows', first: '10:06:00', last: '10:42:00', n: 12, go: 'nodes', why: '巡检周期变长', todo: '已确认：观察到收盘，不影响下单' },
  { id: 'a4', sev: 'ok' as Status, st: '已恢复', title: '企业微信通知超时', src: '节点 · 外网', first: '08:30:00', last: '08:30:12', n: 1, go: 'nodes', why: '一次超时，重试成功', todo: '—' },
  { id: 'a5', sev: 'ok' as Status, st: '已恢复', title: '行情延迟 38s', src: '行情 · 网关', first: '09:25:12', last: '09:27:30', n: 3, go: 'today', why: '竞价结束瞬间行情推送延迟', todo: '—' },
  ...(PM ? [{ id: 'a6', sev: 'warn' as Status, st: '已确认', title: 'catalog-verify 34 条警告', src: '数据 · 质检', first: '19:48:10', last: '19:48:10', n: 1, go: 'quality', why: '上游数据自相矛盾 4 条（#653）+ 首根分钟量历史缺口 28 条（#594）+ 其他 2 条', todo: '规则已标注已知问题，只警告；任务与服务检查不因此失败' }] : []),
]
export function Alerts({ p }: { p: Palette }) {
  const [f, setF] = useState('未处理')
  const [selId, setSelId] = useState<string | null>(null)
  const [acks, setAcksRaw] = useState<Record<string, string>>(() => JSON.parse(sessionStorage.getItem('qs-acks-' + SCENE) || '{}'))
  const setAcks = (fn: (a: Record<string, string>) => Record<string, string>) => setAcksRaw((a) => {
    const n = fn(a)
    sessionStorage.setItem('qs-acks-' + SCENE, JSON.stringify(n))
    publishAlertsOpen(ALERTS.filter((x) => x.st === '未处理' && !n[x.id]).length)
    return n
  })
  const { message } = App.useApp()
  const all = ALERTS.map((a) => (acks[a.id] ? { ...a, st: '已确认', ackAt: acks[a.id] } : { ...a, ackAt: a.st === '已确认' ? (a.id === 'a6' ? '19:48:10 规则自动标注已知问题，' : a.id === 'a10' ? '10:37:02 采集器自动标注缺口区间，' : '10:08:12') : '' }))
  const sel = all.find((a) => a.id === selId) ?? null
  const setSel = (a: { id: string } | null) => setSelId(a?.id ?? null)
  const cnt = (st: string) => all.filter((a) => a.st === st).length
  const days = [...tradingDays(13), '11-16']
  const r = rng(77)
  const opt = useMemo(() => ({
    animation: false, grid: { left: 30, right: 8, top: 26, bottom: 22 }, legend: { top: 0, right: 0, textStyle: { color: p.muted, fontSize: 11 } },
    tooltip: { ...tooltipStyle(p), trigger: 'axis' },
    xAxis: { type: 'category', data: days, ...axisStyle(p), splitLine: { show: false } }, yAxis: { type: 'value', minInterval: 1, ...axisStyle(p) },
    series: [
      { name: '✕ 严重', type: 'bar', stack: 'a', data: days.map((_, i) => (i === 9 ? 1 : i === 13 ? ALERTS.filter((x) => x.sev === 'bad').length : 0)), itemStyle: { color: p.bad } },
      { name: '▲ 警告', type: 'bar', stack: 'a', data: days.map((_, i) => (i === 13 ? ALERTS.filter((x) => x.sev === 'warn').length : Math.floor(r() * 4))), itemStyle: { color: p.warn } },
      { name: '● 已自动恢复', type: 'bar', stack: 'a', data: days.map((_, i) => (i === 13 ? ALERTS.filter((x) => x.sev === 'ok').length : Math.floor(r() * 3))), itemStyle: { color: p.ok, opacity: 0.6 } },
    ],
  }), [p]) // eslint-disable-line react-hooks/exhaustive-deps
  const list = all.filter((a) => f === '全部' || a.st === f)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s={all.some((a) => a.sev === 'bad' && a.st === '未处理') ? 'bad' : cnt('未处理') ? 'warn' : 'ok'} icon={<BellFilled />} title={cnt('未处理') ? `${cnt('未处理')} 条未处理 · ${all.filter((a) => a.sev === 'bad' && a.st === '未处理').length ? `其中 ${all.filter((a) => a.sev === 'bad' && a.st === '未处理').length} 条严重` : '无严重告警'}` : '没有未处理告警'} sub={`今日 ${all.length} 条：未处理 ${cnt('未处理')} · 已确认 ${cnt('已确认')} · 已自动恢复 ${cnt('已恢复')} · 同一问题自动合并计数`}
        kpis={[{ k: '未处理', v: String(cnt('未处理')), c: cnt('未处理') ? p.warn : p.ok, onClick: () => setF('未处理') }, { k: '已确认', v: String(cnt('已确认')), onClick: () => setF('已确认') }, { k: '今日已恢复', v: String(cnt('已恢复')), c: p.ok, onClick: () => setF('已恢复') }, { k: '近 14 日严重', v: BAD ? '3' : '1', c: BAD ? p.bad : undefined, sub: BAD ? '今天 2 · 11-10 1' : '11-10 Windows 重启窗口' }]} />
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 3fr', gap: 10 }}>
        <Panel p={p} title="告警是在变多还是变少？" info="按严重度统计每个交易日新产生的告警（合并后）。">
          <EChart height={260} option={opt} />
        </Panel>
        <Panel p={p} title="告警" bodyStyle={{ padding: '4px 0 0' }} extra={<Segmented size="small" value={f} onChange={(v) => setF(v as string)} options={['未处理', '已确认', '已恢复', '全部']} />}>
          <Table size="small" pagination={false} rowKey="id" dataSource={list} className="qs-click" onRow={(x) => ({ onClick: () => setSel(x) })}
            rowClassName={(x) => (x.id === selId ? 'qs-row-sel' : '')} locale={{ emptyText: <span style={{ color: p.ok }}>● 没有{f === '全部' ? '' : f}的告警</span> }} columns={[
            { title: '告警', dataIndex: 'title', render: (v, x) => <div><span style={{ color: p.head }}>{v}</span><div style={{ color: p.muted, fontSize: 11 }}>{x.src}</div></div> },
            { title: '级别', dataIndex: 'sev', width: 80, render: (s: Status, x) => <StatusTag p={p} s={s} text={x.st === '已恢复' ? '已恢复' : s === 'bad' ? '严重' : '警告'} /> },
            { title: '状态', dataIndex: 'st', width: 70 },
            { title: '首次 / 最近', width: 160, render: (_, x) => <span style={{ whiteSpace: 'nowrap' }}><Num size={12} color={p.muted}>{x.first} → {x.last}</Num></span> },
            { title: '次数', dataIndex: 'n', align: 'right', width: 60, render: (v) => <Num>{v}</Num> },
          ]} />
        </Panel>
      </div>
      <Drawer open={!!sel} onClose={() => setSel(null)} size={480} title={sel?.title} extra={sel && <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}><DrawerNav p={p} list={list} cur={list.find((x) => x.id === selId) ?? null} set={setSel} /><StatusTag p={p} s={sel.sev} text={sel.st} /></span>}>
        {sel && (<>
          <Descriptions size="small" column={1} bordered items={[
            { key: 'a', label: '来源', children: sel.src }, { key: 'b', label: '原因', children: sel.why }, { key: 'c', label: '处理建议', children: sel.todo },
            { key: 'd', label: '首次 / 最近', children: <Num>{sel.first} / {sel.last}（{sel.n} 次）</Num> },
          ]} />
          <Timeline style={{ marginTop: 16 }} items={[
            { content: `${sel.first} 首次触发`, color: statusColor(p, sel.sev === 'ok' ? 'warn' : sel.sev) },
            ...(sel.n > 1 ? [{ content: `${sel.last} 最近一次（共 ${sel.n} 次，已合并）`, color: p.muted }] : []),
            ...(sel.st === '已确认' ? [{ content: sel.ackAt.endsWith('，') ? `${sel.ackAt.slice(0, -1)}` : `${sel.ackAt} 维护者确认已知悉`, color: p.info }] : []),
            ...(sel.st === '已恢复' ? [{ content: `${sel.last} 自动恢复`, color: p.ok }] : []),
          ]} />
          <Space><Button type="primary" size="small" onClick={() => { location.hash = sel.go }}>去相关页面 →</Button>
            {sel.st === '未处理' && <Button size="small" onClick={() => { const t = fmtNow(); setAcks((a) => ({ ...a, [sel.id]: t })); message.success('已确认（只记录已知悉）') }}>确认已知悉</Button>}</Space>
          <div style={{ color: p.muted, fontSize: 12, marginTop: 12 }}>“确认”只记录已知悉，不会清除急停或改变任何交易状态。</div>
        </>)}
      </Drawer>
    </div>
  )
}
