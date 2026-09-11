from datetime import datetime, timezone, timedelta
import pytest
from pydantic import ValidationError
from aegis.data import Calendar, resample, quality_report, DataRepository
from aegis.domain import Bar, Quote
from aegis.features import FeatureEngine
from aegis.research import synthetic_bars


@pytest.fixture(scope="module")
def bars():
    return synthetic_bars(days=2, symbols=("AAPL",))


def test_calendar_holiday_early_close_dst():
    cal = Calendar()
    assert cal.session(datetime(2025, 7, 4, tzinfo=timezone.utc).date()) is None
    early = cal.session(datetime(2025, 11, 28, tzinfo=timezone.utc).date())
    assert early[1].hour == 18
    assert cal.session(datetime(2025, 3, 7).date())[0].hour == 14
    assert cal.session(datetime(2025, 3, 10).date())[0].hour == 13


@pytest.mark.parametrize("field,value", [("open", -1), ("high", 1), ("volume", -1), ("close", float("nan"))])
def test_invalid_ohlc(field, value, bars):
    data = bars[0].model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        Bar(**data)


def test_naive_timestamp_rejected(bars):
    data = bars[0].model_dump()
    data["start"] = datetime(2025, 1, 6, 14, 30)
    with pytest.raises(ValidationError):
        Bar(**data)


def test_early_available_rejected(bars):
    data = bars[0].model_dump()
    data["available_at"] = data["start"]
    with pytest.raises(ValidationError):
        Bar(**data)


def test_crossed_quote(at):
    with pytest.raises(ValidationError):
        Quote(symbol="AAPL", timestamp=at, bid=101, ask=100)


def test_prefix_invariance_and_future_poisoning(bars):
    engine = FeatureEngine()
    at = bars[30].end
    prefix = engine.calculate(bars[:31], at)
    full = engine.calculate(bars, at)
    poisoned = [
        b.model_copy(update={"close": b.close * 100, "high": b.high * 100}) if b.start >= at else b
        for b in bars
    ]
    assert prefix == full == engine.calculate(poisoned, at)


def test_completed_bar_only(bars):
    engine = FeatureEngine()
    f = engine.calculate(bars, bars[31].start)
    assert f["price"] == bars[30].close
    assert f["price"] != bars[31].close


def test_delayed_publication(bars):
    delayed = bars[30].model_copy(update={"available_at": bars[30].end + timedelta(minutes=5)})
    f = FeatureEngine().calculate(bars[:30] + [delayed], bars[30].end)
    assert f["price"] == bars[29].close


def test_opening_range_not_ready_early(bars):
    f = FeatureEngine().calculate(bars, bars[13].end)
    assert f["or_15_ready"] == 0
    assert FeatureEngine().calculate(bars, bars[14].end)["or_15_ready"] == 1


def test_resample_only_complete(bars):
    five = resample(bars[:6], 5)
    assert len(five) == 1 and five[0].available_at == bars[4].end
    assert five[0].volume == sum(b.volume for b in bars[:5])
    assert not resample([bars[i] for i in (0, 1, 3, 4, 5)], 5)


def test_quality_duplicate_gap_and_zero(bars):
    q = quality_report([bars[0], bars[0], bars[3].model_copy(update={"volume": 0})])
    assert {"DUPLICATE", "MISSING_BARS", "ZERO_VOLUME"} <= {i["code"] for i in q["issues"]}
    assert not q["interpolated"]


def test_data_roundtrip_and_revision_rejection(store, bars):
    repo = DataRepository(store)
    one = repo.save(bars[:5], {"synthetic": True})
    two = repo.save(bars[:5], {"synthetic": True})
    assert one == two and repo.load(source=bars[0].source) == bars[:5]
    changed = bars[0].model_copy(update={"volume": bars[0].volume + 1})
    with pytest.raises(ValueError):
        repo.save([changed], {})
