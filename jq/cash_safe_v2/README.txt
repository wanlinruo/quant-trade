Structural Strategy V1.2 - FROZEN FINAL
WEAK_MAIN and WEAK_CHINEXT are completely disabled before Gate/allocation.
HIGH lifecycle remains unchanged from V1.1.
LOW lifecycle remains unchanged from V1.1.
No threshold tuning and no other structural changes are introduced.
daily_buy_details.csv contains REAL filled BUY reconciliation rows: date, stock, strategy_tag.
regime_daily.csv contains the exact daily market-factor inputs stored in g.regime_daily and used by the frozen Gates.
buy_execution_audit.csv records original Frozen target budget/day_open vs real fills.
cash_path_audit.csv records daily cash and negative-cash state.
cash_safe_v2_execution.csv records protected V2 order/fill details.
A ZIP package is written automatically for direct export.

Recommended first test: 2025-01-01 ~ 2026-09-01.
