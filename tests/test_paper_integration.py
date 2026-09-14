"""Opt-in real PAPER checks. Writes require a separate explicit toggle."""

import os
import threading
import time
import pytest
from aegis.config import Settings
from aegis.broker import AlpacaPaperBroker


@pytest.mark.paper
def test_real_paper_stream_authentication(tmp_path):
    if os.getenv("AEGIS_RUN_PAPER_INTEGRATION") != "1":
        pytest.skip("Paper integration credentials unavailable")
    from aegis.data import AlpacaData, DataRepository
    from aegis.store import Store

    settings = Settings()
    assert settings.trading_mode == "PAPER"

    async def discard(value):
        pass

    data = AlpacaData(settings, DataRepository(Store("sqlite:///:memory:"), tmp_path))
    streams = [
        data.stream(["SPY"], discard, discard, discard),
        AlpacaPaperBroker(settings).stream_trade_updates(discard),
    ]
    threads = [threading.Thread(target=s.run, daemon=True) for s in streams]
    try:
        for thread in threads:
            thread.start()
        until = time.monotonic() + 20
        while time.monotonic() < until and not all(getattr(s, "_running", False) for s in streams):
            time.sleep(0.1)
        # alpaca-py sets _running only after authentication and subscription sends succeed.
        assert all(getattr(s, "_running", False) for s in streams)
    finally:
        for stream in streams:
            stream.stop()
        for thread in threads:
            thread.join(timeout=6)


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
