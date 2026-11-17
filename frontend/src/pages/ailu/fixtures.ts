import type { ResearchView, ShadowEntry, ShadowView } from '../../api/ailu'

export function entry(rank: number, over: Partial<ShadowEntry> = {}): ShadowEntry {
  return {
    rank, symbol: `60000${rank}.SH`, name: `样本${rank}`, board: '主板', market_state: '',
    p_touch: 0.5, p_seal_given_touch: 0.6, p_close_limit: 0.3 - rank * 0.01,
    actual_touched: null, actual_closed_limit: null, opened_at_limit: null, one_price_limit: null, ...over,
  }
}

export function shadow(over: Partial<ShadowView> = {}): ShadowView {
  return {
    state: 'ready', available: true, historical: false, message: '只读影子结果已就绪',
    snapshot: {
      prediction_date: '2026-11-16', target_session: '2026-11-17', generated_at: '2026-11-16T19:36:00+08:00',
      status: 'ready', model_version: 'lgbm-v3', model_sha: 'abcdef1', data_version: 'd1', data_sha: '1234567',
      eligible_count: 87, rolling_precision_at_20: 0.31, calibration_error: null, drift_score: 0.12, error: '',
      entries: [entry(1, { actual_touched: true, actual_closed_limit: false, opened_at_limit: false, one_price_limit: false }), entry(2)],
      limit_up_scores: [],
    },
    history: [
      { prediction_date: '2026-11-16', target_session: '2026-11-17', generated_at: '2026-11-16T19:36:00+08:00', status: 'ready', model_version: 'lgbm-v3', entry_count: 20, precision_at_20: null },
      { prediction_date: '2026-11-13', target_session: '2026-11-14', generated_at: '2026-11-13T19:36:00+08:00', status: 'ready', model_version: 'lgbm-v3', entry_count: 20, precision_at_20: null },
      { prediction_date: '2026-11-12', target_session: '2026-11-13', generated_at: '2026-11-12T19:36:00+08:00', status: 'ready', model_version: 'lgbm-v3', entry_count: 20, precision_at_20: 0.35 },
    ],
    ...over,
  }
}

export function research(): ResearchView {
  return {
    state: 'ready', available: true, historical_research: true, message: '研究回测结果',
    selected: null,
    summary: {
      generated_at: '2026-11-01T10:00:00', source_sha: 'srcsha1234', data_sha: 'datasha567',
      first_prediction_date: '2024-01-02', last_prediction_date: '2026-10-31', sessions: 690, test_sessions: 480, obs_sessions: 210,
      test_precision_at_20: 0.33, obs_precision_at_20: 0.29, obs_baseline_precision_at_20: 0.12, obs_lift_at_20: 2.4,
      obs_month_win_rate: 0.8, promoted_to_shadow: true,
    },
    history: [{ prediction_date: '2026-10-30', target_session: '2026-10-31', split: 'OBS', precision_at_20: 0.3, touch_precision_at_20: 0.5 }],
    monthly: [
      { month: '2026-09-01', split: 'OBS', precision_at_20: 0.28, predictions: 420, hits: 118 },
      { month: '2026-10-01', split: 'OBS', precision_at_20: 0.3, predictions: 440, hits: 132 },
    ],
  }
}
