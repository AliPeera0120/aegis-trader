from collections import defaultdict
from datetime import datetime
import numpy as np
from aegis.data import NY


def safe_ratio(a, b):
    return float(a / b) if b else None


def performance(trades, curve, initial_equity):
    pnl = np.array([t["net_pnl"] for t in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    eq = np.array([initial_equity] + [p["equity"] for p in curve])
    peaks = np.maximum.accumulate(eq)
    dd = (peaks - eq) / peaks
    underwater, longest = 0, 0
    for d in dd:
        underwater = underwater + 1 if d > 0 else 0
        longest = max(longest, underwater)
    days = {}
    for p in curve:
        day = datetime.fromisoformat(p["timestamp"]).astimezone(NY).date().isoformat()
        days[day] = p["equity"]
    daily = np.diff(np.log(np.array([initial_equity] + list(days.values())))) if days else np.array([])
    downside = np.minimum(daily, 0)
    groups = {}
    for dimension in (
        "symbol",
        "strategy",
        "sector",
        "regime",
        "side",
        "hour",
        "weekday",
        "volatility_bucket",
        "market_direction",
    ):
        buckets = defaultdict(list)
        for t in trades:
            entered = datetime.fromisoformat(t["entry_at"]).astimezone(NY)
            f = t.get("features", {})
            derived = {
                "hour": str(entered.hour),
                "weekday": entered.strftime("%A"),
                "volatility_bucket": "high" if f.get("realized_volatility", 0) > 0.004 else "normal",
                "market_direction": "up" if f.get("market_return", 0) > 0 else "down_or_flat",
            }
            key = derived.get(dimension, t.get(dimension, "unknown"))
            buckets[key].append(t["net_pnl"])
        groups[dimension] = {
            k: {"trades": len(v), "net_pnl": sum(v), "expectancy": float(np.mean(v))}
            for k, v in buckets.items()
        }
    holdings = [
        t.get(
            "holding_minutes",
            (datetime.fromisoformat(t["exit_at"]) - datetime.fromisoformat(t["entry_at"])).total_seconds()
            / 60,
        )
        for t in trades
    ]
    return {
        "trade_count": len(trades),
        "total_return": float(eq[-1] / initial_equity - 1),
        "net_pnl": float(pnl.sum()),
        "marked_pnl": float(eq[-1] - initial_equity),
        "gross_pnl": sum(t["gross_pnl"] for t in trades),
        "average_winner": float(wins.mean()) if len(wins) else None,
        "average_loser": float(losses.mean()) if len(losses) else None,
        "win_rate": safe_ratio(len(wins), len(trades)),
        "loss_rate": safe_ratio(len(losses), len(trades)),
        "payoff_ratio": safe_ratio(float(wins.mean()), abs(float(losses.mean())))
        if len(wins) and len(losses)
        else None,
        "profit_factor": safe_ratio(float(wins.sum()), abs(float(losses.sum()))),
        "expectancy": float(pnl.mean()) if len(pnl) else None,
        "median_trade": float(np.median(pnl)) if len(pnl) else None,
        "trade_std": float(pnl.std(ddof=1)) if len(pnl) > 1 else None,
        "max_drawdown": float(dd.max()),
        "average_drawdown": float(dd.mean()),
        "drawdown_duration_bars": longest,
        "sharpe": safe_ratio(float(daily.mean()) * np.sqrt(252), float(daily.std(ddof=1)))
        if len(daily) >= 20
        else None,
        "sortino": safe_ratio(float(daily.mean()) * np.sqrt(252), float(np.sqrt(np.mean(downside**2))))
        if len(daily) >= 20
        else None,
        "ratio_basis": "Daily log equity returns; 252 sessions, zero risk-free rate; suppressed below 20 days",
        "turnover": sum(t["qty"] * (t["entry"] + t["exit"]) for t in trades) / initial_equity,
        "average_exposure": float(np.mean([p.get("exposure", 0) / p["equity"] for p in curve]))
        if curve
        else 0,
        "average_holding_minutes": float(np.mean(holdings)) if holdings else None,
        "trades_per_day": safe_ratio(len(trades), len(days)),
        "fees": sum(t.get("fees", 0) for t in trades),
        "slippage_cost": sum(t.get("slippage_cost", 0) for t in trades),
        "spread_cost": sum(t.get("spread_cost", 0) for t in trades),
        "largest_win": float(pnl.max()) if len(pnl) else None,
        "largest_loss": float(pnl.min()) if len(pnl) else None,
        "attribution": groups,
    }


def monte_carlo(trades, initial_equity=100000, runs=1000, seed=7, method="bootstrap", ruin_fraction=0.5):
    if (
        not trades
        or not 1 <= runs <= 100000
        or not 0 < ruin_fraction < 1
        or method not in {"bootstrap", "shuffle"}
    ):
        raise ValueError("Valid trades, simulation count, ruin fraction and method required")
    rng = np.random.default_rng(seed)
    values = np.array([t["net_pnl"] for t in trades])
    returns, drawdowns, streaks, vols, ruins = [], [], [], [], []
    for _ in range(runs):
        sample = (
            rng.choice(values, len(values), replace=True)
            if method == "bootstrap"
            else rng.permutation(values)
        )
        path = np.r_[initial_equity, initial_equity + np.cumsum(sample)]
        peak = np.maximum.accumulate(path)
        returns.append(path[-1] / initial_equity - 1)
        drawdowns.append(float(((peak - path) / peak).max()))
        longest, streak = 0, 0
        for value in sample:
            streak = streak + 1 if value < 0 else 0
            longest = max(longest, streak)
        streaks.append(longest)
        vols.append(float(sample.std()))
        ruins.append(path.min() <= initial_equity * ruin_fraction)

    def quantiles(values):
        return dict(zip(["p05", "p50", "p95"], map(float, np.quantile(values, [0.05, 0.5, 0.95]))))

    return {
        "runs": runs,
        "seed": seed,
        "method": method,
        "return": quantiles(returns),
        "drawdown": quantiles(drawdowns),
        "losing_streak": quantiles(streaks),
        "trade_pnl_volatility": quantiles(vols),
        "ruin_probability": float(np.mean(ruins)),
        "assumptions": "Fixed-dollar trade P&L; independent trade bootstrap or exchangeable order; no adaptive sizing, costs inherited. Not a market-path simulation.",
    }


def paper_comparison(expected, actual, slippage_threshold=0.001):
    paired = []
    by_signal = {t.get("signal_id", t.get("order_id")): t for t in actual}
    for e in expected:
        a = by_signal.get(e.get("signal_id", e.get("order_id")))
        if a:
            delta = (a["entry"] - e["entry"]) / e["entry"]
            timing = (
                datetime.fromisoformat(a["entry_at"]) - datetime.fromisoformat(e["entry_at"])
            ).total_seconds()
            paired.append(
                {
                    "symbol": e["symbol"],
                    "fill_deviation": delta,
                    "timing_seconds": timing,
                    "flagged": abs(delta) > slippage_threshold or abs(timing) > 60,
                }
            )
    return {
        "expected_trades": len(expected),
        "actual_trades": len(actual),
        "matched": len(paired),
        "pairs": paired,
        "count_mismatch": len(expected) != len(actual),
    }


def decay(trades, historical_expectancy, at):
    sorted_trades = sorted(trades, key=lambda t: t["exit_at"])
    windows = {}
    for n in (20, 50, 100):
        part = sorted_trades[-n:]
        windows[f"{n}_trades"] = {
            "samples": len(part),
            "expectancy": float(np.mean([t["net_pnl"] for t in part])) if part else None,
        }
    for days in (20, 60):
        part = [t for t in sorted_trades if 0 <= (at - datetime.fromisoformat(t["exit_at"])).days < days]
        windows[f"{days}_days"] = {
            "samples": len(part),
            "expectancy": float(np.mean([t["net_pnl"] for t in part])) if part else None,
        }
    recent = windows["20_trades"]
    blocked = recent["samples"] >= 20 and recent["expectancy"] < 0
    warning = recent["samples"] >= 20 and recent["expectancy"] < historical_expectancy * 0.5
    return {"windows": windows, "blocked": blocked, "warning": warning}


def classify_trade(trade):
    if trade.get("system_error"):
        return "SYSTEM ERROR"
    if trade.get("strategy_violation"):
        return "STRATEGY VIOLATION"
    if abs(trade.get("slippage", 0)) / trade["entry"] > 0.002:
        return "SLIPPAGE ISSUE"
    return "SIGNAL / WIN" if trade["net_pnl"] > 0 else "SIGNAL / LOSS"
