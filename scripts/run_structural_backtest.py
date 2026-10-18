"""Compatibility exports for the packaged research command."""

from qsys.research.structural_backtest_cli import (
    RESEARCH_THRESHOLD_KEYS,
    load_research_thresholds,
    _cost_model,
    main,
)


if __name__ == "__main__":
    raise SystemExit(main())
