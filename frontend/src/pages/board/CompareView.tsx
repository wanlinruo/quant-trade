import { useState } from 'react'
import { Button } from 'antd'
import type { BoardData, CompareGroup, CompareItem, ShadowInfo } from '../../api/board'
import { pct, signedPct } from '../../page/format'
import { Value } from '../../page/Value'
import { hms, parseLocal } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Num } from '../../ui/Num'
import { LayerTable, levelLabel } from './LayerTable'

export const EFFECT_INFO = '昨日涨停 = 昨日连板高度 ≥ 1 的个股（非 ST 取 limit_list_d，ST 取 daily_flags）。今日涨跌幅 = 实时价 ÷ 昨收 − 1，无报价的票不进入平均、单独计数。断板 = 今日未封未炸，按涨跌幅 > 0 / ≤ 0 分红绿；大面 = 今日涨跌幅 ≤ −5%（含跌停）。晋级率的“昨日”取前一交易日看板同层。以上口径为设计者决定。'

function statusText(i: CompareItem, live: boolean): string {
  switch (i.status) {
    case 'promoted': return '晋级'
    case 'broken': return '炸板'
    case 'limit_down': return '跌停'
    case 'unknown': return '封板未知'
    case 'rush': return live ? '冲板中' : '断板'
    default: return '断板'
  }
}

function Effect({ id, k, provisional, children }: { id: string; k: string; provisional: boolean; children: React.ReactNode }) {
  const p = usePalette()
  return (
    <div data-testid={id} style={{ background: p.panel2, border: `1px solid ${p.border}`, borderRadius: 4, padding: '6px 10px', minWidth: 0 }}>
      <div style={{ fontSize: 11, color: p.muted }}>{k}{provisional && ' · 暂计'}</div>
      <div style={{ fontSize: 14 }}>{children}</div>
    </div>
  )
}

function Item({ i, live, shadow, onPick }: { i: CompareItem; live: boolean; shadow: ShadowInfo | null | undefined; onPick(s: string): void }) {
  const p = usePalette()
  const color = i.pct === null ? p.muted : i.pct > 0 ? p.up : i.pct < 0 ? p.down : p.muted
  const promoted = i.status === 'promoted'
  const seal = parseLocal(i.first_seal)
  const ready = shadow?.state === 'ready'
  return (
    <div data-item={i.symbol} role="button" tabIndex={0} onClick={() => onPick(i.symbol)} onKeyDown={(e) => { if (e.key === 'Enter') onPick(i.symbol) }}
      style={{ cursor: 'pointer', display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) 64px minmax(0, 1.2fr) 72px', gap: 6, alignItems: 'center',
        padding: '4px 6px', borderBottom: `1px solid ${p.border}`, opacity: promoted || i.status === 'rush' || i.status === 'unknown' ? 1 : 0.7, fontSize: 12 }}>
      <span style={{ color: p.head, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{i.name || i.symbol}</span>
      <Num color={color}>{signedPct(i.pct) ?? '无报价'}</Num>
      <span style={{ color: promoted ? p.up : p.muted }}>
        {statusText(i, live)}{promoted && seal && ` · ${live ? '≈' : ''}${hms(seal)}`}{promoted && i.open_times ? ` · 开 ${i.open_times}` : ''}
      </span>
      <span style={{ color: p.muted, textAlign: 'right' }}>
        {!ready ? '—' : i.prediction === null ? '无预测' : <Num color={p.info}>{pct(i.prediction)}</Num>}
      </span>
    </div>
  )
}

function Group({ g, live, shadow, onPick }: { g: CompareGroup; live: boolean; shadow: ShadowInfo | null | undefined; onPick(s: string): void }) {
  const p = usePalette()
  const [open, setOpen] = useState(false)
  const keep = (i: CompareItem) => i.status === 'promoted' || i.status === 'rush' || i.status === 'unknown'
  const shown = g.level === 1 && !open ? g.items.filter(keep) : g.items
  const hidden = g.items.length - shown.length
  return (
    <section data-testid={`group-${g.level}`} style={{ border: `1px solid ${p.border}`, borderRadius: 4, marginBottom: 8, minWidth: 0 }}>
      <header style={{ padding: '6px 8px', borderBottom: `1px solid ${p.border}`, fontSize: 12, color: p.head }}>
        {levelLabel(g.level)} → 今 {g.level + 1} 板 · 晋级 <Num color={p.up}>{g.promoted}/{g.items.length}</Num>
      </header>
      {shown.map((i) => <Item key={i.symbol} i={i} live={live} shadow={shadow} onPick={onPick} />)}
      {hidden > 0 && <Button size="small" type="link" onClick={() => setOpen(true)}>{`其余 ${hidden} 只 展开`}</Button>}
    </section>
  )
}

function ShadowHead({ shadow, live }: { shadow: ShadowInfo | null | undefined; live: boolean }) {
  const p = usePalette()
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', fontSize: 12, color: p.muted, marginBottom: 6 }}>
      <span style={{ color: p.info, border: `1px solid ${p.info}66`, padding: '0 6px', borderRadius: 3 }}>AI 影子 · 非交易信号</span>
      {!shadow ? <span>影子榜单未配置</span> : shadow.state !== 'ready' ? <span>{shadow.message}</span> : (
        <>
          <span>模型 {shadow.model_version}</span>
          <span>滚动 P@20 {pct(shadow.precision_at_20) ?? '未知'}</span>
          <span>{live ? `右列为昨日预测（${shadow.prediction_date}）的今日晋级概率` : '右列为今日晋级者的明日晋级概率'}</span>
          {live && shadow.hits !== null && <span>昨日预测已晋级 {shadow.hits} 只</span>}
        </>
      )}
    </div>
  )
}

