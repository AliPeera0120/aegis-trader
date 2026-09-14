# September 14, 2026 paper experiment

## Actual session outcome and recovery

At the September 14 close, the paper account had zero submitted local orders, zero open broker orders, zero positions and zero closed trades. Paper P&L was $0. Both strategies still have zero training samples. The morning readiness checks below did not establish a successful trading session.

During the session, macOS sleep/wake events interrupted connectivity. An early service error latched entries off, repeated reconciliation failures followed, and the worker heartbeat stopped at 1:22 p.m. Eastern. Inspection at 3:56 p.m. found its worker blocked in an SSL read; the SDK supplied no request timeout. Separately, submission compared a newly fetched broker clock against an earlier local timestamp, incorrectly classifying a fresh clock as future data. The old error record omitted the exception type, so the exact cause of that first error cannot be established from its log.

Recovery added bounded network waits, corrected the clock comparison, aged out stale dashboard health and recorded exception types with actual failure times. All 141 offline tests pass, with three opt-in paper integration tests skipped in that run. Regression tests cover a hung HTTP response, the broker clock ordering and stale status. No paper order was forced to test the repair.

The repaired service restarted at 4:02 p.m. Eastern. Subsequent checks confirmed advancing heartbeats, successful broker reconciliation, both streams authenticated and no broker orders or positions. The technical pause was cleared only after those checks, while the market was closed. PAPER is armed for the next regular session reported by the broker: September 15 at 9:30 a.m. Eastern. A successful full session, actual paper fills, protective exits and end-of-day closure still require observation. After-hours IEX quotes were stale or one-sided and were correctly excluded. Keep the Mac powered on, lid open and online; idle-sleep prevention does not make a closed laptop an always-on host. Live execution remains locked.

## Morning preparation record

At 6:46 a.m. Eastern, the supervised PAPER worker was running, broker reconciliation was current, and market-data and trade-update streams were authenticated. The broker clock reported today's regular session as 9:30 a.m.–4:00 p.m. Eastern. Live execution remained locked. No paper orders or fills had occurred before open.

Watchlist: SPY, QQQ, IWM, AAPL, MSFT and NVDA. Real historical IEX context loaded successfully for all six. The worker refreshes context at 9:00 a.m. and begins scanning at 9:30. Its first order requires a completed-bar signal and all risk checks; no entry is forced at the bell.

The experiment selects only ORB version a875abe90459 and VWAP continuation version 3a23c63a21cc. Both remain unproven. Simulated limits: one share/order, two open positions, ten entries/day, $1,000/order, $2,000 exposure and a $25 daily-loss stop trigger. The 15-minute ORB can first qualify after its opening range completes. The previous ORB study lost money under optimistic, standard and conservative fills and in the later test period; that evidence was not promoted or replaced.

Closed paper trades automatically retain decision features and execution outcomes. Each strategy's training status is currently COLLECTING with zero completed trades. Chronological model experiments require at least 100 closed trades across ten days and remain research only. A positive future result would still need separate validation and operational qualification before any real-money use.

The macOS LaunchAgent is com.aegis-trader.paper, with private application state under ~/Library/Application Support/AegisTrader. It has been restarted and reconnected successfully after deployment. Keep the Mac on, lid open and online. Scheduled checks in the existing Codex task are set for 9:05 and 9:50 a.m. Eastern today; keep Codex open for those checks.

Open http://127.0.0.1:8000 to view the dashboard. If the restarted server requests operator access, use the private operator-token.txt already saved locally. The **Stop trading** button pauses new entries. See PAPER_TRADING.md for supervision, reports, updates and shutdown instructions.

Code checks cover market timing, stale data, version admission, loss/exposure limits, order state, protective-leg retrieval, cancellation, chronological training and LIVE isolation. Real PAPER account/history and both stream authentication checks passed. Actual market-session fills, protective exits and end-of-day closure still need observation; this is an armed experimental session, not a verified profitable strategy.
