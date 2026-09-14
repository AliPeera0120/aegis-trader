"""Exchange-session aware ingestion. Raw responses are retained before normalization."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip
import json
import math
from zoneinfo import ZoneInfo
import exchange_calendars as xcals
import pandas as pd
from sqlalchemy import select
from aegis.domain import Bar, Quote, stable_id, require_utc, utcnow
from aegis.store import bars_table

NY = ZoneInfo("America/New_York")


class Calendar:
    def __init__(self, name="XNYS"):
        self.calendar = xcals.get_calendar(name, start="2000-01-01", end="2035-12-31")

    def session(self, value):
        date = value.astimezone(NY).date() if isinstance(value, datetime) else value
        day = pd.Timestamp(str(date))
        if not self.calendar.is_session(day):
            return None
        return self.calendar.session_open(day).to_pydatetime(), self.calendar.session_close(
            day
        ).to_pydatetime()

    def is_open(self, value):
        value = require_utc(value)
        session = self.session(value)
        return bool(session and session[0] <= value < session[1])

    def sessions(self, start, end):
        for day in self.calendar.sessions_in_range(str(start), str(end)):
            yield self.session(day.date())


def quality_report(bars, calendar=None):
    calendar = calendar or Calendar()
    issues, seen, previous = [], set(), {}
    for b in sorted(bars, key=lambda x: (x.symbol, x.start)):
        key = b.symbol, b.start, b.timeframe
        if key in seen:
            issues.append({"code": "DUPLICATE", "symbol": b.symbol, "at": b.start.isoformat()})
        seen.add(key)
        if b.volume <= 0:
            issues.append({"code": "ZERO_VOLUME", "symbol": b.symbol, "at": b.start.isoformat()})
        last = previous.get(b.symbol)
        if last and abs(math.log(b.open / last.close)) > 0.35:
            issues.append(
                {"code": "OUTLIER_OR_CORPORATE_ACTION", "symbol": b.symbol, "at": b.start.isoformat()}
            )
        if last and last.end < b.start and last.start.astimezone(NY).date() == b.start.astimezone(NY).date():
            issues.append(
                {
                    "code": "MISSING_BARS",
                    "symbol": b.symbol,
                    "from": last.end.isoformat(),
                    "to": b.start.isoformat(),
                }
            )
        previous[b.symbol] = b
        if b.timeframe != "1Day" and not calendar.is_open(b.start):
            issues.append({"code": "OUTSIDE_REGULAR_SESSION", "symbol": b.symbol, "at": b.start.isoformat()})
    return {
        "bars": len(bars),
        "issues": issues,
        "interpolated": False,
        "corporate_actions_verified": False,
        "point_in_time_universe": False,
    }


def resample(bars, minutes=5):
    if minutes not in (5, 15):
        raise ValueError("Supported aggregation: 5 or 15 minutes")
    buckets = {}
    cal = Calendar()
    for b in bars:
        if b.timeframe != "1Min":
            raise ValueError("Aggregation requires minute bars")
        session = cal.session(b.start)
        if not session or not session[0] <= b.start < session[1]:
            continue
        index = int((b.start - session[0]).total_seconds() // (60 * minutes))
        buckets.setdefault((b.symbol, session[0] + timedelta(minutes=index * minutes)), []).append(b)
    result = []
    for (symbol, start), group in sorted(buckets.items()):
        group.sort(key=lambda b: b.start)
        expected = {start + timedelta(minutes=i) for i in range(minutes)}
        if len(group) != minutes or {b.start for b in group} != expected:
            continue  # Incomplete bars are not fabricated; upstream quality reports retain the gap.
        volume = sum(b.volume for b in group)
        result.append(
            Bar(
                symbol=symbol,
                start=start,
                end=group[-1].end,
                available_at=max(b.available_at for b in group),
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                volume=volume,
                vwap=sum((b.vwap or b.close) * b.volume for b in group) / volume if volume else None,
                timeframe=f"{minutes}Min",
                source=group[0].source,
                adjustment=group[0].adjustment,
            )
        )
    return result


class DataRepository:
    def __init__(self, store, raw_dir=Path("var/raw")):
        self.store, self.raw_dir = store, Path(raw_dir)

    def save(self, bars, manifest):
        payload = [b.model_dump(mode="json") for b in bars]
        import hashlib

        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.store.transaction() as c:
            for b, p in zip(bars, payload):
                identifier = stable_id(b.symbol, b.start, b.timeframe, b.source)
                if not c.execute(select(bars_table.c.id).where(bars_table.c.id == identifier)).first():
                    c.execute(
                        bars_table.insert().values(
                            id=identifier,
                            symbol=b.symbol,
                            start=b.start.isoformat(),
                            timeframe=b.timeframe,
                            source=b.source,
                            payload=p,
                        )
                    )
                else:
                    old = c.execute(
                        select(bars_table.c.payload).where(bars_table.c.id == identifier)
                    ).scalar_one()
                    if old != p:
                        raise ValueError("Historical revision needs a distinct dataset source version")
        self.store.put(
            "datasets",
            fingerprint,
            {**manifest, "hash": fingerprint, "count": len(bars), "quality": quality_report(bars)},
        )
        return fingerprint

    def load(self, symbols=None, source=None, timeframe="1Min"):
        stmt = select(bars_table.c.payload).where(bars_table.c.timeframe == timeframe)
        if symbols:
            stmt = stmt.where(bars_table.c.symbol.in_(symbols))
        if source:
            stmt = stmt.where(bars_table.c.source == source)
        with self.store.engine.connect() as c:
            return [Bar.model_validate(r[0]) for r in c.execute(stmt.order_by(bars_table.c.start))]


class AlpacaData:
    def __init__(self, settings, repository):
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.enums import DataFeed

        key, secret = settings.data_credentials()
        if not key or not secret:
            raise RuntimeError("Market-data credentials are not configured")
        self.client = StockHistoricalDataClient(key, secret)
        self.feed = DataFeed(settings.data_feed)
        self.repository, self.settings = repository, settings

    def history(self, symbols, start, end, timeframe="1Min"):
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import Adjustment

        require_utc(start)
        require_utc(end)
        durations = {"1Min": 1, "5Min": 5, "15Min": 15, "1Day": 1440}
        if timeframe not in durations or end <= start:
            raise ValueError("Invalid history request")
        tf = TimeFrame.Day if timeframe == "1Day" else TimeFrame(durations[timeframe], TimeFrameUnit.Minute)
        try:
            response = self.client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=symbols,
                    start=start,
                    end=end,
                    timeframe=tf,
                    feed=self.feed,
                    adjustment=Adjustment.RAW,
                )
            )
        except Exception:
            raise RuntimeError(
                "Historical market-data request failed; check credentials and feed entitlement"
            ) from None
        self.repository.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.repository.raw_dir / f"{stable_id(symbols, start, end, utcnow())}.json.gz"
        with gzip.open(path, "wt") as file:
            json.dump(response.model_dump(mode="json"), file, default=str)
        bars, cal = [], Calendar()
        for symbol, rows in response.data.items():
            for row in rows:
                opened = row.timestamp.astimezone(timezone.utc)
                if timeframe == "1Day":
                    session = cal.session(opened.astimezone(NY).date())
                    if not session:
                        continue
                    opened, closed = session
                else:
                    closed = opened + timedelta(minutes=durations[timeframe])
                    if not cal.is_open(opened):
                        continue
                if closed > min(end, utcnow()):
                    continue  # Incomplete daily/minute bars would change identity on the next download.
                bars.append(
                    Bar(
                        symbol=symbol,
                        start=opened,
                        end=closed,
                        available_at=closed,
                        open=row.open,
                        high=row.high,
                        low=row.low,
                        close=row.close,
                        volume=row.volume,
                        vwap=row.vwap,
                        timeframe=timeframe,
                        source=f"alpaca-{self.settings.data_feed}",
                    )
                )
        self.repository.save(
            bars,
            {
                "source": f"alpaca-{self.settings.data_feed}",
                "synthetic": False,
                "symbols": symbols,
                "start": start,
                "end": end,
                "raw_file": str(path),
                "adjustment": "raw",
                "availability_assumption": "Historical bars available at bar end; revisions not point-in-time",
            },
        )
        return bars

    def latest_quotes(self, symbols):
        from alpaca.data.requests import StockLatestQuoteRequest

        try:
            data = self.client.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=symbols, feed=self.feed)
            )
        except Exception:
            raise RuntimeError("Quote request failed") from None
        quotes = {}
        for s, q in data.items():
            try:
                quotes[s] = Quote(
                    symbol=s,
                    timestamp=q.timestamp,
                    bid=q.bid_price,
                    ask=q.ask_price,
                    bid_size=q.bid_size,
                    ask_size=q.ask_size,
                )
            except ValueError:
                self.repository.store.log(
                    "INVALID_REST_QUOTE", {"symbol": s, "feed": self.settings.data_feed}
                )
        return quotes

    def stream(self, symbols, on_bar, on_quote, on_trade):
        from alpaca.data.live import StockDataStream
        from aegis.tls import websocket_params

        key, secret = self.settings.data_credentials()
        stream = StockDataStream(key, secret, feed=self.feed, websocket_params=websocket_params())
        stream.subscribe_bars(on_bar, *symbols)
        stream.subscribe_quotes(on_quote, *symbols)
        stream.subscribe_trades(on_trade, *symbols)
        return stream


def select_universe(instruments, config):
    accepted, rejected = [], []
    for item in instruments:
        reasons = []
        rules = {
            "PRICE": config["min_price"] <= item.get("price", 0) <= config["max_price"],
            "LIQUIDITY": item.get("adv", 0) >= config["min_adv"]
            and item.get("dollar_volume", 0) >= config["min_dollar_volume"],
            "SPREAD": item.get("spread_pct", 1) <= config["max_spread_pct"],
            "EXCHANGE": item.get("exchange") in config["exchanges"],
            "VOLATILITY": config["min_volatility"] <= item.get("volatility", -1) <= config["max_volatility"],
            "SHORTABILITY": not config["require_shortable"] or item.get("shortable", False),
            "MARKET_CAP": not config["min_market_cap"]
            or item.get("market_cap", 0) >= config["min_market_cap"],
            "TRADABLE": item.get("tradable", False) and item.get("active", False),
        }
        reasons = [k for k, valid in rules.items() if not valid]
        (rejected if reasons else accepted).append({**item, "reasons": reasons})
    return accepted, rejected