/** 昨日涨停今日表现（§8.9）：赚钱效应 → 晋级表 → 分列卡片；数字全部取后端派生。 */
export function CompareView({ data, onPick }: { data: BoardData; onPick(s: string): void }) {
  const p = usePalette()
  const e = data.effect
  const meta = (data.meta ?? {}) as { heights_error?: string | null }
  if (!e) {
    return <div style={{ color: p.warn }}>{meta.heights_error ? `昨日连板高度不可用：${meta.heights_error}` : '昨日没有涨停个股记录'}</div>
  }
  const prov = !!data.provisional
  const live = data.mode === 'live'
  const color = (v: number | null) => (v === null ? undefined : v > 0 ? p.up : v < 0 ? p.down : undefined)
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8, marginBottom: 12 }}>
        <Effect id="effect-avg" k="昨日涨停今日平均" provisional={prov}>
          <Value v={signedPct(e.avg_pct)} why="昨日涨停个股今日均无报价" color={color(e.avg_pct)} />
          <div style={{ fontSize: 11, color: p.muted }}>有报价 {e.priced}/{e.total}{e.no_quote > 0 && `，无报价 ${e.no_quote} 只`}</div>
        </Effect>
        <Effect id="effect-red" k="红盘率" provisional={prov}><Value v={pct(e.red_rate)} why="昨日涨停个股今日均无报价" /></Effect>
        <Effect id="effect-multi" k="昨日连板今日平均" provisional={prov}>
          <Value v={signedPct(e.multi_avg_pct)} why="昨日没有连板个股，或它们今日均无报价" color={color(e.multi_avg_pct)} />
        </Effect>
        <Effect id="effect-loss" k="大面家数（≤ −5%）" provisional={prov}><Num color={p.down}>{e.big_loss}</Num></Effect>
        <Effect id="effect-top" k={`昨日最高板（${e.top_level} 板）今日`} provisional={prov}>
          {e.top_items.map((t) => (
            <div key={t.symbol} style={{ fontSize: 12 }}>
              {t.name || t.symbol} <Num color={color(t.pct)}>{signedPct(t.pct) ?? '无报价'}</Num>{' '}
              <span style={{ color: p.muted }}>{statusText({ ...t, column: null, first_seal: null, open_times: null, prediction: null }, live)}</span>
            </div>
          ))}
        </Effect>
      </div>
      <LayerTable rows={data.layers ?? []} />
      <ShadowHead shadow={data.shadow} live={live} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', gap: 8 }}>
        {(data.compare ?? []).map((g) => <Group key={g.level} g={g} live={live} shadow={data.shadow} onPick={onPick} />)}
      </div>
    </>
  )
}
