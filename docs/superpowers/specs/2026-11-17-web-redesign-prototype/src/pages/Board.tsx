import { useMemo, useState } from 'react'
import { Drawer, Segmented, Switch, Tag, Tooltip, Descriptions, Select } from 'antd'
import { FireFilled } from '@ant-design/icons'
import type { Palette } from '../theme'
import { EChart, Num, Panel, axisStyle, tooltipStyle } from '../ui'
import { Hero } from './common'
import { PM, BAD } from '../scene'
import { YEST_RATE, YEST_TIERS, follow, ladder, promotion, stockPath, type Card, type Follow } from './marketMock'
import { MINUTES, minuteTimes } from '../mock'
import { Table } from 'antd'

const HEIGHTS = [5, 4, 3, 2, 1]
const tm = (s: string) => { const [h, m] = s.split(':').map(Number); return h + m / 60 }

function Chip({ p, c, onClick, dim }: { p: Palette; c: Card; onClick: () => void; dim: boolean }) {
  const label = c.status === 'sealed' ? '封' : c.status === 'resealed' ? '回封' : '炸'
  const col = c.status === 'broken' ? p.muted : p.up
  return (
    <div onClick={onClick} style={{ cursor: 'pointer', border: `1px solid ${c.status === 'broken' ? p.border : p.up + '66'}`, background: c.status === 'broken' ? 'transparent' : p.up + '10', borderRadius: 3, padding: '5px 7px', opacity: dim ? 0.25 : 1, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ color: p.head, fontWeight: 600, fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.name}</span>
        {c.st && <Tag style={{ margin: 0, fontSize: 10, lineHeight: '14px', padding: '0 3px' }}>ST</Tag>}
        {c.bj && <Tooltip title="北交所 · 无交易权限，仅用于情绪"><Tag style={{ margin: 0, fontSize: 10, lineHeight: '14px', padding: '0 3px' }}>北</Tag></Tooltip>}
        {c.diff && <Tooltip title={c.diff}><Tag color="warning" style={{ margin: 0, fontSize: 10, lineHeight: '14px', padding: '0 3px' }}>≠</Tag></Tooltip>}
        {c.ai != null && <Tooltip title={`AI 影子概率 ${c.ai}（非交易信号）`}><Tag color="purple" style={{ margin: 0, fontSize: 10, lineHeight: '14px', padding: '0 3px' }}>AI</Tag></Tooltip>}
        <span style={{ flex: 1 }} />
        <span style={{ color: col, fontSize: 11, whiteSpace: 'nowrap' }}>{label}{c.opens > 0 && <span style={{ color: p.muted }}>·开{c.opens}</span>}</span>
      </div>
      <div style={{ display: 'flex', gap: 6, fontSize: 11, color: p.muted, whiteSpace: 'nowrap', overflow: 'hidden' }}>
        <Num size={11} color={p.muted}>{c.code}</Num>
        <span style={{ flex: 1 }} />
        {c.seal ? <Num size={11}>{c.seal}</Num> : <Tooltip title="缺逐笔封板时间，不推算"><span style={{ color: p.warn }}>封板未知</span></Tooltip>}
        <Num size={11} color={c.status === 'broken' ? p.up : p.up}>+{c.pct.toFixed(2)}%</Num>
      </div>
    </div>
  )
}

export function Board({ p }: { p: Palette }) {
  const cards = useMemo(() => ladder(), [])
  const fl = useMemo(() => follow(cards), [cards])
  const rate = (yh: number) => Math.round((fl.filter((f) => f.yh === yh && f.out === 'up').length / YEST_TIERS[yh]) * 100)
  const [mode, setMode] = useState(() => new URLSearchParams(location.search).get('bm') ?? (PM ? 'final' : 'live'))
  const [theme, setTheme] = useState<string | null>(null)
  const [hideBroken, setHideBroken] = useState(false)
  const [sel, setSel] = useState<Card | null>(null)
  const pr = promotion()
  const themes = [...new Set(cards.map((c) => c.theme))]

  const sealOpt = useMemo(() => ({
    animation: false, grid: { left: 48, right: 16, top: 22, bottom: 24 },
    tooltip: { ...tooltipStyle(p), formatter: (q: { data: { c: Card } }) => `<b>${q.data.c.name}</b> ${q.data.c.code}<br/>${q.data.c.h} 板 · 首封 ${q.data.c.seal}${q.data.c.opens ? ` · 开板 ${q.data.c.opens} 次` : ''}` },
    // 午休折叠：13:00 之后左移 1.5 小时，与 11:30 相接
    xAxis: { type: 'value', min: 9.5, max: 13.5, interval: 0.5, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => { const t = v > 11.5 ? v + 1.5 : v; return v === 11.5 ? '11:30/13:00' : `${Math.floor(t)}:${t % 1 ? '30' : '00'}` } } },
    yAxis: { type: 'category', data: HEIGHTS.map((h) => `${h} 板`), inverse: true, ...axisStyle(p), axisLabel: { color: p.text, fontSize: 12 }, splitLine: { show: true, lineStyle: { color: p.grid } } },
    series: [{
      type: 'scatter', symbolSize: 9,
      data: cards.filter((c) => c.seal).map((c) => ({ value: [tm(c.seal!) >= 13 ? tm(c.seal!) - 1.5 : Math.min(11.5, tm(c.seal!)), HEIGHTS.indexOf(c.h)], c,
        symbol: c.status === 'broken' ? 'emptyCircle' : 'circle', itemStyle: { color: c.status === 'broken' ? p.muted : p.up, opacity: theme && c.theme !== theme ? 0.15 : 0.9 } })),
      markLine: { silent: true, symbol: 'none', data: [
        { xAxis: 11.5, lineStyle: { color: p.muted, type: 'dashed' }, label: { show: false } },
        ...(PM ? [] : [{ xAxis: 10.7, lineStyle: { color: p.accent, type: 'solid' }, label: { formatter: '现在', position: 'start', color: p.accent, fontSize: 11 } }]),
      ] },
    }],
  }), [p, cards, theme])

  const prOpt = useMemo(() => ({
    animation: false, grid: { left: 36, right: 10, top: 26, bottom: 22 },
    legend: { top: 0, right: 0, textStyle: { color: p.muted, fontSize: 11 }, itemWidth: 14, itemHeight: 2 },
    tooltip: { ...tooltipStyle(p), trigger: 'axis', valueFormatter: (v: number) => `${v}%` },
    xAxis: { type: 'category', data: pr.days, ...axisStyle(p), splitLine: { show: false } },
    yAxis: { type: 'value', max: 100, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: '{value}%' } },
    // 与梯队一致：11-13 用昨日晋级率；晚间 11-16 用今日算出的晋级率
    series: pr.rows.map((r, i) => ({ name: r.k, type: 'line', data: (() => { const d = [...r.d]; const yi = PM ? 18 : 19; d[yi] = YEST_RATE[i + 1]; if (PM) d[19] = rate(i + 1); return d })(), symbol: 'none', lineStyle: { width: 1.6, color: [p.up, p.line2, p.line][i] }, itemStyle: { color: [p.up, p.line2, p.line][i] } })),
  }), [p, fl]) // eslint-disable-line react-hooks/exhaustive-deps

  const order = (c: Card) => (c.status === 'broken' ? 2 : c.seal == null ? 1 : 0)
  const shown = cards.filter((c) => !(hideBroken && c.status === 'broken')).sort((a, b) => order(a) - order(b) || (a.seal ?? '').localeCompare(b.seal ?? ''))
  const n = (f: (c: Card) => boolean) => cards.filter(f).length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s={BAD ? 'warn' : 'ok'} icon={<FireFilled />} title={<>最高 <span style={{ color: p.up }}>5 板</span> · 涨停 {n((c) => c.status !== 'broken')} · 炸板 {n((c) => c.status === 'broken')}</>}
        sub={BAD ? '盘中实时 · 10:48:14 · 10:31–10:36 采集中断（隧道），这段的封板/开板时间缺失，不推算；盘后以 limit_list_d 定版为准' : PM ? '盘后定版 · 17:10 limit_list_d 直取 · 与 15:00 收盘实时快照相比 1 只不一致（标 ≠，以定版为准）' : '盘中实时 · 10:42:14 · 盘后 17:10 以 limit_list_d 直取定版，与盘中不一致的值标 ≠'}
        kpis={[
          { k: '1进2 晋级率', v: `${rate(1)}%`, sub: `昨 ${YEST_RATE[1]}%`, onClick: () => setMode('cmp') }, { k: '2进3', v: `${rate(2)}%`, sub: `昨 ${YEST_RATE[2]}%`, onClick: () => setMode('cmp') },
          { k: '首板封板率', v: `${Math.round((n((c) => c.h === 1 && c.status !== 'broken') / n((c) => c.h === 1)) * 100)}%` },
          { k: '最强题材', v: '算力', sub: '涨停 12 家', c: p.up },
        ]} />

      <Panel p={p} title={mode === 'cmp' ? '昨天的涨停，今天接上了吗？' : '今天的连板梯队'} info={mode === 'cmp' ? '按昨日板数分层：晋级＝今日在高一层封住；炸板＝触及涨停没封住；断板按今日涨跌分红盘、绿盘、跌停。上方是赚钱效应：昨日涨停股今天平均涨多少、几成红盘、有几只大面。' : '按连板高度分列，每张卡片一只股票：首封时间、开板次数、当前涨幅。点击卡片看分时与封板过程。ST 只认 stock_st 直取；北交所仅用于情绪。'}
        extra={<div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <Segmented size="small" value={mode} onChange={(v) => setMode(v as string)} options={[{ label: PM ? '收盘实时' : '盘中实时', value: 'live' }, { label: '盘后定版', value: 'final' }, { label: '昨日涨停今日表现', value: 'cmp' }]} />
          <Select size="small" allowClear placeholder="按题材高亮" style={{ width: 130 }} value={theme} onChange={setTheme} options={themes.map((t) => ({ value: t, label: t }))} />
          <span style={{ color: p.muted, fontSize: 12 }}>隐藏炸板 <Switch size="small" checked={hideBroken} onChange={setHideBroken} /></span>
        </div>}>
        {mode === 'final' && PM && (
          <div style={{ border: `1px solid ${p.info}55`, background: p.info + '12', borderRadius: 4, padding: '6px 10px', marginBottom: 8, fontSize: 12 }}>
            <b style={{ color: p.info }}>定版 17:10</b>　limit_list_d 直取（tushare-sync）。与 15:00 收盘实时快照相比：<span style={{ color: p.warn }}>≠ 1 只</span>（实时快照记首板封住，定版记为炸板，以定版为准；差异留在质检样本）。
          </div>
        )}
        {mode === 'y' ? <YestView p={p} fl={fl} onBack={() => setMode(PM ? 'final' : 'live')} /> : mode === 'cmp' ? <FollowView p={p} fl={fl} theme={theme} hideBroken={hideBroken} onPick={(c) => setSel(c)} /> : mode === 'final' && !PM ? (
          <div style={{ padding: '36px 0', textAlign: 'center', color: p.muted }}>
            <div style={{ color: p.head, fontSize: 15, marginBottom: 6 }}>今天的盘后定版还没有生成</div>
            <div style={{ fontSize: 12 }}>17:10 由 tushare-sync 取 limit_list_d 直取定版；现在 10:42 只有盘中实时快照。定版前不拿盘中值冒充。</div>
            <div style={{ marginTop: 12, display: 'flex', gap: 8, justifyContent: 'center' }}>
              <a onClick={() => setMode('live')}>回到盘中实时</a><span>·</span><a onClick={() => setMode('y')}>看上一交易日 11-13 定版</a>
            </div>
          </div>
        ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1.2fr 3.6fr', gap: 8 }}>
          {HEIGHTS.map((h) => {
            const col = shown.filter((c) => c.h === h)
            return (
              <div key={h} style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, padding: '2px 2px 6px', borderBottom: `2px solid ${p.up}` }}>
                  <b style={{ color: p.head, fontSize: 14 }}>{h === 1 ? '首板' : `${h} 板`}</b>
                  <Num color={p.up} size={12}>封 {col.filter((c) => c.status !== 'broken').length}</Num>
                  {col.some((c) => c.status === 'broken') && <Num color={p.muted} size={12}>炸 {col.filter((c) => c.status === 'broken').length}</Num>}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: h === 1 ? 'repeat(3, minmax(0,1fr))' : h === 2 ? 'repeat(1, minmax(0,1fr))' : '1fr', gap: 4, marginTop: 6, maxHeight: 360, overflow: 'auto' }}>
                  {col.map((c) => <Chip key={c.code} p={p} c={c} dim={!!theme && c.theme !== theme} onClick={() => setSel(c)} />)}
                </div>
              </div>
            )
          })}
        </div>
        )}
      </Panel>

      <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 10 }}>
        <Panel p={p} title="什么时候封的板？" info="每个点一只股票的首封时间；空心＝已炸板。午休时段折叠。选了题材时其他点变淡。">
          <EChart height={220} option={sealOpt} onClick={(q) => setSel((q.data as { c: Card }).c)} />
        </Panel>
        <Panel p={p} title="近 20 日接力成功率" info="前一交易日 N 板中，今日晋级为 N+1 板的比例。">
          <EChart height={220} option={prOpt} />
        </Panel>
      </div>

      <Drawer open={!!sel} onClose={() => setSel(null)} size={620} title={sel && `${sel.name} ${sel.code}`} extra={sel && <Tag color={sel.status === 'broken' ? 'default' : 'red'}>{sel.h === 1 ? '首板' : `${sel.h} 板`} · {sel.status === 'sealed' ? '封板' : sel.status === 'resealed' ? '回封' : '炸板'}</Tag>}>
        {sel && (
          <>
            <StockChart p={p} c={sel} />
            <Descriptions style={{ marginTop: 12 }} size="small" column={2} bordered items={[
              { key: 'a', label: '首封', children: sel.seal ? <Num>{sel.seal}</Num> : <span style={{ color: p.warn }}>未知（不推算）</span> },
              { key: 'b', label: '开板次数', children: <Num>{sel.opens}</Num> },
              { key: 'c', label: '题材', children: sel.theme }, { key: 'd', label: 'ST', children: sel.st ? '是（stock_st）' : '否' },
              { key: 'e', label: 'AI 影子', children: sel.ai != null ? `${sel.ai}（非交易信号）` : '—' },
              { key: 'f', label: '盘后定版', children: PM ? <span style={{ color: p.ok }}>17:10 已定版 · 一致</span> : <span style={{ color: p.muted }}>17:10 后可见</span> },
            ]} />
            <div style={{ color: p.muted, fontSize: 12, marginTop: 8 }}>纵轴为相对昨收的涨跌幅；红虚线＝直取涨停价。首封、开板、回封时间取逐笔/分钟线，缺数据时只标“未知”，不推算。</div>
          </>
        )}
      </Drawer>
    </div>
  )
}

