"""Windows/QMT 只读回报冒烟；绝不调用下单或撤单接口。"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="QMT 账户与回报只读冒烟")
    parser.add_argument("--account")
    parser.add_argument("--qmt")
    parser.add_argument("--runtime-yaml")
    args = parser.parse_args()

    if args.runtime_yaml:
        if args.account or args.qmt:
            parser.error("--runtime-yaml 不能与 --account/--qmt 同时使用")
        from run_agent import _load_qmt_runtime

        args.account, args.qmt = _load_qmt_runtime(args.runtime_yaml)
    elif not args.account or not args.qmt:
        parser.error("必须提供 --runtime-yaml，或同时提供 --account 和 --qmt")

    from qsys.agent.xtquant_adapter import XtQuantAdapter

    adapter = XtQuantAdapter(args.account, args.qmt)
    account = adapter.query_account_details()
    orders = adapter.query_orders()
    trades = adapter.query_trades()
    print(
        f"账户现金={account.cash:,.2f} 冻结={account.frozen_cash:,.2f} "
        f"市值={account.market_value:,.2f} 总资产={account.total_asset:,.2f}"
    )
    print(f"持仓={len(account.positions)} 委托={len(orders)} 成交={len(trades)}")
    missing = [o.broker_order_id for o in orders if not o.client_order_id]
    if missing:
        print(f"警告: {len(missing)} 笔委托没有 order_remark/client_order_id")
    print("只读冒烟完成：未调用下单或撤单接口")
    return 0


if __name__ == "__main__":
    sys.exit(main())
