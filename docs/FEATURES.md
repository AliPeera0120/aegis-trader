# Feature pipeline

The pure minute-bar pipeline implements 1/5/15/30-minute returns; gap and distance from open/high/low; volume, rolling volume, volume acceleration and dollar volume; ATR, realized volatility, intraday range and trend persistence; cumulative session VWAP, distance, slope, reclaim and rejection; price acceleration; prior range highs/lows; 5/15/30 minute completed opening ranges; SPY-relative momentum; and quote spread/size imbalance.

Relative volume first compares the same minute of previous sessions (up to 20 observations). If unavailable, it uses preceding intraday bars and sets `rvol_historical=0`. Features carry this distinction so research cannot silently call an intraday fallback a historical RVOL estimate. Prior-day gap similarly exposes `gap_known`.

ATR uses a simple rolling mean of true range, not Wilder smoothing. Return windows use completed timestamps. Realized volatility is the standard deviation of recent minute log returns, not an annualized forecast. Sector-relative and beta-adjusted features, quote-change rates and trade intensity are extension points; their required feeds/history are not synthesized.

Regimes are deterministic thresholds for bullish/bearish trend, range, elevated/low volatility, risk-off and dislocation. They are hypothesis features rather than an independently validated predictive model. Strategy/regime attribution is computed from recorded trade entry regimes.
