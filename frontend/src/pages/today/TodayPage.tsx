import { Result, Table } from 'antd'
import { useMemo } from 'react'
import { fetchToday } from '../../api/today'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { pct } from '../../page/format'
import { useFetch } from '../../page/useFetch'
import { dateTime } from '../../shell/time'
import { navigate } from '../../shell/router'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { StatusTag } from '../../ui/StatusTag'
import { DaySelect, useTradingDay, yuan } from '../trading/common'

const phaseTag = (s: string) => <StatusTag s={s === 'ok' ? 'ok' : s === 'warning' ? 'warn' : s === 'failed' ? 'bad' : s === 'running' ? 'running' : 'idle'} text={{ ok: '完成', warning: '警告', failed: '失败', running: '进行中', not_started: '未到', unknown: '未知' }[s] ?? s} />

export function TodayPage() {
  const p = usePalette(); const day = useTradingDay(); const { data, error } = useFetch((s) => fetchToday(day, s), [day])
  const sentiment = useMemo<EChartsOption | null>(() => data?.enabled && data.sentiment.length ? ({
    grid: { left: 46, right: 16, top: 15, bottom: 25 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: data.sentiment.map((x) => x.day) }, yAxis: { type: 'value', splitLine: { lineStyle: { color: p.grid } } },
    series: [{ name: '涨停', type: 'line', connectNulls: false, showSymbol: false, data: data.sentiment.map((x) => x.limit_up) },
      { name: '跌停', type: 'line', connectNulls: false, showSymbol: false, data: data.sentiment.map((x) => x.limit_down) }],
  }) : null, [data, p])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="今日运行数据源未配置" />
  const d = data; const hero = d.state === 'running' ? 'running' : d.state === 'reduce_only' ? 'bad' : d.state === 'unknown' ? 'warn' : 'idle'
  const allLinkTimes = d.links.flatMap((x) => x.points.map((v) => new Date(v.sampled_at).getTime())).filter(Number.isFinite)
  const linkMin = Math.min(...allLinkTimes); const linkMax = Math.max(...allLinkTimes)
  return <>
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}><DaySelect value={{ selected: d.day, available: d.days }} /></div>
    <Hero s={hero} title={`${d.title}${d.concerns.length ? ` · ${d.concerns.length} 项需要留意` : ''}`}
      subtitle="账户总资产为原始观测；页面只组合已有事实，不提供交易或运维控制。"
      kpis={[{ k: '账户总资产', v: d.kpis.account_total == null ? <Missing why={d.missing.live ?? '未记录'} /> : yuan(d.kpis.account_total), onClick: () => navigate('live') },
        { k: d.kpis.index_name ?? '上证指数', v: d.kpis.index_price ?? <Missing why={d.missing.market ?? '未记录'} />, sub: d.kpis.index_change_ratio == null ? undefined : pct(d.kpis.index_change_ratio), onClick: () => navigate('market') },
        { k: '涨停 / 跌停', v: d.kpis.limit_up == null || d.kpis.limit_down == null ? <Missing why="市场快照缺失" /> : `${d.kpis.limit_up} / ${d.kpis.limit_down}`, sub: d.kpis.seal_ratio == null ? undefined : `封板率 ${pct(d.kpis.seal_ratio)}`, onClick: () => navigate('board') },
        { k: '链路心跳', v: d.kpis.link_max_age_seconds == null ? <Missing why={d.missing.links ?? '未采样'} /> : `${d.kpis.link_max_age_seconds.toFixed(1)}s`, sub: d.kpis.link_total == null ? undefined : `${d.kpis.link_fresh}/${d.kpis.link_total} 新鲜`, onClick: () => navigate('nodes') }]} />
    {d.concerns.length > 0 && <Panel title="现在需要留意什么？"><div style={{ display: 'grid', gap: 8 }}>{d.concerns.map((x) => <button key={x.id} onClick={() => navigate(x.page)} style={{ textAlign: 'left', border: `1px solid ${p.border}`, background: p.panel2, color: p.text, padding: 9, cursor: 'pointer' }}>{x.severity === 'critical' ? '✕' : '▲'} {x.label}{x.detail ? ` · ${x.detail}` : ''}</button>)}</div></Panel>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', marginBottom: 12 }}>{d.phases.map((x) => <section key={x.id} style={{ padding: 10, border: `1px solid ${p.border}`, background: p.panel }}><b>{x.label}</b><div style={{ float: 'right' }}>{phaseTag(x.state)}</div><div style={{ color: p.muted, fontSize: 11, marginTop: 7 }}>{x.time_label} · {x.completed}/{x.recorded} 记录完成</div></section>)}</div>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(420px,100%),1fr))', gap: 12 }}>
      <Panel title="大盘今天怎么走的？" info="上证 1 分钟分时；缺失时不从当前价生成曲线。"><Missing why={d.missing.index_intraday} /> <span style={{ color: p.muted }}>{d.missing.index_intraday}</span></Panel>
      <Panel title="策略事件按时完成了吗？" info="点击事件进入运行记录查看同日证据。"><Table size="small" pagination={false} rowKey="event_key" dataSource={d.events} onRow={() => ({ onClick: () => navigate('runs'), style: { cursor: 'pointer' } })} columns={[
        { title: '计划', dataIndex: 'scheduled_at', render: (v) => v ? dateTime(v) : <Missing why="计划时刻未记录" /> }, { title: '阶段', dataIndex: 'phase' }, { title: '结果', dataIndex: 'state', render: (v) => phaseTag(v === 'completed' ? 'ok' : 'failed') }, { title: '摘要', dataIndex: 'summary', render: (v, x) => x.detail || v || '—' },
      ]} /></Panel>
    </div>
    <Panel title="交易链路实际采样怎样？" info="只画实际采样点；点与点之间不推断在线，红点表示该次采样已过期。">
      {allLinkTimes.length > 0 && <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '7px 10px', minWidth: 520, marginBottom: 14 }}>
        {d.links.flatMap((link) => [<b key={`${link.source}-label`} style={{ fontSize: 12 }}>{link.source}</b>, <div key={`${link.source}-track`} style={{ position: 'relative', height: 20, borderBottom: `1px solid ${p.border}` }}>
          {link.points.map((point, i) => { const at = new Date(point.sampled_at).getTime(); const left = linkMax === linkMin ? 50 : 2 + 96 * (at - linkMin) / (linkMax - linkMin); return <span key={`${point.sampled_at}-${i}`} title={`${dateTime(point.sampled_at)} · ${point.fresh == null ? '未知' : point.fresh ? '新鲜' : '过期'}`} style={{ position: 'absolute', left: `${left}%`, bottom: -5, width: 9, height: 9, borderRadius: '50%', background: point.fresh == null ? p.muted : point.fresh ? p.ok : p.bad, transform: 'translateX(-50%)' }} /> })}
        </div>])}
      </div>}
      <Table size="small" pagination={false} rowKey="source" dataSource={d.links.map((x) => ({ source: x.source, latest: x.points.at(-1), count: x.points.length }))} columns={[
      { title: '环节', dataIndex: 'source' }, { title: '最后采样', key: 'at', render: (_, x) => x.latest ? dateTime(x.latest.sampled_at) : '—' }, { title: '状态', key: 'fresh', render: (_, x) => !x.latest ? <Missing why="无采样" /> : <StatusTag s={x.latest.fresh == null ? 'idle' : x.latest.fresh ? 'ok' : 'bad'} text={x.latest.fresh == null ? '未知' : x.latest.fresh ? '新鲜' : '过期'} /> }, { title: '年龄 / 阈值', key: 'age', render: (_, x) => x.latest?.age_seconds == null ? '—' : `${x.latest.age_seconds.toFixed(1)}s / ${x.latest.threshold_seconds?.toFixed(1) ?? '—'}s` }, { title: '采样点', dataIndex: 'count' },
    ]} /></Panel>
    <Panel title="近 20 日短线情绪怎样？" info="只展示直取/既有纯派生字段；不合成未定义的‘情绪温度’。">{sentiment ? <EChart option={sentiment} height={220} /> : <Missing why="市场情绪历史不可读" />}</Panel>
  </>
}
