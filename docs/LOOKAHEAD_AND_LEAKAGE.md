# Look-ahead and leakage controls

A minute bar labeled 10:01 Eastern describes [10:01,10:02). Its OHLCV is inaccessible before 10:02 and before `available_at`. Thus a 10:01 decision sees the completed 10:00–10:01 bar, not the 10:01–10:02 close or high.

`FeatureEngine.calculate(bars, at)` filters all input before calculating indicators. Session VWAP resets each exchange session. A completed opening range requires all exact opening-minute timestamps and the full range duration. Previous-session gaps use the prior session's completed close. Benchmark features use the same decision cutoff. A future quote is an error.

Adversarial tests verify prefix invariance: calculating from a historical prefix equals calculating at the same timestamp with an entire later dataset supplied. Poisoning future prices by 100× must not change the earlier feature vector. Tests cover incomplete bars, delayed publication, opening-range readiness and DST.

Backtest orders are created after signal observations. Fills use only a later bar whose opening time is at or after signal time plus latency. Positive latency conservatively skips the immediately starting minute instead of pretending the order could execute at its open. Targets are disallowed on the entry bar; stop/target ambiguity resolves to the stop. Neither convention reconstructs a true tick path.

Walk-forward selection scores training and validation candidates before running the selected parameters on test. Whole-session embargoes separate windows. ML rows require decision `timestamp` and `label_end`; outcomes overlapping the next split are purged, with a five-minute embargo. Scaling fits train only; calibration fits validation only; final test metrics are separate. Permutation importance and ablation on test are diagnostics, not additional tuning permission.

Remaining leakage risks: vendor corrections published after a historical bar, corporate-action adjustments, unversioned external metadata, a current survivorship-biased watchlist, repeated human inspection of the same test period, and unrecorded historical data entitlements. Tests cannot prove these external datasets are point-in-time correct. Use a fresh final holdout after learning from diagnostic results.