/* ============ 昨日涨停今日表现（晋级视图） ============
   主流复盘工具的读法：按“昨日几板”分层，先看每层有几只接上（晋级率），没接上的再分炸板 / 断板红盘 / 断板绿盘 / 跌停；
   上方给“昨日涨停今日平均涨幅、红盘率、大面”这类赚钱效应指标。盘中这些是暂时结果，收盘后才定。 */
const OUT_LABEL: Record<Follow['out'], string> = { up: '晋级', brk: '炸板', red: '断板·红', green: '断板·绿', down: '跌停' }
function FollowView({ p, fl, theme, hideBroken, onPick }: { p: Palette; fl: Follow[]; theme: string | null; hideBroken: boolean; onPick: (c: Card) => void }) {
  const [tier, setTier] = useState<number | null>(null)
  const avg = (xs: Follow[]) => xs.reduce((a, x) => a + x.pct, 0) / Math.max(1, xs.length)
  const all = fl, lb = fl.filter((f) => f.yh >= 2), top = fl.filter((f) => f.yh === 4)
  const color = (o: Follow['out']) => (o === 'up' ? p.up : o === 'brk' ? p.warn : o === 'red' ? p.up : o === 'green' ? p.down : p.down)
  const tag = PM ? '' : '（盘中暂计）'
  const stats = [
    { k: '昨日涨停今日平均', v: `${avg(all) >= 0 ? '+' : ''}${avg(all).toFixed(2)}%`, c: avg(all) >= 0 ? p.up : p.down, sub: `昨 54 只${tag}` },
    { k: '红盘率', v: `${Math.round((all.filter((f) => f.pct > 0).length / all.length) * 100)}%`, sub: `${all.filter((f) => f.pct > 0).length} / ${all.length}` },
    { k: '昨日连板今日平均', v: `${avg(lb) >= 0 ? '+' : ''}${avg(lb).toFixed(2)}%`, c: avg(lb) >= 0 ? p.up : p.down, sub: `昨 2 板以上 ${lb.length} 只` },
    { k: '昨日最高板', v: `${top[0]?.pct >= 0 ? '+' : ''}${top[0]?.pct.toFixed(2)}%`, c: p.up, sub: `昨 4 板 → 今${OUT_LABEL[top[0]?.out ?? 'up']}` },
    { k: '大面（≤ −5%）', v: String(all.filter((f) => f.pct <= -5).length), c: p.down, sub: `含跌停 ${all.filter((f) => f.out === 'down').length}` },
  ]
  const rows = [4, 3, 2, 1].map((yh) => {
    const t = fl.filter((f) => f.yh === yh)
    const n = (o: Follow['out']) => t.filter((f) => f.out === o).length
    return { yh, total: t.length, up: n('up'), brk: n('brk'), red: n('red'), green: n('green'), down: n('down'), avg: avg(t) }
  })
  const seg = (r: (typeof rows)[number]) => (['up', 'brk', 'red', 'green', 'down'] as const).map((o) => ({ o, n: r[o] }))
  const tierName = (h: number) => (h === 1 ? '首板' : `${h} 板`)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', border: `1px solid ${p.border}`, borderRadius: 4 }}>
        {stats.map((x, i) => (
          <div key={x.k} style={{ padding: '6px 12px', borderLeft: i ? `1px solid ${p.border}` : 'none' }}>
            <div style={{ color: p.muted, fontSize: 11 }}>{x.k}</div>
            <Num size={16} weight={600} color={x.c ?? p.head}>{x.v}</Num>
            <div style={{ color: p.muted, fontSize: 11 }}>{x.sub}</div>
          </div>
        ))}
      </div>
      <Table size="small" pagination={false} rowKey="yh" dataSource={rows} className="qs-click"
        onRow={(r) => ({ onClick: () => setTier(tier === r.yh ? null : r.yh) })} rowClassName={(r) => (r.yh === tier ? 'qs-row-sel' : '')}
        columns={[
          { title: '昨日', dataIndex: 'yh', width: 90, render: (h: number) => <b style={{ color: p.head }}>{tierName(h)}</b> },
          { title: '家数', dataIndex: 'total', width: 60, align: 'right', render: (v) => <Num>{v}</Num> },
          { title: `今日去向${tag}`, render: (_, r) => (
            <div style={{ display: 'flex', height: 14, borderRadius: 2, overflow: 'hidden', background: p.idle }}>
              {seg(r).filter((x) => x.n).map((x) => (
                <Tooltip key={x.o} title={`${OUT_LABEL[x.o]} ${x.n}`}>
                  <div style={{ width: `${(x.n / r.total) * 100}%`, background: color(x.o), opacity: x.o === 'up' ? 1 : x.o === 'red' ? 0.45 : x.o === 'green' ? 0.45 : 0.9,
                    border: x.o === 'brk' ? `1px dashed ${p.up}` : undefined, color: '#fff', fontSize: 10, textAlign: 'center', lineHeight: '14px' }}>{x.n}</div>
                </Tooltip>))}
            </div>) },
          { title: '晋级', dataIndex: 'up', width: 56, align: 'right', render: (v) => <Num color={p.up}>{v}</Num> },
          { title: '炸板', dataIndex: 'brk', width: 56, align: 'right', render: (v) => <Num color={v ? p.warn : p.muted}>{v}</Num> },
          { title: '断板·红', dataIndex: 'red', width: 64, align: 'right', render: (v) => <Num>{v}</Num> },
          { title: '断板·绿', dataIndex: 'green', width: 64, align: 'right', render: (v) => <Num color={v ? p.down : p.muted}>{v}</Num> },
          { title: '跌停', dataIndex: 'down', width: 56, align: 'right', render: (v) => <Num color={v ? p.down : p.muted}>{v}</Num> },
          { title: '晋级率', width: 120, render: (_, r) => <span><Num weight={600} color={p.head}>{Math.round((r.up / r.total) * 100)}%</Num> {r.yh <= 3 && <span style={{ color: p.muted, fontSize: 11 }}>昨 {YEST_RATE[r.yh]}%</span>}</span> },
          { title: '今日平均', dataIndex: 'avg', width: 84, align: 'right', render: (v: number) => <Num color={v >= 0 ? p.up : p.down}>{v >= 0 ? '+' : ''}{v.toFixed(2)}%</Num> },
        ]} />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1.3fr 3.6fr', gap: 8 }}>
        {[4, 3, 2, 1].map((yh) => {
          const t = fl.filter((f) => f.yh === yh && !(hideBroken && f.out === 'brk'))
            .sort((a, b) => ['up', 'brk', 'red', 'green', 'down'].indexOf(a.out) - ['up', 'brk', 'red', 'green', 'down'].indexOf(b.out) || b.pct - a.pct)
          const up = t.filter((f) => f.out === 'up').length
          return (
            <div key={yh} style={{ minWidth: 0, opacity: tier && tier !== yh ? 0.3 : 1, transition: 'opacity .15s' }}>
              <div style={{ padding: '2px 2px 6px', borderBottom: `2px solid ${p.up}` }}>
                <b style={{ color: p.head, fontSize: 14 }}>昨{tierName(yh)} → 今{yh + 1} 板</b>
                <div style={{ fontSize: 11, color: p.muted }}>接上 <Num size={11} color={p.up}>{up}</Num> / {YEST_TIERS[yh]} · <Num size={11} color={p.head}>{Math.round((up / YEST_TIERS[yh]) * 100)}%</Num></div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: yh === 1 ? 'repeat(3, minmax(0,1fr))' : '1fr', gap: 4, marginTop: 6, maxHeight: 380, overflow: 'auto' }}>
                {t.map((f) => f.card && (f.out === 'up' || f.out === 'brk')
                  ? <Chip key={f.code} p={p} c={f.card} dim={!!theme && f.theme !== theme} onClick={() => onPick(f.card!)} />
                  : (
                    <div key={f.code} style={{ border: `1px dashed ${p.border}`, borderRadius: 3, padding: '4px 7px', opacity: theme && f.theme !== theme ? 0.25 : 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ color: p.text, fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.name}</span>
                        <span style={{ flex: 1 }} />
                        <span style={{ fontSize: 11, color: f.out === 'down' ? p.down : p.muted }}>{f.out === 'down' ? '跌停' : '断板'}</span>
                      </div>
                      <div style={{ display: 'flex', fontSize: 11 }}><Num size={11} color={p.muted}>{f.code}</Num><span style={{ flex: 1 }} /><Num size={11} color={f.pct >= 0 ? p.up : p.down}>{f.pct >= 0 ? '+' : ''}{f.pct.toFixed(2)}%</Num></div>
                    </div>
                  ))}
              </div>
            </div>
          )
        })}
      </div>
      <div style={{ color: p.muted, fontSize: 12 }}>
        昨日名单取 11-13 limit_list_d 定版（收盘仍封住的 54 只）；今日涨跌取{PM ? '收盘价' : ' 10:42 实时快照，收盘前都可能变'}。炸板＝今日触及高一层涨停价但没封住。点击上表某一层只看那一层；实心卡片可点开看分时。
      </div>
    </div>
  )
}

