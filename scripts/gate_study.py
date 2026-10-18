"""Compatibility exports for the packaged research command."""

from qsys.research.gate_study_cli import (
    CHECKPOINTS,
    _MA_BARS,
    _STUDY_NOTIONAL,
    _SLIPPAGE,
    net_return_pct,
    _decide,
    replay,
    replay_many,
    describe,
    run,
    main,
    _study,
)


if __name__ == "__main__":
    raise SystemExit(main())
