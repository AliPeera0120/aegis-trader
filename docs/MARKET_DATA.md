# Market data and provenance

Historical 1-minute, 5-minute, 15-minute and daily bars use the official `StockHistoricalDataClient`. Raw responses are gzip JSON files under `var/raw`; normalized bars use UTC timestamps in the database. The configured feed and adjustment policy are recorded. Requests use raw adjustment to avoid silently bringing future adjustment information into historical features.

A bar has `start`, `end`, and `available_at`. Historical bars assume availability at completion; live bars record actual reception, never earlier than completion. Daily bars are mapped to the exchange's actual session close. Features filter on both end and availability. Aggregation creates a 5/15 minute bar only when every required minute exists, using a session-open anchor.

`exchange_calendars` XNYS sessions supply holidays, early closes and DST-aware boundaries. Runtime also requires the broker clock to be open and synchronized. Broker calendar access is available. The package calendar range is explicitly 2000–2035; upgrade/extend it before operating outside that range and keep the calendar package current.

Validation rejects impossible prices, OHLC relationships, negative volume, naive timestamps, pre-completion availability, and crossed quotes. Dataset quality reports identify duplicates, missing interior bars, zero volume, out-of-session rows and extreme price changes. Extreme discontinuities require investigation for splits/outliers; backtesting refuses quarantined events. Data is never interpolated. This does not infer missing first/last bars when the requested dataset's intended coverage is unknown.

Live subscriptions cover bars, quotes and trades. The service records quotes, trade prints, completed bars, disconnects and quarantined events; a bounded queue fails closed on overflow. Stale/future quote, signal and clock timestamps independently reject entries. A reconnect must be followed by reconciliation.

The default universe uses version-controlled price, ADV, dollar-volume, spread, exchange and volatility filters. Unknown sector values are conservatively grouped together. Market cap is optional and unavailable from the baseline asset adapter. No full historical constituent feed, corporate-action feed, delisting history or rename mapping has been supplied. `Membership` and `CorporateEvent` provide timestamp-aware interfaces for those feeds; they are not populated by fabricated data. Results are not survivorship-bias-free.
