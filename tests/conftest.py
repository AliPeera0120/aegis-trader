from datetime import datetime, timezone
import pytest
from aegis.config import Settings
from aegis.domain import Candidate, Quote
from aegis.store import Store
from aegis.risk import Portfolio


@pytest.fixture
def at():
    return datetime(2025, 1, 6, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def candidate(at):
    return Candidate(
        id="candidate-1",
        symbol="AAPL",
        strategy="orb",
        version="test-v1",
        timestamp=at,
        entry=100,
        stop=99,
        target=103,
        features={"dollar_volume": 10000000, "atr": 0.5},
        expected_value=0.2,
        ev_lower_bound=0.1,
        evidence_id="evidence-1",
    )


@pytest.fixture
def quote(at):
    return Quote(symbol="AAPL", timestamp=at, bid=99.99, ask=100.01, bid_size=100, ask_size=100)


@pytest.fixture
def portfolio(at):
    return Portfolio(
        equity=100000,
        buying_power=100000,
        day_start_equity=100000,
        week_start_equity=100000,
        reconciled=True,
        broker_connected=True,
        data_connected=True,
        market_open=True,
        clock_at=at,
        strategy_eligible=True,
        asset_tradable=True,
    )


@pytest.fixture
def store():
    value = Store("sqlite:///:memory:")
    yield value
    value.engine.dispose()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        trading_mode="PAPER",
        runtime_dir=tmp_path,
        alpaca_paper_key="paper-fixture",
        alpaca_paper_secret="fixture-secret",
        database_url="sqlite:///:memory:",
    )


class FakeBroker:
    mode = "PAPER"

    def __init__(self, now=None):
        from aegis.domain import utcnow

        self.now = now or utcnow()
        self.account = {
            "equity": "100000",
            "last_equity": "100000",
            "cash": "100000",
            "buying_power": "100000",
        }
        self.positions, self.orders, self.submissions, self.canceled = [], {}, [], []
        self.disconnected, self.uncertain, self.flattened = False, False, False

    def get_account(self):
        if self.disconnected:
            raise RuntimeError("Simulated disconnect")
        return self.account

    def get_positions(self):
        return self.positions

    def get_clock(self):
        return {"timestamp": self.now.isoformat(), "is_open": True}

    def get_asset(self, symbol):
        return {
            "symbol": symbol,
            "tradable": True,
            "status": "active",
            "shortable": True,
            "exchange": "NASDAQ",
        }

    def get_orders(self):
        return [o for o in self.orders.values() if o["status"] in {"new", "partially_filled"}]

    def get_order(self, identifier):
        return self.orders[identifier]

    def submit_order(self, approved):
        self.submissions.append(approved)
        order = {
            "id": "broker-" + approved.client_order_id,
            "client_order_id": approved.client_order_id,
            "status": "new",
            "filled_qty": "0",
            "filled_avg_price": None,
            "legs": [],
            "symbol": approved.candidate.symbol,
        }
        self.orders[approved.client_order_id] = order
        if self.uncertain:
            raise TimeoutError("Broker accepted but acknowledgment lost")
        return order

    def cancel_order(self, identifier):
        self.canceled.append(identifier)
        for order in self.orders.values():
            if order["id"] == identifier:
                order["status"] = "canceled"

    def close_all_positions(self):
        self.flattened = True
        self.positions = []
