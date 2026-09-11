# Backtesting

`Backtester.run` processes minute events chronologically. Signals see only completed available history. Pending orders reserve risk, expire after a configurable TTL, respect latency, and can partially fill subject to participation. Positions use adverse spread/slippage and per-share costs, protective stop/target behavior, optional trailing ATR, time exits and a scheduled flatten period before the actual session close. Output includes signals, risk decisions, execution events, closed trades, marked equity, open end-of-dataset positions, data quality, config, version and a result hash.

Default fill scenarios:

| Model | Spread | Slippage | Fee/share/side | Participation | Latency |
|---|---:|---:|---:|---:|---:|
| Optimistic | 0 bps | 0 bps | $0 | 10% | 0 sec |
| Standard | 2 bps | 2 bps | $0.005 | 1% | 1 sec |
| Conservative | 8 bps | 8 bps | $0.01 | 0.5% | 2 sec |

These are explicit assumptions, not calibrated claims about Alpaca or any venue. Change them to match measured execution. Limit entries never fill through their limit. Market/stop research entries still cannot exceed reserved risk price. With minute data, positive latency waits for a subsequent bar whose open is known to occur after arrival. If both stop and target are touched, stop wins. Profit targets do not fill on the entry bar.

Entry partial fills use volume caps; protective liquidation currently assumes available liquidity. Gap-through stops execute adversely at the open plus costs. Scheduled end-of-day/time exits use the next available eligible bar's open, not future knowledge of the dataset's final bar. Missing data can prevent an exit; the position remains marked and reported. There is no fabricated closing trade at dataset end.

Closed-trade P&L is after modeled costs; marked return additionally includes open-position P&L and entry fees. Metrics expose both. Daily-return Sharpe/Sortino are suppressed below 20 sessions, use 252 sessions and zero risk-free return. An undefined ratio remains null, not infinity or an invented zero.

Important limits: bars do not establish queue priority, intrabar order, NBBO, halts, market-impact dynamics, stop liquidity or short borrow costs. Same-bar stop handling is deliberately pessimistic. Current universe and raw historical revisions are not point-in-time complete. Real historical results must be tested against unseen periods and actual paper execution.
