from datetime import timedelta
from unittest.mock import Mock
import pytest
from aegis.domain import utcnow, transition
from aegis.evidence import Registry
from aegis.execution import ExecutionService
from aegis.risk import RiskEngine
from aegis.broker import AlpacaPaperBroker, AlpacaLiveBroker, ApprovedOrder, BrokerError
from aegis.config import Settings, LIVE_PHRASE
from conftest import FakeBroker


def ready(store, settings, candidate, quote):
    now = utcnow()
    c = candidate.model_copy(update={"timestamp": now})
    q = quote.model_copy(update={"timestamp": now})
    broker = FakeBroker(now)
    registry = Registry(store)
    key = "strategy:orb:test-v1"
    store.set_control("stage:" + key, {"stage": "PAPER_CANDIDATE", "enabled": True})
    store.put(
        "evidence",
        "evidence-1",
        {
            "strategy_key": key,
            "verified": True,
            "synthetic": False,
            "evaluation": "OOS",
            "as_of": (now - timedelta(days=1)).isoformat(),
            "ev": 0.2,
            "ev_lower_bound": 0.1,
        },
    )
    store.set_control("data_health", {"connected": True, "at": now.isoformat()})
    service = ExecutionService(store, broker, RiskEngine(), settings, registry)
    return service, broker, c, q


def test_order_state_machine():
    assert transition("PROPOSED", "RISK_APPROVED") == "RISK_APPROVED"
    assert transition("ACKNOWLEDGED", "ACKNOWLEDGED") == "ACKNOWLEDGED"
    with pytest.raises(ValueError):
        transition("PROPOSED", "FILLED")


