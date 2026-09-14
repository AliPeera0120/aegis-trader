from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from aegis.broker import AlpacaLiveBroker, AlpacaPaperBroker, BrokerError
from aegis.config import Settings
from aegis.data import AlpacaData, DataRepository, NY
from aegis.domain import utcnow
from aegis.evidence import Registry
from aegis.execution import ExecutionService
from aegis.learning import training_rows, train_closed_outcomes
from aegis.risk import RiskEngine
from aegis.runtime import TradingRuntime
from aegis.strategies import Baseline
from conftest import FakeBroker


def experimental(store, settings, candidate, quote):
    strategy = Baseline("orb")
    key = Registry(store).register(strategy)
    settings = settings.model_copy(update={"paper_learning_enabled": True, "paper_learning_strategies": key})
    now = utcnow()
    c = candidate.model_copy(
        update={
            "timestamp": now,
            "version": strategy.version,
            "expected_value": None,
            "ev_lower_bound": None,
            "evidence_id": None,
        }
    )
    q = quote.model_copy(update={"timestamp": now})
    broker = FakeBroker(now)
    store.set_control("data_health", {"connected": True, "at": now.isoformat()})
    service = ExecutionService(store, broker, RiskEngine(), settings, Registry(store))
    return service, broker, c, q


def test_unproven_paper_is_one_share_and_does_not_promote(store, settings, candidate, quote):
    service, broker, c, q = experimental(store, settings, candidate, quote)
    assert service.submit(c, q)["status"] == "SUBMITTED"
    approved = broker.submissions[0]
    assert approved.decision.qty == 1
    assert "PAPER_EXPERIMENT" in approved.decision.reasons
    assert not store.list("evidence") and not store.list("promotions")
    assert service.registry.stage("strategy:orb:" + c.version) == {"stage": "IDEA", "enabled": False}
    order = next(iter(broker.orders.values()))
    order.update(
        status="filled",
        filled_qty="1",
        filled_avg_price="100",
        filled_at=utcnow().isoformat(),
        legs=[{"filled_qty": "1", "filled_avg_price": "99", "filled_at": utcnow().isoformat()}],
    )
    service.apply_update(order)
    t = store.list("trades", "PAPER")[0]["payload"]
    assert (
        t["execution_purpose"] == "PAPER_EXPERIMENT" and datetime.fromisoformat(t["signal_at"]) == c.timestamp
    )
    assert t["fees_status"] == "unreconciled" and t["net_pnl"] == -1


@pytest.mark.parametrize(
    "condition",
    [
        "not_enabled",
        "wrong_version",
        "stale_quote",
        "stale_bar",
        "kill",
        "loss",
        "drift",
        "closed",
        "mode_mismatch",
        "too_expensive",
        "illiquid",
        "two_positions",
    ],
)
def test_experiment_retains_risk_controls(condition, store, settings, candidate, quote):
    service, broker, c, q = experimental(store, settings, candidate, quote)
    if condition == "not_enabled":
        service.settings = service.settings.model_copy(update={"paper_learning_enabled": False})
    elif condition == "wrong_version":
        c = c.model_copy(update={"version": "unapproved-version"})
    elif condition == "stale_quote":
        q = q.model_copy(update={"timestamp": q.timestamp - timedelta(seconds=60)})
    elif condition == "stale_bar":
        c = c.model_copy(update={"features": {**c.features, "data_age_seconds": 120}})
    elif condition == "kill":
        store.set_control("stop:PAPER", {"stopped": True})
    elif condition == "loss":
        service.reconcile()
        broker.account["equity"] = "99975"
    elif condition == "drift":
        store.set_control("drift:strategy:orb:" + c.version, {"blocked": True})
    elif condition == "closed":
        broker.get_clock = lambda: {"timestamp": utcnow().isoformat(), "is_open": False}
    elif condition == "mode_mismatch":
        broker.mode = "LIVE"
    elif condition == "too_expensive":
        c = c.model_copy(update={"entry": 1100, "stop": 1090, "target": 1120})
        q = q.model_copy(update={"bid": 1099.9, "ask": 1100.1})
    elif condition == "illiquid":
        c = c.model_copy(update={"features": {**c.features, "dollar_volume": 1}})
    elif condition == "two_positions":
        broker.positions = [
            {"symbol": s, "market_value": "100", "qty": "1", "current_price": "100"} for s in ("SPY", "QQQ")
        ]
    assert service.submit(c, q)["status"] == "REJECTED"
    assert not broker.submissions


def test_paper_opt_in_cannot_route_live(store, settings, candidate, quote):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, trading_mode="LIVE", paper_learning_enabled=True)
    service, broker, c, q = experimental(store, settings, candidate, quote)
    service.submit(c, q)
    client = Mock()
    live = AlpacaLiveBroker(
        settings.model_copy(
            update={
                "alpaca_live_key": settings.alpaca_paper_key,
                "alpaca_live_secret": settings.alpaca_paper_secret,
            }
        ),
        client=client,
    )
    with pytest.raises(BrokerError, match="Paper experiments"):
        live.submit_order(broker.submissions[0])
    client.submit_order.assert_not_called()


def test_expired_entry_cancels_but_fill_keeps_protection(store, settings, candidate, quote):
    service, broker, c, q = experimental(store, settings, candidate, quote)
    service.submit(c, q)
    service.expire_entries(utcnow() + timedelta(minutes=4))
    assert len(broker.canceled) == 1
    broker.canceled.clear()
    order = next(iter(broker.orders.values()))
    order.update(status="filled", filled_qty="1", filled_avg_price="100")
    service.apply_update(order)
    service.expire_entries(utcnow() + timedelta(minutes=5))
    assert not broker.canceled


