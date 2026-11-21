import { useCallback, useMemo, useState } from 'react'
import { Button, DatePicker, Descriptions, Drawer, Result, Table, Tabs, Tag } from 'antd'
import dayjs from 'dayjs'
import { ackAlert, fetchAlertDetail, fetchAlerts, type AlertCenter, type AlertItem } from '../../api/alerts'
import { EChart, type EChartsOption } from '../../page/EChart'
import { DrawerNav } from '../../page/DrawerNav'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { usePolling } from '../../page/usePolling'
import { setSearch, useSearch } from '../../shell/router'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import type { Status } from '../../theme/status'
import { MONO } from '../../ui/Num'
import { StatusTag } from '../../ui/StatusTag'

const DAY = /^\d{4}-\d{2}-\d{2}$/
const STATUS: Record<AlertItem['status'], [Status, string]> = {
  unhandled: ['bad', '未处理'], acknowledged: ['warn', '已确认'], resolved: ['ok', '已恢复'], notice: ['idle', '通知'],
}
const SEVERITY: Record<AlertItem['severity'], [Status, string]> = { CRITICAL: ['bad', '严重'], WARNING: ['warn', '警告'], INFO: ['idle', '提示'] }
const TABS = [
  { key: 'unhandled', label: '未处理', pick: (i: AlertItem) => i.status === 'unhandled' },
  { key: 'acknowledged', label: '已确认', pick: (i: AlertItem) => i.status === 'acknowledged' },
  { key: 'resolved', label: '已恢复', pick: (i: AlertItem) => i.status === 'resolved' },
  { key: 'all', label: '全部', pick: () => true },
] as const
export const ACK_NOTE = '只记录已知悉，不改变任何交易状态'

