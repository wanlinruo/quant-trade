import type { MarketPayload, MarketSnapshot, RegimeRow } from '../../api/market'

export const COLUMNS = ['dt', 'limit_up_count', 'limit_down_count', 'broken_board_count', 'market_advance_ratio', 'index_ma20_slope_5d']

export function snapshot(over: Partial<MarketSnapshot> = {}): MarketSnapshot {
  return {
    session: '2026-11-17', published_at: '2026-11-17T10:42:00',
    indices: [{ symbol: '000001.SH', name: '上证指数', price: 3401.2, change_pct: 0.0081, amount: 4.1e11 },
      { symbol: '399001.SZ', name: '深证成指', price: 10800.5, change_pct: -0.0042, amount: 5.0e11 }],
    valid_stock_count: 5438, advance_count: 3388, decline_count: 1900, flat_count: 150,
    limit_up_count: 54, limit_down_count: 3, touched_limit_up_count: 70, broken_board_count: 16,
    broken_board_rate: 0.2286, market_advance_ratio: 0.623, market_median_return_1d: 0.0081,
    market_total_money: 1.2e12, limit_price_missing_count: 2, limit_up_unknown_count: null, limit_down_unknown_count: 1,
    coverage: {
      status: 'complete', universe_source: 'stock_basic', universe_session: '2026-11-17', universe_sha256: null,
      expected: 5441, excluded_non_a: 0, invalid_identity_count: 0, unexpected_count: 0,
      by_market: [
        { market: 'SH', expected: 2300, received: 2300, valid: 2299, suspended: 1, missing: 0, invalid: 0, other_session: 0, missing_samples: [], source_at_min: '2026-11-17T10:41:58', source_at_max: '2026-11-17T10:42:00' },
        { market: 'BJ', expected: 270, received: 268, valid: 268, suspended: 0, missing: 2, invalid: 0, other_session: 0, missing_samples: ['920001.BJ'], source_at_min: null, source_at_max: null },
      ],
    },
    ...over,
  }
}

export function regime(): RegimeRow[] {
  return [
    { dt: '2026-11-13', limit_up_count: 40, limit_down_count: 5, broken_board_count: 10, market_advance_ratio: 0.41, index_ma20_slope_5d: 0.1 },
    { dt: '2026-11-14', limit_up_count: 45, limit_down_count: 4, broken_board_count: 12, market_advance_ratio: 0.52, index_ma20_slope_5d: null },
    { dt: '2026-11-16', limit_up_count: 50, limit_down_count: 2, broken_board_count: 9, market_advance_ratio: 0.6, index_ma20_slope_5d: 0.2 },
  ]
}

export function payload(over: Partial<MarketPayload> = {}): MarketPayload {
  return {
    enabled: true,
    live: { available: true, stale: false, snapshot: snapshot() },
    schedule: { checked_at: '2026-11-17T10:42:00', is_trading_day: true, is_open: true, next_open_at: null },
    limit_check: { session: '2026-11-17', checked: 5441, mismatched: 0, samples: [] },
    regime: regime(), regime_columns: COLUMNS,
    missing: { index_intraday: '没有指数分时序列（见 #675）', return_distribution: '没有涨跌幅分布（见 #675）' },
    errors: {},
    ...over,
  }
}
