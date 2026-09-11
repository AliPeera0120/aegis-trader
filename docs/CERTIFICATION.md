# Paper certification and explicit overrides

Certification consumes real stored broker fills and operator-reviewed external evidence. These commands were implemented and mock-tested; no certification was granted to this build.

Fee reconciliation is intentionally explicit. Supply a JSON allocation derived from your broker statement, including every owned round-trip's total fees (including zero where the statement confirms zero):

```json
{"mode":"PAPER","source":"Alpaca paper statement export, reviewed locally","period":"2026-09-01/2026-09-30","fees":[{"order_id":"aegis-CLIENT_ORDER_ID","total_fees":0.05}]}
```

Run `aegis reconcile-fees --statement /private/path/fees.json`. The command validates all rows before changing records, preserves a SHA-256 source fingerprint, sets `fees_status=operator_reconciled`, recalculates net P&L and audits the action. It does not invent zero fees or fetch an unsupported SDK endpoint. Reconcile cash flows and manual activity separately.

After actually verifying the paper lifecycle, prepare a report with `mode=PAPER`, operator name, timezone-aware `verified_at` within the last seven days, substantive `evidence_notes`, and `checks` mapping `paper_auth`, `data_stream`, `reconciliation`, `kill_switch`, and `order_states` to true. Run:

```sh
aegis certify-paper-checks --report /private/path/checks.json --attestation 'I VERIFIED THESE CHECKS ON ALPACA PAPER'
aegis derive-paper-evidence --strategy-key strategy:orb:VERSION
```

The first command is an auditable operator attestation, not an automated assertion that the tests happened. The second derives statistics from recorded PAPER trades of that exact strategy version, requires all fees reconciled, configured minimum days/trades and broker equity snapshots, and stores an immutable evidence record. It does not manufacture paper history. Promotion still requires sequential stages and positive lower-bound expectancy.

An operator can temporarily waive the global readiness evidence checklist with `aegis override-readiness --reason 'Substantive reason for this temporary exception' --hours 1 --acknowledgment 'OVERRIDE READINESS AT MY OWN RISK'`. This emits a warning, logs the reason and expiry, and expires within 24 hours. It does not waive live configuration, dedicated credentials, capital/order limits, unresolved critical errors, candidate EV, independent risk review, or the strategy's sequential promotion eligibility. There is no UI unlock button. No override was applied during development.
