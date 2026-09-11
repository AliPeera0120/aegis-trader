from dataclasses import replace
from datetime import timedelta
import pytest
from aegis.risk import RiskEngine, RiskLimits


def test_absurd_position_is_bounded(candidate, quote, portfolio, at):
    d = RiskEngine().evaluate(candidate.model_copy(update={"requested_qty": 10**12}), quote, portfolio, at)
    assert d.accepted and d.qty <= 9
    assert d.notional <= 1000 and d.risk_dollars <= 250


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("kill_switch", True, "KILL_SWITCH"),
        ("reconciled", False, "UNHEALTHY_OR_UNRECONCILED"),
        ("broker_connected", False, "UNHEALTHY_OR_UNRECONCILED"),
        ("data_connected", False, "UNHEALTHY_OR_UNRECONCILED"),
        ("market_open", False, "MARKET_CLOSED"),
        ("account_blocked", True, "ACCOUNT_BLOCKED"),
        ("equity", 97000, "DAILY_LOSS_LIMIT"),
        ("week_start_equity", 110000, "WEEKLY_LOSS_LIMIT"),
        ("strategy_drawdown", 0.05, "STRATEGY_DRAWDOWN"),
        ("trades_today", 10, "MAX_TRADES"),
        ("losses_today", 5, "MAX_LOSSES"),
        ("consecutive_losses", 3, "LOSS_COOLDOWN"),
        ("asset_tradable", False, "ASSET_NOT_TRADABLE"),
        ("strategy_eligible", False, "STRATEGY_NOT_ELIGIBLE"),
        ("drift_blocked", True, "STRATEGY_NOT_ELIGIBLE"),
        ("buying_power", 0, "INVALID_ACCOUNT_OR_BUYING_POWER"),
        ("equity", float("nan"), "INVALID_ACCOUNT_OR_BUYING_POWER"),
    ],
)
def test_fail_closed(field, value, reason, candidate, quote, portfolio, at):
    d = RiskEngine().evaluate(candidate, quote, replace(portfolio, **{field: value}), at)
    assert not d.accepted and reason in d.reasons


@pytest.mark.parametrize("delta", [-6, 1])
def test_bad_quote_time(delta, candidate, quote, portfolio, at):
    q = quote.model_copy(update={"timestamp": at + timedelta(seconds=delta)})
    assert not RiskEngine().evaluate(candidate, q, portfolio, at).accepted


def test_spread_and_missing(candidate, quote, portfolio, at):
    assert (
        "SPREAD"
        in RiskEngine().evaluate(candidate, quote.model_copy(update={"ask": 101}), portfolio, at).reasons
    )
    assert "MISSING_QUOTE" in RiskEngine().evaluate(candidate, None, portfolio, at).reasons


def test_no_ev(candidate, quote, portfolio, at):
    c = candidate.model_copy(update={"expected_value": None, "ev_lower_bound": None})
    assert not RiskEngine().evaluate(c, quote, portfolio, at).accepted
    assert RiskEngine().evaluate(c, quote, portfolio, at, research=True).accepted


def test_no_averaging_down(candidate, quote, portfolio, at):
    p = replace(portfolio, positions=[{"symbol": "AAPL", "notional": 100, "risk_dollars": 1}])
    assert "NO_AVERAGING_OR_DUPLICATE_EXPOSURE" in RiskEngine().evaluate(candidate, quote, p, at).reasons


def test_reservations_consume_capital(candidate, quote, portfolio, at):
    p = replace(
        portfolio, reservations=[{"symbol": "QQQ", "notional": 14950, "risk_dollars": 1, "sector": "unknown"}]
    )
    assert not RiskEngine().evaluate(candidate, quote, p, at).accepted


def test_live_caps(candidate, quote, portfolio, at):
    d = RiskEngine().evaluate(candidate, quote, portfolio, at, live_capital=5000, live_order_cap=150)
    assert d.qty == 1 and d.notional <= 150


def test_zero_live_capital(candidate, quote, portfolio, at):
    assert (
        not RiskEngine().evaluate(candidate, quote, portfolio, at, live_capital=0, live_order_cap=0).accepted
    )


@pytest.mark.parametrize("method", ["fixed_dollar", "fixed_fractional", "volatility_adjusted"])
def test_sizing_methods(method, candidate, quote, portfolio, at):
    d = RiskEngine(RiskLimits(sizing=method)).evaluate(candidate, quote, portfolio, at)
    assert d.accepted and 0 < d.qty <= 9


def test_wrong_clock(candidate, quote, portfolio, at):
    p = replace(portfolio, clock_at=at + timedelta(seconds=1))
    assert "STALE_OR_FUTURE_CLOCK" in RiskEngine().evaluate(candidate, quote, p, at).reasons
