"""指标字典验证：按字典里的规则验证当日数据，结果写 `metric_checks`。

由 qsys-catalog-verify.timer 在交易日 19:40 触发——Tushare 收盘同步（17:00 与
19:00 补跑）之后、每日服务质检（20:00）之前，质检因此读得到当晚的最新结果。

范围是「当日增量规则 + 当年至今的全量规则」，理由见
`qsys.data.catalog.verification`。整段历史仍走手工 `scripts/verify_metrics.py`。

用法：
  uv run python scripts/catalog_verify.py --warehouse warehouse
  uv run python scripts/catalog_verify.py --day 2026-09-16      # 复验某一天
"""

import sys

from qsys.data.catalog_verify_cli import main


if __name__ == "__main__":
    sys.exit(main())
