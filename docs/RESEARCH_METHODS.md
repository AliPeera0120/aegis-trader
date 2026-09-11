# Reproducible research

1. Record a hypothesis before inspecting the holdout.
2. Ingest a timestamped dataset and retain its raw response, feed, adjustment policy, quality report, universe and content hash.
3. Compare all fill models. Register each exact parameter/source version.
4. Use grid or seeded random search only on training/validation partitions. Walk-forward test periods are evaluated once per selected fold. Record parameter count, best training expectancy, validation/test metrics and every neighboring parameter result. Fewer than five validation trades disqualifies a fold's selection.
5. Use conservative fills and adequate samples. Monte Carlo can bootstrap or reshuffle fixed-dollar trade P&L to summarize return, drawdown, losing streak, volatility and ruin distributions. Independence/exchangeability is a strong assumption; it is not a market-path forecast.
6. Attack concentration by ticker, sector, regime, hour, weekday, side, volatility and market direction. No post-selection performance claim based only on a profitable subgroup.
7. Compare calibrated ML models with logistic and constant baselines. After diagnostics, reserve a new untouched test period if parameters/features changed.
8. Derive evidence from stored real results, then promote stage by stage. Synthetic fixtures and optimistic fills cannot qualify.

```sh
aegis register-strategy --name orb --parameters '{}'
aegis derive-evidence --experiment EXPERIMENT_ID --strategy-key strategy:orb:VERSION --evaluation BACKTEST
aegis promote --strategy-key strategy:orb:VERSION --target BACKTEST --evidence BACKTEST_EVIDENCE_ID
aegis derive-evidence --experiment WALK_FORWARD_ID --strategy-key strategy:orb:VERSION --evaluation OOS
aegis promote --strategy-key strategy:orb:VERSION --target OUT_OF_SAMPLE --evidence OOS_EVIDENCE_ID
aegis promote --strategy-key strategy:orb:VERSION --target PAPER_CANDIDATE --evidence OOS_EVIDENCE_ID
```

Evidence uses only version-matched closed trades, at least 30 samples, prior outcome timestamps, mean modeled fees, empirical win/loss magnitude and a 97.5% t-based lower confidence bound. Positive mean alone is insufficient for paper candidacy. Dollar-per-share EV is a rough empirical estimate and not a calibrated per-symbol opportunity model; use matched populations before deployment. Bounds can overstate confidence when trades are clustered.

Experiments store hypothesis, dataset/result hashes, date range, universe through dataset manifest, features at signals, parameters, fill/risk settings, metrics, commit hash and installed dependency versions. `notebooks/research_workflow.ipynb` demonstrates the software loop using explicitly synthetic data.

No Bayesian optimization, neural network or SHAP dependency was added: these are optional and unjustified before validating simple baselines. No fully historical constituent feed was supplied. No strategy has demonstrated robust real-market positive expectancy in this build.
