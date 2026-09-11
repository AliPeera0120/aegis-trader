"""Deterministic bar-event simulator. All entry orders execute after their signal event."""

from dataclasses import dataclass
from datetime import timedelta
from collections import defaultdict
import hashlib
import json
import math
from aegis.data import Calendar, NY, quality_report
from aegis.domain import Quote
from aegis.features import FeatureEngine, regime
from aegis.risk import RiskEngine, Portfolio


@dataclass(frozen=True)
class FillModel:
    name: str = "standard"
    spread_bps: float = 2
    slippage_bps: float = 2
    fee_per_share: float = 0.005
    participation: float = 0.01
    latency_seconds: float = 1

    @classmethod
    def named(cls, name):
        return {
            "optimistic": cls("optimistic", 0, 0, 0, 0.1, 0),
            "standard": cls(),
            "conservative": cls("conservative", 8, 8, 0.01, 0.005, 2),
        }[name]

    def __post_init__(self):
        if (
            not all(
                math.isfinite(x) and x >= 0
                for x in (self.spread_bps, self.slippage_bps, self.fee_per_share, self.latency_seconds)
            )
            or not 0 < self.participation <= 1
        ):
            raise ValueError("Invalid fill assumptions")

    def price(self, reference, side):
        sign = 1 if side == "buy" else -1
        return reference * (1 + sign * (self.spread_bps / 2 + self.slippage_bps) / 10000)

    def entry(self, order, bar):
        c = order["candidate"]
        if bar.start < c.timestamp + timedelta(seconds=self.latency_seconds) or bar.volume <= 0:
            return None
        side = c.side
        limit = order["decision"].entry_limit
        reference = bar.open
        if c.order_type == "limit":
            # Touch fills only; no queue-priority claim. Adverse fill adjustment must also fit limit.
            reference = min(bar.open, limit) if side == "buy" else max(bar.open, limit)
            if (side == "buy" and bar.low > limit) or (side == "sell" and bar.high < limit):
                return None
            price = self.price(reference, side)
            if (side == "buy" and price > limit) or (side == "sell" and price < limit):
                return None
        elif c.order_type == "stop":
            if (side == "buy" and bar.high < c.entry) or (side == "sell" and bar.low > c.entry):
                return None
            reference = max(bar.open, c.entry) if side == "buy" else min(bar.open, c.entry)
            price = self.price(reference, side)
        else:
            price = self.price(reference, side)
        # Even research market fills cannot exceed reserved entry risk without another approval.
        if (side == "buy" and price > limit) or (side == "sell" and price < limit):
            return None
        qty = min(order["remaining"], math.floor(bar.volume * self.participation))
        return (price, qty, abs(price - reference), reference * self.spread_bps / 20000) if qty > 0 else None


@dataclass
class BacktestConfig:
    initial_equity: float = 100000
    max_hold_minutes: int = 45
    order_ttl_minutes: int = 3
    flatten_minutes_before_close: int = 5
    trailing_atr: float | None = None
    seed: int = 7

    def __post_init__(self):
        if (
            not math.isfinite(self.initial_equity)
            or self.initial_equity <= 0
            or min(self.max_hold_minutes, self.order_ttl_minutes, self.flatten_minutes_before_close) <= 0
        ):
            raise ValueError("Invalid backtest configuration")


