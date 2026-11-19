import { Select } from 'antd'
import type { TradingDayInfo } from '../../api/contracts.gen'
import { setSearch, useSearch } from '../../shell/router'

const DAY = /^\d{4}-\d{2}-\d{2}$/

export function useTradingDay(): string | null {
  const raw = useSearch().get('day')
  return raw && DAY.test(raw) ? raw : null
}

export function DaySelect({ value }: { value: TradingDayInfo }) {
  return <Select size="small" aria-label="交易日" value={value.selected ?? undefined} style={{ minWidth: 140 }}
    options={value.available.map((d) => ({ value: d, label: d }))}
    onChange={(d) => setSearch({ day: d })} />
}

export const yuan = (v: number | null | undefined) => v == null ? null : `¥${v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
export const label = (s: string) => ({ buy: '买入', sell: '卖出', limit: '限价', market: '市价',
  completed: '完成', failed: '失败', accepted: '券商受理', sent: '已发送', created: '意图生成',
  risk_passed: '风控通过', partially_filled: '部分成交', filled: '已成交', cancelled: '已撤',
  rejected: '已拒绝', unknown: '状态未知', 'dispatch:unknown': '发送结果未知' } as Record<string, string>)[s] ?? s
