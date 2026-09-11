"""Pure timestamp-safe indicators. Strategies see only completed, available bars."""

from datetime import timedelta
import math
import numpy as np
from aegis.data import NY, Calendar
from aegis.domain import require_utc


class FeatureEngine:
    def __init__(self, calendar=None):
        self.calendar = calendar or Calendar()

    def calculate(self, bars, at, benchmark=None, quote=None):
        require_utc(at)
        history = sorted((b for b in bars if b.end <= at and b.available_at <= at), key=lambda b: b.start)
        if not history:
            return None
        if len({b.symbol for b in history}) != 1 or any(b.timeframe != "1Min" for b in history):
            raise ValueError("Feature pipeline requires one symbol of minute bars")
        day = at.astimezone(NY).date()
        today = [b for b in history if b.start.astimezone(NY).date() == day]
        if not today:
            return None
        last = today[-1]
        closes = np.array([b.close for b in history])
        returns = np.diff(np.log(closes[-31:]))
        previous = [b for b in history if b.start.astimezone(NY).date() < day]
        session = self.calendar.session(at)
        if session is None:
            return None
        volume = sum(b.volume for b in today)
        vwap = (
            sum((b.vwap or (b.high + b.low + b.close) / 3) * b.volume for b in today) / volume
            if volume
            else last.close
        )
        prev_vwap = (
            (
                sum((b.vwap or (b.high + b.low + b.close) / 3) * b.volume for b in today[:-1])
                / sum(b.volume for b in today[:-1])
            )
            if len(today) > 1 and sum(b.volume for b in today[:-1])
            else vwap
        )
        prev_close = closes[-2] if len(closes) > 1 else last.close
        true_ranges = []
        for i, b in enumerate(history[-15:]):
            prior = history[-15:][i - 1].close if i else b.open
            true_ranges.append(max(b.high - b.low, abs(b.high - prior), abs(b.low - prior)))
        # RVOL denominator uses previous sessions' corresponding minute; fallback is labeled intraday rolling.
        matched = [
            b.volume for b in previous if b.start.astimezone(NY).time() == last.start.astimezone(NY).time()
        ]
        baseline = matched[-20:] or [b.volume for b in today[-21:-1]]
        avg_volume = float(np.mean(baseline)) if baseline else 0
        features = {
            "price": last.close,
            "previous_close": float(prev_close),
            "volume": last.volume,
            "rolling_volume": sum(b.volume for b in today[-5:]),
            "relative_volume": last.volume / avg_volume if avg_volume else 0,
            "rvol_historical": float(bool(matched)),
            "dollar_volume": last.close * last.volume,
            "volume_acceleration": last.volume / today[-2].volume - 1
            if len(today) > 1 and today[-2].volume
            else 0,
            "vwap": vwap,
            "vwap_distance": last.close / vwap - 1,
            "vwap_slope": vwap / prev_vwap - 1,
            "vwap_reclaim": float(prev_close <= prev_vwap and last.close > vwap),
            "vwap_rejection": float(prev_close >= prev_vwap and last.close < vwap),
            "atr": float(np.mean(true_ranges[-14:])),
            "realized_volatility": float(np.std(returns)) if len(returns) > 1 else 0,
            "distance_open": last.close / today[0].open - 1,
            "distance_high": last.close / max(b.high for b in today) - 1,
            "distance_low": last.close / min(b.low for b in today) - 1,
            "intraday_range": max(b.high for b in today) / min(b.low for b in today) - 1,
            "gap": today[0].open / previous[-1].close - 1 if previous else 0,
            "gap_known": float(bool(previous)),
            "minutes_open": (at - session[0]).total_seconds() / 60,
            "data_age_seconds": (at - last.end).total_seconds(),
            "trend_persistence": float(np.mean(returns > 0)) if len(returns) else 0,
            "prior_high": max(b.high for b in today[-16:-1]) if len(today) > 1 else last.high,
            "prior_low": min(b.low for b in today[-16:-1]) if len(today) > 1 else last.low,
        }
        for n in (1, 5, 15, 30):
            past = [b for b in today if b.end <= last.end - timedelta(minutes=n)]
            features[f"return_{n}m"] = last.close / past[-1].close - 1 if past else 0
        features["acceleration"] = features["return_1m"] - features["return_5m"] / 5
        for n in (5, 15, 30):
            opening = [b for b in today if b.start < session[0] + timedelta(minutes=n)]
            complete = {b.start for b in opening} == {session[0] + timedelta(minutes=i) for i in range(n)}
            features[f"or_{n}_ready"] = float(complete and at >= session[0] + timedelta(minutes=n))
            features[f"or_{n}_high"] = max((b.high for b in opening), default=last.high)
            features[f"or_{n}_low"] = min((b.low for b in opening), default=last.low)
        features["relative_strength"] = 0
        if benchmark:
            bf = self.calculate(benchmark, at)
            if bf:
                features["relative_strength"] = features["return_5m"] - bf["return_5m"]
                features["market_return"] = bf["distance_open"]
        if quote:
            if quote.timestamp > at:
                raise ValueError("Future quote cannot be used")
            features["spread_pct"] = quote.spread_pct
            total = quote.bid_size + quote.ask_size
            features["quote_imbalance"] = (quote.bid_size - quote.ask_size) / total if total else 0
        if not all(math.isfinite(v) for v in features.values()):
            raise ValueError("Nonfinite feature")
        return features


def regime(features):
    if not features:
        return "unknown"
    vol, ret = features["realized_volatility"], features["distance_open"]
    if abs(features.get("gap", 0)) > 0.08 or vol > 0.015:
        return "dislocated"
    if ret < -0.015 and vol > 0.003:
        return "risk_off"
    if vol > 0.004:
        return "elevated_volatility"
    if vol < 0.0002:
        return "low_volatility"
    if ret > 0.003:
        return "bullish_trend"
    if ret < -0.003:
        return "bearish_trend"
    return "range_bound"
