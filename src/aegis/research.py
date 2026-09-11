from datetime import timedelta
from itertools import product
import json
import subprocess
import numpy as np
from aegis.data import Calendar, NY
from aegis.domain import Bar, stable_id
from aegis.backtest import Backtester
from aegis.strategies import Baseline


def synthetic_bars(start="2025-01-06", days=5, symbols=("SPY", "AAPL", "MSFT"), seed=7):
    """Infrastructure fixture only. NEVER market evidence or a deployable dataset."""
    if not 1 <= days <= 250 or len(symbols) > 100:
        raise ValueError("Fixture size out of bounds")
    import pandas as pd

    rng = np.random.default_rng(seed)
    cal = Calendar()
    sessions = list(
        cal.sessions(
            pd.Timestamp(start).date(), (pd.Timestamp(start) + pd.Timedelta(days=days * 2 + 14)).date()
        )
    )[:days]
    results = []
    prices = {symbol: 100 + i * 35 for i, symbol in enumerate(symbols)}
    for day, (opened, closed) in enumerate(sessions):
        minute_count = int((closed - opened).total_seconds() / 60)
        for symbol in symbols:
            price = prices[symbol] * (1 + rng.normal(0, 0.004))
            for minute in range(minute_count):
                trend = 0.00012 * np.sin(minute / 45 + day) + (0.0003 if 16 < minute < 24 else 0)
                next_price = price * np.exp(rng.normal(trend, 0.0008))
                wiggle = abs(rng.normal(0, 0.0004)) * price
                volume = int(rng.integers(20000, 80000) * (3 if 16 < minute < 24 else 1))
                timestamp = opened + timedelta(minutes=minute)
                results.append(
                    Bar(
                        symbol=symbol,
                        start=timestamp,
                        end=timestamp + timedelta(minutes=1),
                        available_at=timestamp + timedelta(minutes=1),
                        open=price,
                        high=max(price, next_price) + wiggle,
                        low=min(price, next_price) - wiggle,
                        close=next_price,
                        volume=volume,
                        source=f"synthetic-{seed}",
                        vwap=(price + next_price) / 2,
                    )
                )
                price = next_price
            prices[symbol] = price
    return sorted(results, key=lambda b: (b.start, b.symbol))


def parameter_grid(space, random_count=None, seed=7):
    keys = sorted(space)
    grid = [dict(zip(keys, values)) for values in product(*(space[k] for k in keys))]
    if len(grid) > 10000:
        raise ValueError("Search budget exceeds 10,000 combinations")
    if random_count:
        rng = np.random.default_rng(seed)
        indices = rng.choice(len(grid), min(random_count, len(grid)), replace=False)
        return [grid[i] for i in indices]
    return grid


def temporal_folds(bars, train_days=20, validation_days=5, test_days=5, embargo_days=1):
    if min(train_days, validation_days, test_days) < 1 or embargo_days < 0:
        raise ValueError("Invalid temporal fold lengths")
    days = sorted({b.start.astimezone(NY).date() for b in bars})
    width = train_days + validation_days + test_days + 2 * embargo_days
    for offset in range(0, len(days) - width + 1, test_days):
        train = days[offset : offset + train_days]
        v0 = offset + train_days + embargo_days
        valid = days[v0 : v0 + validation_days]
        t0 = v0 + validation_days + embargo_days
        test = days[t0 : t0 + test_days]
        yield {"train": train, "validation": valid, "test": test}


def walk_forward(
    bars,
    strategy_name,
    parameter_space,
    train_days=20,
    validation_days=5,
    test_days=5,
    embargo_days=1,
    random_count=None,
    seed=7,
    synthetic=False,
):
    combinations = parameter_grid(parameter_space, random_count, seed)
    if not combinations:
        raise ValueError("Parameter space is empty")
    folds, test_trades = [], []
    for dates in temporal_folds(bars, train_days, validation_days, test_days, embargo_days):
        partitions = {
            k: [b for b in bars if b.start.astimezone(NY).date() in values] for k, values in dates.items()
        }
        scored = []
        for params in combinations:
            strategy = Baseline(strategy_name, params)
            train = Backtester(strategy).run(partitions["train"], synthetic=synthetic)
            valid = Backtester(strategy).run(partitions["validation"], synthetic=synthetic)
            # Selection never reads final test outcomes. Drawdown and sample scarcity penalize validation score.
            metric = valid["metrics"]
            score = (metric["expectancy"] or 0) - metric["max_drawdown"] * 1000
            if metric["trade_count"] < 5:
                score = -1e9
            scored.append({"params": params, "score": score, "train": train["metrics"], "validation": metric})
        chosen = sorted(scored, key=lambda s: (-s["score"], json.dumps(s["params"], sort_keys=True)))[0]
        result = Backtester(Baseline(strategy_name, chosen["params"])).run(
            partitions["test"], synthetic=synthetic
        )
        test_trades.extend(result["trades"])
        folds.append(
            {
                "dates": {k: [str(d) for d in v] for k, v in dates.items()},
                "selected": chosen["params"],
                "combinations_tested": len(scored),
                "best_train_expectancy": max((s["train"]["expectancy"] or 0) for s in scored),
                "validation": chosen["validation"],
                "test": result["metrics"],
                "sensitivity": scored,
                "test_evaluations": 1,
                "qualified": chosen["score"] > -1e9,
            }
        )
    if not folds:
        raise ValueError("Insufficient sessions for the specified walk-forward windows")
    return {
        "folds": folds,
        "test_trades": test_trades,
        "synthetic": synthetic,
        "seed": seed,
        "total_combinations_tested": len(combinations) * len(folds),
        "limitations": [
            "Multiple-testing adjustment not a guarantee",
            "Embargo is by whole session",
            "No position carry across folds",
            "Warm-up restarts at fold boundary",
        ],
    }


def gap_study(bars):
    groups = {}
    for b in sorted(bars, key=lambda b: (b.symbol, b.start)):
        groups.setdefault((b.symbol, b.start.astimezone(NY).date()), []).append(b)
    prior, rows = {}, []
    for (symbol, day), session in sorted(groups.items()):
        if symbol in prior:
            gap = session[0].open / prior[symbol] - 1
            first_hour = session[:60]
            rows.append(
                {
                    "symbol": symbol,
                    "date": str(day),
                    "gap": gap,
                    "direction": "up" if gap > 0 else "down",
                    "magnitude_bucket": "large" if abs(gap) >= 0.02 else "small",
                    "first_hour_return": first_hour[-1].close / session[0].open - 1,
                    "gap_filled": any(b.low <= prior[symbol] <= b.high for b in session),
                    "continuation": (session[-1].close / session[0].open - 1) * np.sign(gap) > 0,
                    "premarket_volume": None,
                    "sector": "unknown",
                }
            )
        prior[symbol] = session[-1].close
    return {
        "observations": rows,
        "availability": "Descriptive end-of-session labels; not available to same-day features.",
    }


def commit_hash():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return "uncommitted"


def persist_experiment(store, result, hypothesis):
    import importlib.metadata

    result["hypothesis"] = hypothesis
    result["commit_hash"] = commit_hash()
    result["dependencies"] = {
        name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn", "alpaca-py")
    }
    identifier = result.get("id") or stable_id(json.dumps(result, sort_keys=True, default=str))
    store.put("experiments", identifier, result)
    store.log(
        "EXPERIMENT_RECORDED",
        {"id": identifier, "synthetic": result.get("synthetic", False), "hypothesis": hypothesis},
    )
    return identifier
