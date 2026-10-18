"""Compatibility exports for the packaged research command."""

from qsys.research.auction_timing_study_cli import (
    DECISION_TIMES,
    LIMIT_MARKUPS,
    AUCTION_AT,
    virtual_at,
    limit_price,
    auction_fill,
    bps,
    vwap,
    describe,
    _VirtualBound,
    VirtualAuctionMarket,
    replay,
    consistency,
    mismatch_causes,
    _final_auction_volume,
    _minute_bars,
    run,
    main,
    _study,
)


if __name__ == "__main__":
    raise SystemExit(main())
