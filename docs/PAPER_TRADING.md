# Continuous paper service

Set TRADING_MODE=PAPER and SERVICE_ENABLED=true in the private .env to start the worker with the dashboard. A file lock permits one execution owner per runtime directory. Broker clock and exchange calendar govern regular-session entry times. Current status is available at http://127.0.0.1:8000 and /api/status.

Thirty minutes before open, the service downloads completed historical bars and builds its watchlist. IEX volume thresholds describe that exchange's reported volume, not consolidated volume. Stale overnight spreads are deferred to the mandatory fresh-quote entry check. Premarket bars are excluded from regular-session VWAP and opening ranges. Bursts of quotes are coalesced into the latest snapshot for each symbol per cycle.

The scanner starts at the opening bell. An order still requires an actual completed-bar strategy signal and risk approval; a 15-minute opening range cannot trade at 9:30. No-trade is a valid outcome. Entries use bounded limit orders and native stop/target brackets. Unfilled entries expire after three minutes. Five minutes before close, the configured FLATTEN policy stops entries, cancels unfilled entries and requests liquidation. After close, the service creates a session report and compares paper outcomes with a standard-fill replay. Overnight-policy behavior and execution differences remain part of the report.

## Experimental paper learning

Normal strategy admission requires positive, version-matched evidence. To collect unproven outcomes, explicitly set PAPER_LEARNING_ENABLED=true and PAPER_LEARNING_STRATEGIES to comma-separated exact registered keys, for example strategy:orb:<version>. Startup rejects empty, unknown or obsolete keys. This PAPER experiment does not change an IDEA strategy into a proven or promoted strategy. Prior losing research remains in the database.

The experiment permits at most one share/order, two positions, ten entries/day, $1,000/order and $2,000 total exposure. Its additional $25 daily paper account-loss trigger requests flattening and latches new entries off until review. This is a trigger, not a guarantee that losses stop at $25. Fresh quotes/bars, liquidity, spread, clock, reconciliation, ordinary loss limits, drift and the stop latch still apply. Both execution and broker boundaries prevent routing experimental authorizations to LIVE.

Closed trades retain signal time, decision features, strategy version, broker fills, fee status and the PAPER_EXPERIMENT label. After close, each exact strategy version can enter a chronological, purged logistic/calibration experiment once it has 100 closed trades across 10 days and both outcome classes in the training/calibration partitions. Labels subtract an explicit cost buffer from observed gross P&L. Reports compare against a constant baseline. Insufficient outcomes remain COLLECTING.

Training reports are research records; models are not automatically deployed. Repeated diagnostics and selected-trade labels are not independent proof of a profitable edge. These data-count thresholds do not grant real-money eligibility. Separate positive out-of-sample evidence, fee reconciliation, operational checks and all live configuration locks remain required.

## macOS supervision

Run `python scripts/install_paper_service.py` from the installed project environment. It deploys a private snapshot to `~/Library/Application Support/AegisTrader` and installs the user LaunchAgent `com.aegis-trader.paper`. The development .env and deployed .env point to one authoritative SQLite database/runtime directory there. Original historical archives are retained; some existing raw-file references still point to the original var/raw directory. Credentials are private local files and never enter the plist or Git.

The service restarts after process failure and at login. It prevents idle system sleep while running. Keep the Mac powered on, lid open and online. Closing a terminal does not stop it. **Stop trading** on the dashboard latches new entries off and retains protective exits. Unload the whole service with `launchctl bootout gui/$(id -u)/com.aegis-trader.paper`.

Inspect supervision with `launchctl print gui/$(id -u)/com.aegis-trader.paper`. Logs are under `~/Library/Application Support/AegisTrader/var/service`, with reports and the private operator token elsewhere under the same var directory. The deployed code is a snapshot: rerun the installer to apply source/config changes while idle, then verify API status. Dependencies are copied from the tested project venv on first installation; update the private venv explicitly when changing dependencies.

Account authentication, clock, historical data and both authenticated streams passed real-paper integration checks. Actual order fills, protective exits and a complete unattended session still need observed broker records. A running experiment is not PAPER VERIFIED or LIVE ELIGIBLE.