function AlertDrawer({ item, rows, day, onPick, onClose, onAcked }: {
  item: AlertItem; rows: AlertItem[]; day: string | null; onPick(item: AlertItem): void; onClose(): void; onAcked(): void
}) {
  const p = usePalette()
  const { data: d, error } = useFetch((s) => fetchAlertDetail(item.key, item.subject, day, s), [item.key, item.subject, day])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const ack = async () => {
    setBusy(true); setErr(null)
    try { await ackAlert(item.key, item.subject, item.last_at); onAcked(); onClose() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const canAck = !day && (item.status === 'unhandled')
  return (
    <Drawer open onClose={onClose} size="large" title={item.title} destroyOnHidden
      extra={<DrawerNav rows={rows} current={item} identify={(x) => `${x.key}|${x.subject}`} onPick={onPick} copyText={item.key} />}>
      <Descriptions size="small" bordered column={1} items={[
        { key: 'k', label: '告警键', children: <span style={{ fontFamily: MONO }}>{item.key}</span> },
        { key: 'd', label: '说明', children: item.description ?? '未登记键：受管清单中没有说明' },
        { key: 's', label: '对象', children: item.subject || '—' }, { key: 'src', label: '来源', children: item.source },
        { key: 'st', label: '状态', children: <StatusTag s={STATUS[item.status][0]} text={STATUS[item.status][1]} /> },
        { key: 'a', label: '最近确认', children: item.ack ? `${item.ack.by} · ${dateTime(item.ack.acked_at)}（确认到 ${dateTime(item.ack.through)}）` : '无' },
      ]} />
      <div style={{ margin: '12px 0', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {canAck && <Button type="primary" loading={busy} onClick={ack}>确认已知悉</Button>}
        {canAck && <span style={{ fontSize: 12, color: p.muted }}>{ACK_NOTE}</span>}
        {day && <span style={{ fontSize: 12, color: p.warn }}>回看 {day}：只读，不能确认</span>}
        {err && <span role="alert" style={{ color: p.bad }}>{err}</span>}
      </div>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>时间线</div>
      {error && <div style={{ color: p.bad }}>{error}</div>}
      {!d && !error && <div style={{ color: p.muted }}>加载中</div>}
      {d && (
        <Table size="small" pagination={false} rowKey="event_id" dataSource={d.events} scroll={{ x: 'max-content' }}
          columns={[
            { title: '时间', dataIndex: 'occurred_at', render: (v: string) => dateTime(v) },
            { title: '状态', dataIndex: 'state', render: (v: string) => ({ firing: '发生', resolved: '恢复', notice: '通知' } as Record<string, string>)[v] ?? v },
            { title: '标题', dataIndex: 'title' }, { title: '详情', dataIndex: 'detail', render: (v: string) => <span style={{ whiteSpace: 'pre-wrap' }}>{v}</span> },
            { title: '投递', key: 'dv', render: (_: unknown, e) => e.failed.length ? <span style={{ color: p.bad }}>失败：{e.failed.join('、')}</span> : `${e.delivered} 个通道` },
          ]} />
      )}
    </Drawer>
  )
}

/** 告警中心（§8.2）：未处理结论、14 日趋势、分标签告警表、抽屉与“确认已知悉”（只记录）。 */
export function AlertsPage() {
  const p = usePalette()
  const q = useSearch()
  const rawDay = q.get('day')
  const day = rawDay && DAY.test(rawDay) ? rawDay : null
  const fetcher = useCallback((s: AbortSignal) => fetchAlerts(day, s), [day])
  const poll = usePolling(fetcher, { intervalMs: 60_000, enabled: !day })
  const [tab, setTab] = useState<string>('unhandled')
  const [picked, setPicked] = useState<AlertItem | null>(null)
  const d = poll.data
  const option = useMemo<EChartsOption | null>(() => {
    if (!d || !d.enabled || d.trend.length === 0) return null
    const days = d.trend.map((t) => t.day.slice(5))
    const bar = (name: string, k: 'critical' | 'warning' | 'info' | 'resolved', color: string) =>
      ({ name, type: 'bar' as const, stack: k === 'resolved' ? 'r' : 'a', data: d.trend.map((t) => t[k]), itemStyle: { color } })
    return { grid: { left: 32, right: 12, top: 28, bottom: 24 }, legend: { top: 0, textStyle: { color: p.muted } }, tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: days }, yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: p.grid } } },
      series: [bar('严重', 'critical', p.bad), bar('警告', 'warning', p.warn), bar('通知', 'info', p.muted), bar('已恢复', 'resolved', p.ok)] }
  }, [d, p])
  if (!d) return poll.error ? <Hero s="bad" title="告警中心接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />
  if (!d.enabled) return <Result status="info" title="告警中心未启用" />
  const c = d as AlertCenter
  const hero = c.state === 'unknown'
    ? <Hero s="idle" title="告警存储不可读" subtitle={Object.values(c.missing).join('；')} />
    : <Hero s={c.unhandled_critical ? 'bad' : c.unhandled ? 'warn' : 'ok'} title={`未处理 ${c.unhandled} 条（其中严重 ${c.unhandled_critical} 条）`}
        subtitle={`已确认 ${c.acknowledged} 条 · 通知 ${c.notices} 条 · 窗口 ${c.window_start} ~ ${c.window_end}${day ? ' · 回看只读' : ''}`} />
  const current = TABS.find((t) => t.key === tab) ?? TABS[0]
  const rows = c.items.filter(current.pick)
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginBottom: 8 }}>
        <DatePicker size="small" allowClear placeholder="回看某日" value={day ? dayjs(day) : null}
          onChange={(v) => setSearch({ day: v ? v.format('YYYY-MM-DD') : null })} />
      </div>
      {hero}
      <Panel title="近 14 日告警趋势">{option ? <EChart option={option} height={200} /> : <div style={{ color: p.muted }}>没有趋势数据</div>}</Panel>
      <Panel title="有哪些事要我处理？">
        <Tabs size="small" activeKey={current.key} onChange={setTab}
          items={TABS.map((t) => ({ key: t.key, label: `${t.label}（${c.items.filter(t.pick).length}）` }))} />
        <div data-testid="alerts-table">
          <Table<AlertItem> size="small" rowKey={(r) => `${r.key}|${r.subject}`} dataSource={rows} scroll={{ x: 'max-content' }}
            pagination={{ pageSize: 30, hideOnSinglePage: true }}
            onRow={(r) => ({ onClick: () => setPicked(r), style: { cursor: 'pointer' } })}
            columns={[
              { title: '级别', dataIndex: 'severity', render: (v: AlertItem['severity']) => <StatusTag s={SEVERITY[v][0]} text={SEVERITY[v][1]} /> },
              { title: '标题', dataIndex: 'title', render: (v: string, r) => <span>{v}{r.kind === 'unregistered' && <Tag color="red" style={{ marginLeft: 6 }}>未登记键</Tag>}</span> },
              { title: '对象', dataIndex: 'subject', render: (v: string) => v || '—' }, { title: '来源', dataIndex: 'source' },
              { title: '首次', dataIndex: 'first_at', render: (v: string) => dateTime(v) }, { title: '最近', dataIndex: 'last_at', render: (v: string) => dateTime(v) },
              { title: '次数', dataIndex: 'count', align: 'right' },
              { title: '状态', dataIndex: 'status', render: (v: AlertItem['status']) => <StatusTag s={STATUS[v][0]} text={STATUS[v][1]} /> },
            ]} />
        </div>
      </Panel>
      {picked && <AlertDrawer item={picked} rows={rows} day={day} onPick={setPicked} onClose={() => setPicked(null)} onAcked={poll.refresh} />}
    </>
  )
}
