// 数据组示意数据：表名取自现有体系的常见命名，行数、覆盖、违规均为示意。
import { rng, type Status } from '../mock'
import { PM } from '../scene'

export const DIMS = ['新鲜度', '完整性', '有效性', '一致性', '关系']
export type Table = { name: string; domain: string; layer: string; grain: string; rows: string; cover: string; fresh: string; dims: Status[]; role: string; owner: string; desc: string }
const T = (name: string, domain: string, layer: string, grain: string, rows: string, cover: string, fresh: string, dims: string, role: string, desc: string): Table => ({
  // 晚间：收盘后到达的表已更新到今天（11-16）
  name, domain, layer, grain, rows, role, owner: 'tushare-sync', desc,
  cover: PM ? cover.replace('~ 11-13', '~ 11-16') : cover, fresh: PM ? fresh.replace(/^11-13/, '11-16') : fresh,
  dims: dims.split('').map((c) => ({ o: 'ok', w: 'warn', b: 'bad', i: 'idle' }[c] as Status)),
})
export const tables: Table[] = [
  T('tushare_daily', '行情', 'canonical', '日 × 股票', '1,203万', '2005-01-04 ~ 11-13', '11-13 17:06', 'oowoo', '真值', '日线（Tushare 直连）'),
  T('daily_basic', '行情', 'canonical', '日 × 股票', '1,187万', '2005-01-04 ~ 11-13', '11-13 17:08', 'oowoo', '真值', '每日指标（市值、股本、换手）'),
  T('stk_limit', '行情', 'canonical', '日 × 股票', '1,021万', '2007-01-04 ~ 11-16', '11-16 08:58', 'ooooo', '真值', '涨跌停价（直取）'),
  T('bar_minute_1m', '行情', 'canonical', '分钟 × 股票', '41.6亿', '2019-01-02 ~ 11-13', '11-13 15:52', 'owowo', '真值', '1 分钟线（D-23 首根量 0 未结案）'),
  T('limit_list_d', '行情', 'canonical', '日 × 股票', '38.2万', '2019-01-02 ~ 11-13', '11-13 17:10', 'ooooo', '真值', '涨跌停明细（盘后定版）'),
  T('stock_st_daily', '证券', 'canonical', '日 × 股票', '312万', '2016-01-04 ~ 11-16', '11-16 08:40', 'ooooo', '真值', 'ST 名单（stock_st 直取）'),
  T('security_master', '证券', 'canonical', '股票', '5,712', '—', '11-13 17:00', 'ooooo', '真值', '证券主表（上市/退市日期）'),
  T('namechange', '证券', 'canonical', '事件', '2.1万', '1990 ~ 11-13', '11-13 17:01', 'ooooo', '真值', '名称变更（退市整理期判定）'),
  T('paid_daily', '行情', 'archive', '日 × 股票', '1,190万', '2005 ~ 09-11', '冻结', 'iooio', '历史名称唯一真值', '付费源日线（不能删）'),
  T('suspend_d', '事件', 'canonical', '日 × 股票', '86万', '2005 ~ 11-16', '11-16 09:05', 'ooooo', '真值', '停复牌'),
  T('market_regime_daily', '衍生', 'derived', '日', '4,812', '2007 ~ 11-13', '11-13 19:10', 'ooooo', '派生', '市场情绪（纯派生）'),
  T('limit_up_shadow', '衍生', 'derived', '日 × 股票', '1.8万', '2025-05 ~ 11-16', '11-13 19:35', 'ooooi', '研究', 'AI 涨停影子榜单'),
]
export const domains = [
  { d: '行情', n: 5, s: 'warn' as Status, note: '上游自相矛盾 2 表（#653，只警告）· 分钟线首根量 D-23（#594）' }, { d: '证券', n: 3, s: 'ok' as Status, note: '' },
  { d: '事件', n: 1, s: 'ok' as Status, note: '' }, { d: '衍生', n: 2, s: 'ok' as Status, note: '' }, { d: '归档', n: 1, s: 'idle' as Status, note: '冻结' },
]

