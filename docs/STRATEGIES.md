# Strategy lab

Seven baseline implementations share a signal/entry/invalidation/exit/metadata/required-feature interface: opening range breakout; VWAP continuation; VWAP reclaim; VWAP reversion; relative-volume momentum; gap continuation; gap fade. All built-in hypotheses are long-only. A broker/risk short path exists behind explicit short authorization, but no short baseline is promoted.

ORB requires a completed 5, 15 or 30 minute opening range and a completed close crossing above its high, VWAP confirmation and configurable relative volume. VWAP reversion requires the contemporaneous range regime. Gap studies classify positive/negative gaps, size buckets, fill behavior and first-hour return. Missing premarket/sector inputs remain null or unknown.

Strategy versions hash the strategy source plus canonical parameters. The registry rejects immutable-record edits. A material code or parameter change creates a new version and returns its eligibility to IDEA. Register a parameterized version with `aegis register-strategy --name orb --parameters '{"opening_minutes":5,"min_rvol":1.5}'`.

Every candidate stores actual feature values and deterministic trigger reasons. Cross-sectional scanning ranks candidates by evidence-based EV when available, then score and symbol; the risk engine evaluates each against already reserved exposure. These scores are heuristic research rankings, not calibrated probabilities.

Promotion is IDEA → BACKTEST → OUT_OF_SAMPLE → PAPER_CANDIDATE → PAPER_VERIFIED → LIVE_ELIGIBLE. No skipped stages; synthetic evidence is rejected. See RESEARCH_METHODS.md for deriving evidence. Baselines are disabled on a clean install and have no claimed statistical edge.
