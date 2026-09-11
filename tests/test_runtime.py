from datetime import datetime, timezone, timedelta
from unittest.mock import Mock
import pytest
from aegis.runtime import TradingRuntime
from aegis.domain import utcnow
from aegis.research import synthetic_bars
from aegis.provenance import Membership, historical_universe, CorporateEvent, corporate_flags
from aegis.cli import main
from aegis.data import AlpacaData, DataRepository
from aegis.evidence import Registry, estimate_ev
from aegis.strategies import Baseline
from conftest import FakeBroker


def test_stream_event_processing_quarantine_and_recovery(store, settings):
    runtime = TradingRuntime(settings, store, broker=FakeBroker())
    now = utcnow()
    runtime._enqueue(
        "quote",
        {
            "symbol": "AAPL",
            "timestamp": now.isoformat(),
            "bid_price": 100,
            "ask_price": 100.01,
            "bid_size": 20,
            "ask_size": 30,
        },
    )
    runtime.process_events()
    assert runtime.quotes["AAPL"].bid == 100
    assert store.control("data_health")["connected"]
    runtime._enqueue(
        "quote", {"symbol": "MSFT", "timestamp": now.isoformat(), "bid_price": 110, "ask_price": 100}
    )
    runtime.process_events()
    assert not store.control("data_health")["connected"]
    assert store.events()[0]["event"] == "EVENT_QUARANTINED"
    fresh = utcnow()
    runtime._enqueue(
        "quote", {"symbol": "AAPL", "timestamp": fresh.isoformat(), "bid_price": 100, "ask_price": 100.02}
    )
    runtime.process_events()
    assert store.control("data_health")["connected"]


def test_stream_bar_and_trade(store, settings):
    runtime = TradingRuntime(settings, store, broker=FakeBroker())
    start = utcnow().replace(second=0, microsecond=0) - timedelta(minutes=1)
    runtime._enqueue(
        "bar",
        {
            "symbol": "SPY",
            "timestamp": start.isoformat(),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100.1,
            "volume": 10000,
        },
    )
    runtime._enqueue(
        "trade", {"symbol": "SPY", "timestamp": start.isoformat(), "price": 100, "size": 20, "id": 123}
    )
    runtime.process_events()
    assert len(runtime.history["SPY"]) == 1
    assert runtime.history["SPY"][0].available_at >= runtime.history["SPY"][0].end
    assert store.list("system_events")[0]["payload"]["event"] == "TRADE_PRINT"


def test_scanner_ranks_and_uses_execution_gate(store, settings):
    runtime = TradingRuntime(settings, store, broker=FakeBroker())
    bars = synthetic_bars(days=1, symbols=("SPY",))
    runtime.history["SPY"].extend(bars[:25])
    runtime.eligible_symbols = {"SPY"}
    execution = Mock()
    execution.submit.return_value = {"status": "REJECTED", "reasons": ["STRATEGY_NOT_ELIGIBLE"]}
    runtime.execution = execution
    # Pick a deterministic baseline with an intentionally permissive research trigger.
    runtime.strategies = [Baseline("vwap_continuation", {"min_momentum": -1})]
    at = bars[24].end
    results = runtime.scan(at)
    assert results
    assert execution.submit.call_count == len(results)
    assert store.list("ranks")
    assert all(r["decision"]["status"] == "REJECTED" for r in results)


def test_service_opt_in(store, settings):
    runtime = TradingRuntime(settings, store)
    with pytest.raises(ValueError):
        runtime.start()


def test_session_report_requires_real_account(store, settings):
    runtime = TradingRuntime(settings, store)
    result = runtime.report(datetime(2025, 1, 6).date())
    assert result["metrics"] is None
    assert result["trades"] == [] and result["mode"] == "PAPER"
    assert store.list("reports")


def test_point_in_time_universe_and_events(at):
    rows = [
        Membership(
            universe_version="v1",
            symbol="OLD",
            effective_from=at - timedelta(days=5),
            effective_to=at + timedelta(days=1),
            known_at=at - timedelta(days=5),
            source="fixture",
        ),
        Membership(
            universe_version="v1",
            symbol="NEW",
            effective_from=at - timedelta(days=1),
            known_at=at + timedelta(days=1),
            source="fixture",
        ),
    ]
    assert historical_universe(rows, at, "v1") == ["OLD"]
    event = CorporateEvent(
        symbol="OLD",
        event_type="split",
        published_at=at + timedelta(days=1),
        effective_at=at + timedelta(days=2),
        source="fixture",
        details={"ratio": 2},
    )
    assert not corporate_flags([event], "OLD", at, at + timedelta(days=5), at)


def test_data_sdk_history_adapter_mock(store, settings, tmp_path):
    from aegis.domain import Bar
    from types import SimpleNamespace

    provider = AlpacaData(settings, DataRepository(store, tmp_path))
    timestamp = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)
    row = SimpleNamespace(
        timestamp=timestamp, open=100, high=101, low=99, close=100.2, volume=10000, vwap=100.1
    )
    response = Mock()
    response.data = {"SPY": [row]}
    response.model_dump.return_value = {"data": {"SPY": [{"timestamp": timestamp.isoformat()}]}}
    provider.client = Mock()
    provider.client.get_stock_bars.return_value = response
    bars = provider.history(["SPY"], timestamp, timestamp + timedelta(minutes=1))
    assert len(bars) == 1 and isinstance(bars[0], Bar)
    assert bars[0].end == timestamp + timedelta(minutes=1)
    assert list(tmp_path.glob("*.json.gz"))
    assert store.list("datasets")[0]["payload"]["synthetic"] is False


def test_expected_value_only_prior_closed_outcomes(at):
    trades = [
        {"exit_at": (at - timedelta(days=i + 1)).isoformat(), "gross_pnl": 2 if i % 3 else -1, "qty": 1}
        for i in range(100)
    ]
    future = {"exit_at": (at + timedelta(days=1)).isoformat(), "gross_pnl": 100000, "qty": 1}
    assert estimate_ev(trades, at) == estimate_ev(trades + [future], at)
    assert estimate_ev(trades, at)["eligible"]
    assert not estimate_ev(trades[:5], at)["eligible"]


def test_promotion_forward_with_immutable_evidence(store):
    registry = Registry(store)
    key = registry.register(Baseline("orb"))
    store.put(
        "evidence",
        "historical",
        {"strategy_key": key, "synthetic": False, "verified": True, "evaluation": "BACKTEST"},
    )
    registry.promote(key, "BACKTEST", ["historical"], "test")
    assert registry.stage(key)["stage"] == "BACKTEST"
    with pytest.raises(ValueError):
        store.put("evidence", "historical", {"tampered": True})


def test_cli_paper_refuses_live_env(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(SystemExit):
        main(["paper"])
    assert "Command failed" in capsys.readouterr().err


def test_no_strategy_broker_dependency():
    import ast
    import inspect
    import aegis.strategies

    tree = ast.parse(inspect.getsource(aegis.strategies))
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any("broker" in name or "alpaca" in name or "execution" in name for name in imports)
