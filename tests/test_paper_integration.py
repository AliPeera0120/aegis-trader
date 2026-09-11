"""Opt-in real PAPER checks. Writes require a separate explicit toggle."""

import os
import pytest
from aegis.config import Settings
from aegis.broker import AlpacaPaperBroker


@pytest.mark.paper
def test_real_paper_auth_clock_positions():
    if os.getenv("AEGIS_RUN_PAPER_INTEGRATION") != "1":
        pytest.skip("Set AEGIS_RUN_PAPER_INTEGRATION=1 with PAPER credentials")
    settings = Settings()
    assert settings.trading_mode == "PAPER", "Never run integration tests against LIVE"
    broker = AlpacaPaperBroker(settings)
    assert broker.get_account()["id"]
    assert "is_open" in broker.get_clock()
    assert isinstance(broker.get_positions(), list)
    assert broker.get_asset("SPY")["tradable"]


@pytest.mark.paper
def test_real_paper_data_history(tmp_path):
    if os.getenv("AEGIS_RUN_PAPER_INTEGRATION") != "1":
        pytest.skip("Paper integration credentials unavailable")
    from aegis.data import AlpacaData, DataRepository
    from aegis.store import Store
    from datetime import datetime, timezone

    settings = Settings()
    assert settings.trading_mode == "PAPER"
    provider = AlpacaData(settings, DataRepository(Store("sqlite:///:memory:"), tmp_path))
    bars = provider.history(
        ["SPY"],
        datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc),
        datetime(2025, 1, 6, 15, tzinfo=timezone.utc),
    )
    assert len(bars) > 0 and all(b.end <= b.available_at for b in bars)