/** 指标字典 */
export const metrics = [
  { id: 'pct_chg', name: '涨跌幅', kind: '直取', src: 'tushare_daily.pct_chg', s: 'ok' as Status, valid: '186 / 186', desc: '(close − pre_close) / pre_close，上游原值' },
  { id: 'circ_mv', name: '流通市值', kind: '直取', src: 'daily_basic.circ_mv', s: 'warn' as Status, valid: '上游自相矛盾 2 例', desc: '上游原值；与 float_share × close 不一致时只警告（#653）' },
  { id: 'limit_up_price', name: '涨停价', kind: '直取', src: 'stk_limit.up_limit', s: 'ok' as Status, valid: '全部通过', desc: '只认直取，不按板块档位自算' },
  { id: 'is_st', name: '是否 ST', kind: '直取', src: 'stock_st_daily.is_st', s: 'ok' as Status, valid: '全部通过', desc: '只认 stock_st，不从名称或价格推算' },
  { id: 'seal_ratio', name: '封单比', kind: '纯派生', src: 'limit_list_d.fd_amount / float_mv', s: 'ok' as Status, valid: '全部通过', desc: '直取列的确定性算术' },
  { id: 'first_bar_vol', name: '首根分钟成交量', kind: '直取', src: 'bar_minute_1m.vol', s: 'warn' as Status, valid: '28 个历史字段缺口', desc: 'D-23：首根量 0 归因未完成，禁止当填充删除（#594）' },
  { id: 'adv_ratio', name: '上涨占比', kind: '纯派生', src: 'tushare_daily', s: 'ok' as Status, valid: '全部通过', desc: '上涨家数 / 有效家数' },
  { id: 'prev_close', name: '昨收', kind: '直取', src: 'tushare_daily.pre_close', s: 'ok' as Status, valid: '全部通过', desc: '上一个交易日（#659 已修）' },
]
export function violationTrend(seed: number, n = 30) {
  const r = rng(seed)
  return Array.from({ length: n }, (_, i) => Math.max(0, Math.round((i > 20 ? 2 : 6) * r())))
}

/** 数据质量：近 30 次 catalog-verify，按层（字段/表/关系）× 结果 */
export function qualityRuns() {
  const r = rng(555)
  return Array.from({ length: 30 }, (_, i) => {
    const warn = i === 29 ? 34 : 28 + Math.round(r() * 8)
    const block = i === 9 ? 2 : 0 // 10-19 曾有 2 条阻断（示意），已修复后重验
    return { pass: 186 - warn - block, warn, block }
  })
}
export const rules = [
  { id: 'R-daily-001', obj: 'tushare_daily', layer: '字段', name: 'pct_chg 与 close/pre_close 恒等', s: 'warn' as Status, v: 2, rows: '1,203万', known: '上游数据自相矛盾', issue: '#653' },
  { id: 'R-basic-014', obj: 'daily_basic', layer: '字段', name: 'circ_mv ≈ float_share × close', s: 'warn' as Status, v: 2, rows: '1,187万', known: '上游数据自相矛盾', issue: '#653' },
  { id: 'R-min-003', obj: 'bar_minute_1m', layer: '字段', name: '首根成交量 > 0', s: 'warn' as Status, v: 28, rows: '41.6亿', known: '历史字段缺口', issue: '#594' },
  { id: 'R-limit-002', obj: 'stk_limit', layer: '表', name: '交易日全覆盖', s: 'ok' as Status, v: 0, rows: '1,021万', known: '', issue: '' },
  { id: 'R-rel-007', obj: 'limit_list_d → tushare_daily', layer: '关系', name: '涨停明细 ⊆ 日线', s: 'ok' as Status, v: 0, rows: '38.2万', known: '', issue: '' },
  { id: 'R-st-001', obj: 'stock_st_daily', layer: '字段', name: '日期为交易日', s: 'ok' as Status, v: 0, rows: '312万', known: '', issue: '' },
  { id: 'R-sus-004', obj: 'suspend_d', layer: '字段', name: '退市当日以收盘后为准', s: 'ok' as Status, v: 0, rows: '86万', known: '', issue: '#551' },
]