def test_ack_is_not_fill_and_duplicate_signal(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    result = service.submit(c, q)
    assert result["status"] == "SUBMITTED"
    assert service._local_orders()[0]["state"] == "ACKNOWLEDGED"
    assert not store.list("fills")
    assert service.submit(c, q)["status"] == "DUPLICATE"
    assert len(broker.submissions) == 1


def test_partial_fill_deduplication_and_weighted_delta(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    service.submit(c, q)
    order = next(iter(broker.orders.values()))
    order.update(
        status="partially_filled", filled_qty="2", filled_avg_price="100", filled_at=utcnow().isoformat()
    )
    service.apply_update(dict(order))
    service.apply_update(dict(order))
    assert len(store.list("fills")) == 1
    order.update(filled_qty="4", filled_avg_price="101")
    service.apply_update(dict(order))
    fills = [r["payload"] for r in store.list("fills")]
    assert sum(f["qty"] for f in fills) == 4
    assert sorted(f["price"] for f in fills) == [100, 102]


def test_submission_timeout_recovered_no_retry(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    broker.uncertain = True
    assert service.submit(c, q)["status"] == "ERROR"
    assert service.reconcile()
    assert service._local_orders()[0]["state"] == "ACKNOWLEDGED"
    assert service.submit(c, q)["status"] == "DUPLICATE"
    assert len(broker.submissions) == 1


def test_disconnect_blocks(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    broker.disconnected = True
    assert service.submit(c, q)["status"] == "REJECTED"
    assert not broker.submissions


def test_unowned_broker_order_blocks(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    broker.orders["manual"] = {
        "id": "manual",
        "client_order_id": "manual",
        "status": "new",
        "filled_qty": "0",
    }
    assert service.submit(c, q)["status"] == "REJECTED"


def test_kill_persists_and_keeps_partial_protection(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    service.submit(c, q)
    result = service.emergency_stop()
    assert result["stopped"] and broker.canceled
    assert store.control("stop:PAPER")["stopped"]
    assert not broker.flattened
    broker.orders.clear()
    assert service.submit(c.model_copy(update={"id": "second"}), q)["status"] == "REJECTED"


def test_partial_bracket_not_canceled_by_stop(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    service.submit(c, q)
    order = next(iter(broker.orders.values()))
    order.update(filled_qty="1", status="partially_filled", filled_avg_price="100")
    service.apply_update(dict(order))
    result = service.emergency_stop()
    assert not broker.canceled and result["retained_partial_brackets"]


def test_kill_flatten_explicit(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    service.emergency_stop(flatten=True)
    assert broker.flattened


def test_resume_cannot_clear_daily_breach(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    service.reconcile()
    broker.account["equity"] = "97000"
    service.emergency_stop()
    with pytest.raises(ValueError):
        service.resume()


def test_unverified_evidence_rejected(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    assert service.submit(c.model_copy(update={"expected_value": 999}), q)["status"] == "REJECTED"
    assert not broker.submissions


def test_paper_adapter_cannot_use_live_credentials(settings, candidate, quote, portfolio, at):
    client = Mock()
    client.submit_order.return_value = {"id": "paper-order"}
    s = Settings(
        _env_file=None,
        alpaca_paper_key="paper",
        alpaca_paper_secret="paper-secret",
        alpaca_live_key="live",
        alpaca_live_secret="live-secret",
    )
    broker = AlpacaPaperBroker(s, client=client)
    decision = RiskEngine().evaluate(candidate, quote, portfolio, at)
    broker.submit_order(ApprovedOrder("aegis-test", candidate, decision))
    req = client.submit_order.call_args.kwargs["order_data"]
    assert req.order_class.value == "bracket" and req.type.value == "limit"
    assert req.extended_hours is False
    assert broker.mode == "PAPER"


def test_clean_install_live_locked():
    s = Settings(_env_file=None)
    assert s.trading_mode == "PAPER" and len(s.live_locks()) >= 6


@pytest.mark.parametrize(
    "missing",
    [
        "trading_mode",
        "live_trading_enabled",
        "live_confirmation_phrase",
        "alpaca_live_key",
        "alpaca_live_secret",
        "live_capital_limit",
        "live_max_order_notional",
        "live_stage",
    ],
)
def test_each_live_gate_required(missing):
    values = {
        "trading_mode": "LIVE",
        "live_trading_enabled": True,
        "live_confirmation_phrase": LIVE_PHRASE,
        "alpaca_live_key": "fixture",
        "alpaca_live_secret": "fixture",
        "live_capital_limit": 1000,
        "live_max_order_notional": 100,
        "live_stage": "APPROVAL",
    }
    values.pop(missing)
    assert Settings(_env_file=None, **values).live_locks()


def test_readiness_blocks_even_fully_configured_live(candidate, quote, portfolio, at):
    s = Settings(
        _env_file=None,
        trading_mode="LIVE",
        live_trading_enabled=True,
        live_confirmation_phrase=LIVE_PHRASE,
        alpaca_live_key="fixture",
        alpaca_live_secret="fixture",
        live_capital_limit=1000,
        live_max_order_notional=100,
        live_stage="APPROVAL",
    )
    client = Mock()
    broker = AlpacaLiveBroker(s, client=client, readiness=lambda: {"locked": True})
    with pytest.raises(BrokerError):
        broker.submit_order(
            ApprovedOrder("test", candidate, RiskEngine().evaluate(candidate, quote, portfolio, at))
        )
    client.submit_order.assert_not_called()


def test_sdk_exception_redacted(settings):
    client = Mock()
    client.get_account.side_effect = RuntimeError("PRIVATE_KEY_SHOULD_NOT_LEAK")
    with pytest.raises(BrokerError) as info:
        AlpacaPaperBroker(settings, client=client).get_account()
    assert "PRIVATE_KEY" not in str(info.value)


def test_closed_trade_duplicate_update(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    service.submit(c, q)
    order = next(iter(broker.orders.values()))
    order.update(
        status="filled",
        filled_qty="4",
        filled_avg_price="100",
        filled_at=utcnow().isoformat(),
        legs=[
            {"id": "stop-leg", "filled_qty": "4", "filled_avg_price": "99", "filled_at": utcnow().isoformat()}
        ],
    )
    service.apply_update(dict(order))
    service.apply_update(dict(order))
    assert len(store.list("trades")) == 1
    assert service._local_orders()[0]["state"] == "CLOSED"


def test_explicit_liquidation_fills_close_owned_trade(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    service.submit(c, q)
    order = next(iter(broker.orders.values()))
    order.update(status="filled", filled_qty="4", filled_avg_price="100", filled_at=utcnow().isoformat())
    service.apply_update(dict(order))
    service._state(order["client_order_id"], "EXIT_PENDING")
    service.apply_liquidation(
        {
            "id": "liquidation-1",
            "filled_qty": "4",
            "filled_avg_price": "101",
            "filled_at": utcnow().isoformat(),
        },
        "AAPL",
    )
    assert service._local_orders()[0]["state"] == "CLOSED"
    assert store.list("trades")[0]["payload"]["gross_pnl"] == 4
    service.apply_liquidation(
        {
            "id": "liquidation-1",
            "filled_qty": "4",
            "filled_avg_price": "101",
            "filled_at": utcnow().isoformat(),
        },
        "AAPL",
    )
    assert len(store.list("trades")) == 1


def test_late_fill_after_cancel_is_recorded(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    service.submit(c, q)
    order = next(iter(broker.orders.values()))
    order.update(status="canceled", filled_qty="0")
    service.apply_update(dict(order))
    order.update(status="filled", filled_qty="2", filled_avg_price="100", filled_at=utcnow().isoformat())
    service.apply_update(dict(order))
    assert service._local_orders()[0]["state"] == "FILLED"
    assert store.list("fills")[0]["payload"]["qty"] == 2
