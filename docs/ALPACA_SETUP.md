# Connect Alpaca PAPER

No broker credentials were supplied during this build. Nothing has been submitted to Alpaca.

1. Sign into your own Alpaca account through Alpaca's official website. Switch to the paper environment and generate paper API credentials.
2. In the project directory, copy `.env.example` to `.env` locally. `.env` is ignored by Git and excluded from Docker builds. Set `TRADING_MODE=PAPER`, `ALPACA_PAPER_KEY`, and `ALPACA_PAPER_SECRET` using those paper credentials. Do not paste them into source code, chat, logs, notebooks, or commits.
3. For data, either set `ALPACA_DATA_KEY` / `ALPACA_DATA_SECRET` or let the application use the current mode's credentials. Choose `DATA_FEED=iex` for an entitled IEX connection; use `sip` only with an appropriate entitlement. IEX is not consolidated U.S. market coverage.
4. Create an operator token: `aegis create-control-token`. The command writes `var/operator-token.txt` with owner-only permissions and never prints its contents. Set `AEGIS_CONTROL_TOKEN` in your private environment to that value. The dashboard's Operator access dialog uses this token, never broker keys.
5. Run `aegis connect-check`. This authenticates PAPER, reads account/positions/clock, and reconciles. It submits no orders. Errors are sanitized; verify mode, credentials and entitlement if connection fails.
6. Run `AEGIS_RUN_PAPER_INTEGRATION=1 pytest -q -m paper`. These tests read the paper account and download a known historical SPY sample. They refuse a LIVE configuration.
7. Download research data:

```sh
aegis ingest --symbols SPY,QQQ,IWM,AAPL,MSFT --start 2025-01-02T14:30:00+00:00 --end 2025-04-01T20:00:00+00:00 --timeframe 1Min
aegis backtest --source alpaca-iex --strategy orb --fill conservative
aegis walk-forward --source alpaca-iex --strategy orb --train-days 20 --validation-days 5 --test-days 5
```

8. Register the exact parameterized version under study. Derive evidence from stored nonsynthetic experiments and promote one stage at a time; see RESEARCH_METHODS.md. An enabled service will not invent positive EV or bypass an IDEA strategy's ineligibility.
9. Set `SERVICE_ENABLED=true` only when you intend the continuous service to run. Start `aegis serve`; the service starts with the dashboard when this flag is true. Alternatively run `aegis paper` as a standalone foreground service. Use one continuous execution owner, not both. A server process supervisor or Docker keeps it online; the browser can close.
10. Monitor data freshness, broker reconciliation, actual brackets and fill events. Before calling paper mode operationally ready, verify a complete real paper entry/partial-fill/exit lifecycle, reconnect, stop, cancellation and end-of-day handling. The credentialed lifecycle has not been verified in this build.

Emergency: the dashboard's STOP TRADING latches entries off and cancels eligible entirely unfilled entry orders. `aegis stop` does the same from the CLI; `aegis stop --flatten` explicitly requests liquidation. Keep the database and runtime directory identical across commands. Never assume liquidation completed because its request returned; inspect broker state.

Official references checked for implementation:
- [Alpaca Python SDK](https://alpaca.markets/sdks/python/getting_started.html)
- [Trading orders](https://alpaca.markets/sdks/python/api_reference/trading/orders.html)
- [Historical stock data](https://alpaca.markets/sdks/python/api_reference/data/stock/historical.html)
- [Real-time stock data](https://alpaca.markets/sdks/python/api_reference/data/stock/live.html)
- [Order behavior and brackets](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
