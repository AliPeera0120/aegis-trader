from datetime import datetime, timezone, timedelta
import numpy as np
import pytest
from aegis.backtest import Backtester, FillModel
from aegis.research import synthetic_bars, temporal_folds, parameter_grid, walk_forward
from aegis.strategies import Baseline, baselines
from aegis.risk import RiskEngine
from aegis.analytics import performance, monte_carlo
from aegis.ml import experiment, purged_split, drift_report, probability_report


@pytest.fixture(scope="module")
def bars():
    return synthetic_bars(days=2, symbols=("SPY", "AAPL"))


def test_deterministic_backtest(bars):
    a = Backtester(Baseline("orb")).run(bars, synthetic=True)
    b = Backtester(Baseline("orb")).run(bars, synthetic=True)
    assert a == b
    assert a["synthetic"] and a["metrics"]["trade_count"] > 0
    signals = {s["candidate"]["id"]: s["candidate"] for s in a["signals"]}
    for trade in a["trades"]:
        assert trade["entry_at"] > signals[trade["signal_id"]]["timestamp"]
        assert trade["net_pnl"] == pytest.approx(trade["gross_pnl"] - trade["fees"])
        assert trade["exit_at"] >= trade["entry_at"]


def test_partial_and_latency_fill(candidate, quote, portfolio, at):
    from aegis.domain import Bar

    decision = RiskEngine().evaluate(candidate, quote, portfolio, at)
    bar = Bar(
        symbol="AAPL",
        start=at,
        end=at + timedelta(minutes=1),
        available_at=at + timedelta(minutes=1),
        open=100,
        high=101,
        low=99,
        close=100,
        volume=100,
    )
    order = {"candidate": candidate, "decision": decision, "remaining": decision.qty}
    fill = FillModel(participation=0.01)
    assert fill.entry(order, bar) is None
    bar = bar.model_copy(
        update={
            "start": at + timedelta(minutes=1),
            "end": at + timedelta(minutes=2),
            "available_at": at + timedelta(minutes=2),
        }
    )
    assert fill.entry(order, bar)[1] == 1
    assert fill.entry(order, bar.model_copy(update={"volume": 0})) is None


def test_limit_not_filled_through_limit(candidate, quote, portfolio, at):
    from aegis.domain import Bar

    decision = RiskEngine().evaluate(candidate, quote, portfolio, at)
    bar = Bar(
        symbol="AAPL",
        start=at + timedelta(minutes=1),
        end=at + timedelta(minutes=2),
        available_at=at + timedelta(minutes=2),
        open=110,
        high=111,
        low=109,
        close=110,
        volume=10000,
    )
    assert (
        FillModel().entry({"candidate": candidate, "decision": decision, "remaining": decision.qty}, bar)
        is None
    )


@pytest.mark.parametrize("name", [s.name for s in baselines()])
def test_each_baseline_runs(name, bars):
    result = Backtester(Baseline(name)).run(bars[:300], synthetic=True)
    assert result["strategy"]["name"] == name
    assert "max_drawdown" in result["metrics"]


def test_cost_models_ordered():
    a, b, c = [FillModel.named(n) for n in ("optimistic", "standard", "conservative")]
    assert a.price(100, "buy") <= b.price(100, "buy") <= c.price(100, "buy")
    assert a.price(100, "sell") >= b.price(100, "sell") >= c.price(100, "sell")


def test_fold_order_and_embargo():
    bars = synthetic_bars(days=10, symbols=("SPY",))
    folds = list(temporal_folds(bars, 3, 2, 2, 1))
    assert folds
    for f in folds:
        assert max(f["train"]) < min(f["validation"]) < min(f["test"])
        assert not set(f["train"]) & set(f["test"])


def test_grid_and_seed():
    grid = parameter_grid({"a": [1, 2], "b": [3, 4]})
    assert len(grid) == 4
    assert parameter_grid({"a": [1, 2], "b": [3, 4]}, 2, 7) == parameter_grid(
        {"a": [1, 2], "b": [3, 4]}, 2, 7
    )


def test_walk_forward_test_evaluated_once():
    bars = synthetic_bars(days=4, symbols=("SPY",))
    result = walk_forward(bars, "orb", {"opening_minutes": [5, 15]}, 2, 1, 1, 0, synthetic=True)
    assert len(result["folds"]) == 1
    assert result["folds"][0]["test_evaluations"] == 1
    assert result["folds"][0]["combinations_tested"] == 2


def test_metrics_and_mc(at):
    trades = [
        {
            "symbol": "AAPL",
            "strategy": "orb",
            "side": "buy",
            "entry": 100,
            "exit": 101,
            "qty": 1,
            "entry_at": at.isoformat(),
            "exit_at": (at + timedelta(minutes=5)).isoformat(),
            "gross_pnl": v,
            "net_pnl": v,
            "fees": 0,
        }
        for v in [10, -5, 20, -5]
    ]
    curve = [{"timestamp": at.isoformat(), "equity": 1020, "exposure": 0}]
    p = performance(trades, curve, 1000)
    assert p["expectancy"] == 5 and p["profit_factor"] == 3 and p["win_rate"] == 0.5
    assert p["sharpe"] is None
    a = monte_carlo(trades, 1000, 50, seed=7)
    assert a == monte_carlo(trades, 1000, 50, seed=7)


def ml_rows():
    rng = np.random.default_rng(7)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(500):
        at = start + timedelta(hours=i)
        x, z = rng.normal(size=2)
        rows.append(
            {
                "timestamp": at.isoformat(),
                "label_end": (at + timedelta(minutes=5)).isoformat(),
                "symbol": "SPY",
                "features": {"x": x, "z": z},
                "target": int(x + rng.normal() > 0),
                "future_return": x * 0.001,
            }
        )
    return rows


def test_purged_calibrated_ml():
    rows = ml_rows()
    train, valid, test = purged_split(rows)
    assert max(r["label_end"] for r in train) < min(r["timestamp"] for r in valid)
    model, result = experiment(rows, ["x", "z"])
    assert 0 <= result["metrics"]["brier_score"] <= 1
    assert len(result["permutation_importance"]) == 2 and len(result["ablation"]) == 2
    assert result["regression"]["mse"] < result["regression"]["constant_mse"]
    assert "log_loss" in result["metrics"]


def test_future_labels_purged():
    rows = ml_rows()
    rows[299]["label_end"] = rows[350]["timestamp"]
    train, _, _ = purged_split(rows)
    assert rows[299] not in train


def test_calibration_and_drift():
    report = probability_report([0, 1, 0, 1], [0.1, 0.9, 0.2, 0.8])
    assert report["brier_score"] == pytest.approx(0.025)
    rng = np.random.default_rng(7)
    assert drift_report({"x": rng.normal(size=1000)}, {"x": rng.normal(5, 1, 1000)})["blocked"]
    assert drift_report({"x": [1] * 100}, {"x": [1] * 100})["blocked"] is False