class Backtester:
    def __init__(self, strategy, fill=None, risk=None, config=None):
        self.strategy, self.fill, self.risk, self.config = (
            strategy,
            fill or FillModel(),
            risk or RiskEngine(),
            config or BacktestConfig(),
        )
        self.features, self.calendar = FeatureEngine(), Calendar()

    def run(self, bars, dataset_id=None, synthetic=False):
        bars = sorted(bars, key=lambda b: (b.start, b.symbol))
        if not bars or any(b.timeframe != "1Min" for b in bars):
            raise ValueError("Backtest requires minute bars")
        if len({(b.symbol, b.start) for b in bars}) != len(bars):
            raise ValueError("Duplicate market events")
        sources = {(b.source, b.adjustment) for b in bars}
        if len(sources) > 1:
            raise ValueError("Mixed data sources or adjustment policies")
        quality = quality_report(bars, self.calendar)
        if any(
            i["code"] in {"OUTLIER_OR_CORPORATE_ACTION", "OUTSIDE_REGULAR_SESSION"} for i in quality["issues"]
        ):
            raise ValueError("Quarantined outlier or non-session data; resolve quality before backtest")
        cfg, fill = self.config, self.fill
        history, groups = defaultdict(list), defaultdict(list)
        for b in bars:
            groups[b.start].append(b)
        pending, positions, trades, signals, events, curve = {}, {}, [], [], [], []
        realized, equity = cfg.initial_equity, cfg.initial_equity
        day, week, day_start, week_start = None, None, equity, equity
        trades_day, losses_day, consecutive = 0, 0, 0
        prices = {}

        def close(symbol, reference, at, reason):
            nonlocal realized, losses_day, consecutive
            p = positions.pop(symbol)
            c = p["candidate"]
            exit_side = "sell" if c.side == "buy" else "buy"
            price = fill.price(reference, exit_side)
            if reason == "TARGET":
                # A resting profit-target limit never fills through its limit adversely.
                price = max(price, c.target) if c.side == "buy" else min(price, c.target)
            sign = 1 if c.side == "buy" else -1
            gross = (price - p["entry"]) * p["qty"] * sign
            fees = p["entry_fees"] + p["qty"] * fill.fee_per_share
            net = gross - fees
            realized += net
            losses_day += net < 0
            consecutive = consecutive + 1 if net < 0 else 0
            trade = {
                "symbol": symbol,
                "strategy": c.strategy,
                "version": c.version,
                "sector": c.sector,
                "regime": c.regime,
                "side": c.side,
                "qty": p["qty"],
                "entry": p["entry"],
                "exit": price,
                "entry_at": p["entry_at"].isoformat(),
                "exit_at": at.isoformat(),
                "gross_pnl": gross,
                "net_pnl": net,
                "fees": fees,
                "exit_reason": reason,
                "holding_minutes": (at - p["entry_at"]).total_seconds() / 60,
                "slippage_cost": p["slippage"] + p["qty"] * reference * fill.slippage_bps / 10000,
                "spread_cost": p["spread"] + p["qty"] * reference * fill.spread_bps / 20000,
                "signal_id": c.id,
                "features": c.features,
                "model_version": c.model_version,
                "synthetic": synthetic,
            }
            trades.append(trade)
            pending.pop(symbol, None)
            events.append({"at": at.isoformat(), "event": "CLOSED", "symbol": symbol, "reason": reason})

        for at, batch in sorted(groups.items()):
            decision_at = min(b.end for b in batch)
            current_day = at.astimezone(NY).date()
            current_week = current_day - timedelta(days=current_day.weekday())
            if current_day != day:
                day, day_start, trades_day, losses_day, consecutive = current_day, equity, 0, 0, 0
                pending.clear()
            if current_week != week:
                week, week_start = current_week, equity
            session = self.calendar.session(at)
            for b in batch:
                prices[b.symbol] = b.close
                if b.symbol in pending:
                    o = pending[b.symbol]
                    if at - o["candidate"].timestamp > timedelta(minutes=cfg.order_ttl_minutes):
                        pending.pop(b.symbol)
                        events.append({"at": at.isoformat(), "event": "ORDER_EXPIRED", "symbol": b.symbol})
                    elif session and at < session[1] - timedelta(minutes=cfg.flatten_minutes_before_close):
                        result = fill.entry(o, b)
                        if result:
                            price, qty, adverse, spread = result
                            c = o["candidate"]
                            p = positions.get(b.symbol)
                            if not p:
                                p = {
                                    "candidate": c,
                                    "qty": 0,
                                    "entry": 0,
                                    "entry_at": b.end,
                                    "entry_fees": 0,
                                    "stop": c.stop,
                                    "slippage": 0,
                                    "spread": 0,
                                }
                                positions[b.symbol] = p
                            p["entry"] = (p["entry"] * p["qty"] + price * qty) / (p["qty"] + qty)
                            p["qty"] += qty
                            p["entry_fees"] += qty * fill.fee_per_share
                            p["slippage"] += qty * max(0, adverse - spread)
                            p["spread"] += qty * spread
                            o["remaining"] -= qty
                            events.append(
                                {
                                    "at": b.end.isoformat(),
                                    "event": "PARTIAL_FILL" if o["remaining"] else "FILL",
                                    "symbol": b.symbol,
                                    "qty": qty,
                                    "price": price,
                                }
                            )
                            if not o["remaining"]:
                                pending.pop(b.symbol)
                if b.symbol in positions:
                    p, c = positions[b.symbol], positions[b.symbol]["candidate"]
                    if at >= session[1] - timedelta(minutes=cfg.flatten_minutes_before_close):
                        close(b.symbol, b.open, at, "END_OF_DAY")
                    elif at - p["entry_at"] >= timedelta(minutes=cfg.max_hold_minutes):
                        close(b.symbol, b.open, at, "TIME_EXIT")
                    else:
                        stop_hit = b.low <= p["stop"] if c.side == "buy" else b.high >= p["stop"]
                        target_hit = b.high >= c.target if c.side == "buy" else b.low <= c.target
                        # If both are touched, stop wins. Gap-through stops execute at the adverse open.
                        if stop_hit:
                            ref = min(b.open, p["stop"]) if c.side == "buy" else max(b.open, p["stop"])
                            close(b.symbol, ref, b.end, "STOP")
                        elif target_hit and p["entry_at"] < b.end:
                            close(b.symbol, c.target, b.end, "TARGET")
                        elif cfg.trailing_atr:
                            if c.side == "buy":
                                p["stop"] = max(p["stop"], b.close - cfg.trailing_atr * c.features["atr"])
                            else:
                                p["stop"] = min(p["stop"], b.close + cfg.trailing_atr * c.features["atr"])
                history[b.symbol].append(b)
            equity = realized + sum(
                (prices[s] - p["entry"]) * p["qty"] * (1 if p["candidate"].side == "buy" else -1)
                - p["entry_fees"]
                for s, p in positions.items()
            )
            gross_exposure = sum(prices[s] * p["qty"] for s, p in positions.items())
            curve.append({"timestamp": decision_at.isoformat(), "equity": equity, "exposure": gross_exposure})
            daily_killed = (day_start - equity) / day_start >= self.risk.limits.max_daily_drawdown
            if daily_killed:
                pending.clear()
            benchmark = history.get("SPY", [])
            market = regime(self.features.calculate(benchmark, decision_at)) if benchmark else "unknown"
            if decision_at >= session[1] - timedelta(minutes=cfg.flatten_minutes_before_close + 1):
                continue
            ranked = []
            for b in batch:
                f = self.features.calculate(
                    history[b.symbol], decision_at, benchmark if b.symbol != "SPY" else None
                )
                c = self.strategy.generate_signal(b.symbol, decision_at, f, market)
                if c:
                    ranked.append(c)
            for c in sorted(ranked, key=lambda c: (-c.score, c.symbol)):
                quote = Quote(
                    symbol=c.symbol,
                    timestamp=decision_at,
                    bid=c.entry * (1 - fill.spread_bps / 20000),
                    ask=c.entry * (1 + fill.spread_bps / 20000),
                )
                exp = [
                    {
                        "symbol": s,
                        "notional": prices[s] * p["qty"],
                        "risk_dollars": abs(prices[s] - p["stop"]) * p["qty"],
                        "sector": p["candidate"].sector,
                        "correlation_group": p["candidate"].correlation_group,
                    }
                    for s, p in positions.items()
                ]
                reservations = [
                    {
                        "symbol": s,
                        "notional": o["remaining"] * o["decision"].entry_limit,
                        "risk_dollars": o["remaining"] * abs(o["decision"].entry_limit - o["candidate"].stop),
                        "sector": o["candidate"].sector,
                    }
                    for s, o in pending.items()
                ]
                port = Portfolio(
                    equity=equity,
                    buying_power=max(0, equity - gross_exposure),
                    day_start_equity=day_start,
                    week_start_equity=week_start,
                    positions=exp,
                    reservations=reservations,
                    trades_today=trades_day,
                    losses_today=losses_day,
                    consecutive_losses=consecutive,
                    reconciled=True,
                    broker_connected=True,
                    data_connected=True,
                    market_open=True,
                    clock_at=decision_at,
                    asset_tradable=True,
                    kill_switch=daily_killed,
                )
                decision = self.risk.evaluate(c, quote, port, decision_at, research=True)
                signals.append(
                    {"candidate": c.model_dump(mode="json"), "risk": decision.model_dump(mode="json")}
                )
                if decision.accepted:
                    pending[c.symbol] = {"candidate": c, "decision": decision, "remaining": decision.qty}
                    trades_day += 1
        fingerprint = (
            dataset_id
            or hashlib.sha256(
                json.dumps([b.model_dump(mode="json") for b in bars], sort_keys=True).encode()
            ).hexdigest()
        )
        from aegis.analytics import performance

        result = {
            "strategy": self.strategy.metadata(),
            "dataset_id": fingerprint,
            "synthetic": synthetic,
            "initial_equity": cfg.initial_equity,
            "fill_model": vars(fill),
            "config": vars(cfg),
            "risk_limits": self.risk.limits.model_dump(),
            "start": bars[0].start.isoformat(),
            "end": bars[-1].end.isoformat(),
            "trades": trades,
            "signals": signals,
            "events": events,
            "equity_curve": curve,
            "open_positions": [
                {"symbol": s, "qty": p["qty"], "entry": p["entry"]} for s, p in positions.items()
            ],
            "quality": quality,
            "metrics": performance(trades, curve, cfg.initial_equity),
            "assumptions": [
                "Bar-derived spread; no tick queue model",
                "Entry fill at a later bar after latency",
                "Stop-first ambiguity; targets disabled in entry bar",
                "Protective stop liquidity assumed; entries participation-capped",
                "Dataset-end positions remain marked, not fabricated closed",
                "Historical availability may omit vendor revisions",
                "Current watchlist; survivorship bias not eliminated",
                "Research evaluates hypotheses before requiring deployment EV evidence",
            ],
        }
        result["id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        return result


def replay(bars, strategy, speed=60):
    """Yield the same pure strategy decisions using only observed history; caller controls pacing."""
    if speed <= 0:
        raise ValueError("Replay speed must be positive")
    engine, history = FeatureEngine(), defaultdict(list)
    for b in sorted(bars, key=lambda b: (b.available_at, b.symbol)):
        history[b.symbol].append(b)
        at = b.available_at
        f = engine.calculate(history[b.symbol], at)
        c = strategy.generate_signal(b.symbol, at, f, regime(f))
        yield {
            "at": at.isoformat(),
            "symbol": b.symbol,
            "features": f,
            "candidate": c.model_dump(mode="json") if c else None,
            "delay_seconds": 60 / speed,
        }