/** 个股分时：首封、开板、回封标记 */
function StockChart({ p, c }: { p: Palette; c: Card }) {
  const t = minuteTimes()
  const { y, lim, marks } = stockPath(c, MINUTES)
  const hm = (sec: number) => new Date(sec * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai' })
  const x = t.map(hm)
  const col = { 首封: p.up, 开板: p.warn, 回封: p.up }
  const opt = {
    animation: false, grid: { left: 46, right: 14, top: 24, bottom: 24 },
    tooltip: { ...tooltipStyle(p), trigger: 'axis', valueFormatter: (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` },
    xAxis: { type: 'category', data: x, boundaryGap: false, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, interval: 29 }, splitLine: { show: false } },
    yAxis: { type: 'value', max: Math.ceil(lim + 1), min: (v: { min: number }) => Math.floor(Math.min(-1, v.min)), ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: '{value}%' } },
    series: [{ type: 'line', data: y, symbol: 'none', lineStyle: { color: p.up, width: 1.5 }, areaStyle: { color: p.up, opacity: 0.08 },
      markLine: { silent: true, symbol: 'none', data: [{ yAxis: lim, lineStyle: { color: p.up, type: 'dashed' }, label: { formatter: `涨停 +${lim}%`, color: p.up, fontSize: 10, position: 'insideEndBottom' } }, { yAxis: 0, lineStyle: { color: p.muted, type: 'dashed' }, label: { show: false } }] },
      markPoint: { symbol: 'pin', symbolSize: 34, label: { fontSize: 10, color: '#fff' }, data: marks.map((m) => ({ coord: [m.i, y[m.i]], value: m.k, itemStyle: { color: col[m.k] } })) } }],
  }
  return (
    <div>
      <div style={{ display: 'flex', gap: 14, fontSize: 12, color: p.muted, marginBottom: 2 }}>
        <span>{c.code} · 分时{PM ? '（全天）' : '（截至 10:42）'}</span>
        {marks.map((m, i) => <span key={i}><span style={{ color: col[m.k] }}>●</span> {m.k} <Num size={12}>{x[m.i]}</Num></span>)}
      </div>
      <EChart height={240} option={opt} />
    </div>
  )
}

/** 上一交易日 11-13 定版梯队（只读） */
function YestView({ p, fl, onBack }: { p: Palette; fl: Follow[]; onBack: () => void }) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, border: `1px solid ${p.border}`, background: p.panel2, borderRadius: 4, padding: '6px 10px', marginBottom: 8, fontSize: 12 }}>
        <b style={{ color: p.head }}>11-13 周五 · 盘后定版</b><span style={{ color: p.muted }}>limit_list_d 直取 · 收盘仍封住 54 只 · 最高 4 板</span>
        <span style={{ flex: 1 }} /><a onClick={onBack}>← 回到今天</a>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1.3fr 3.6fr', gap: 8 }}>
        {[4, 3, 2, 1].map((h) => {
          const col = fl.filter((f) => f.yh === h)
          return (
            <div key={h} style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', gap: 6, alignItems: 'baseline', padding: '2px 2px 6px', borderBottom: `2px solid ${p.up}` }}>
                <b style={{ color: p.head, fontSize: 14 }}>{h === 1 ? '首板' : `${h} 板`}</b><Num color={p.up} size={12}>封 {col.length}</Num>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: h === 1 ? 'repeat(3, minmax(0,1fr))' : '1fr', gap: 4, marginTop: 6, maxHeight: 380, overflow: 'auto' }}>
                {col.map((f) => (
                  <div key={f.code} style={{ border: `1px solid ${p.up}55`, background: p.up + '0c', borderRadius: 3, padding: '4px 7px', minWidth: 0 }}>
                    <div style={{ display: 'flex' }}><span style={{ color: p.head, fontSize: 12, fontWeight: 600 }}>{f.name}</span><span style={{ flex: 1 }} /><span style={{ color: p.muted, fontSize: 11 }}>{f.theme}</span></div>
                    <Num size={11} color={p.muted}>{f.code}</Num>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