def test_invalid_rest_quote_does_not_remove_healthy_symbol(store, settings):
    data = AlpacaData(settings, DataRepository(store))
    q = dict(timestamp=utcnow(), bid_price=100, ask_price=100.1, bid_size=1, ask_size=1)
    data.client = Mock()
    data.client.get_stock_latest_quote.return_value = {
        "SPY": SimpleNamespace(**q),
        "AAPL": SimpleNamespace(**{**q, "ask_price": 0}),
    }
    assert list(data.latest_quotes(["SPY", "AAPL"])) == ["SPY"]


def test_bracket_reconciliation_requests_nested_protective_legs(settings):
    client = Mock()
    client.get_order_by_client_id.return_value = {"id": "bracket-1", "order_class": "bracket"}
    client.get_order_by_id.return_value = {"id": "bracket-1", "legs": [{"id": "stop-1"}]}
    broker = AlpacaPaperBroker(settings, client=client)
    assert broker.get_order("client-1")["legs"] == [{"id": "stop-1"}]
    assert client.get_order_by_id.call_args.kwargs["filter"].nested is True


def test_learning_uses_only_closed_prior_labels_and_cost_buffer():
    from aegis.learning import FEATURES

    now = utcnow()
    t = {
        "signal_at": (now - timedelta(hours=1)).isoformat(),
        "exit_at": (now - timedelta(minutes=1)).isoformat(),
        "features": dict.fromkeys(FEATURES, 1.0),
        "gross_pnl": 0.02,
        "qty": 1,
        "entry": 100,
        "symbol": "SPY",
        "order_id": "closed",
    }
    future = {**t, "exit_at": (now + timedelta(minutes=1)).isoformat()}
    rows = training_rows([t, future], now)
    assert len(rows) == 1 and rows[0]["target"] == 0


def test_insufficient_learning_data_does_not_train_or_promote(store, settings, candidate, quote):
    service, _, _, _ = experimental(store, settings, candidate, quote)
    result = train_closed_outcomes(service.settings, store)
    assert next(iter(result["strategies"].values()))["status"] == "COLLECTING"
    assert not store.list("model_versions") and not store.list("promotions")


def test_session_clock_opens_scanner_and_close_cancels_even_without_positions(store, settings, at):
    runtime = TradingRuntime(settings, store)
    runtime.execution = Mock(account={"equity": "100000"}, positions=[], stop_key="stop:PAPER")
    runtime.execution.reconcile.return_value = True
    runtime.initialize_session = Mock()
    runtime.scan = Mock()
    runtime.report = Mock()
    opening, closing = runtime.calendar.session(at)
    runtime.execution.clock = {"is_open": False}
    runtime.run_cycle(opening - timedelta(minutes=20))
    runtime.initialize_session.assert_called_once()
    runtime.scan.assert_not_called()
    runtime.session_date = at.astimezone(NY).date()
    runtime.execution.clock = {"is_open": True}
    runtime.run_cycle(opening + timedelta(seconds=1))
    runtime.scan.assert_called_once()
    runtime.run_cycle(closing - timedelta(minutes=5))
    runtime.execution.emergency_stop.assert_called_once_with("END_OF_DAY", flatten=True)
    runtime.execution.clock = {"is_open": False}
    runtime.run_cycle(closing + timedelta(minutes=1))
    runtime.report.assert_called_once()


def test_quote_burst_coalesces_to_latest_snapshot(store, settings):
    runtime = TradingRuntime(settings, store)
    now = utcnow()
    for i in range(2000):
        runtime._enqueue(
            "quote",
            {
                "symbol": "SPY",
                "timestamp": (now - timedelta(microseconds=2000 - i)).isoformat(),
                "bid_price": 100,
                "ask_price": 100.01,
            },
        )
    assert runtime.queue.qsize() == 0 and len(runtime.pending_quotes) == 1
    runtime.process_events()
    assert len(store.list("quotes")) == 1 and store.control("data_health")["connected"]


def test_premarket_bar_cannot_contaminate_opening_range(store, settings, at):
    runtime = TradingRuntime(settings, store)
    opening, _ = runtime.calendar.session(at)
    runtime._enqueue(
        "bar",
        {
            "symbol": "SPY",
            "timestamp": (opening - timedelta(minutes=1)).isoformat(),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 10000,
        },
    )
    runtime.process_events()
    assert not runtime.history["SPY"]


def test_temporal_training_produces_research_record_only(store, settings, candidate, quote):
    from aegis.learning import FEATURES

    service, _, c, _ = experimental(store, settings, candidate, quote)
    now = utcnow()
    for i in range(100):
        start = now - timedelta(days=110 - i)
        trade = {
            "execution_purpose": "PAPER_EXPERIMENT",
            "strategy": "orb",
            "version": c.version,
            "signal_at": start.isoformat(),
            "exit_at": (start + timedelta(minutes=30)).isoformat(),
            "features": {f: (i % 9) / 10 + n for n, f in enumerate(FEATURES)},
            "gross_pnl": 2 if i % 3 else -1,
            "qty": 1,
            "entry": 100,
            "symbol": "SPY",
            "order_id": str(i),
        }
        store.put("trades", "fixture-" + str(i), trade, "PAPER")
    first = train_closed_outcomes(service.settings, store, now)
    second = train_closed_outcomes(service.settings, store, now)
    assert first == second
    model = store.list("model_versions", "PAPER")[0]["payload"]
    assert not model["execution_enabled"] and model["metrics"]["test_samples"] == 20
    assert not store.list("evidence") and not store.list("promotions")
