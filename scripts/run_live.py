"""Linux实盘核心兼容入口；包内主入口保留原交易姿态门禁与默认路径。"""

from qsys.live.core_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
