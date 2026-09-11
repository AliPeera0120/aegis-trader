"""Deterministic risk controls; no strategy has authority to bypass them."""

from dataclasses import dataclass, field
from datetime import datetime
import math
from pydantic import Field
from aegis.domain import Model, Quote, Candidate, require_utc


class RiskLimits(Model):
    max_position_dollars: float = Field(default=1000, gt=0)
    max_allocation_pct: float = Field(default=0.05, gt=0, le=1)
    max_trade_risk_pct: float = Field(default=0.0025, gt=0, le=0.02)
    max_open_risk_pct: float = Field(default=0.01, gt=0, le=0.1)
    max_positions: int = Field(default=5, ge=1)
    max_sector_pct: float = Field(default=0.15, gt=0, le=1)
    max_correlated_pct: float = Field(default=0.25, gt=0, le=1)
    max_trades_day: int = Field(default=10, ge=1)
    max_losses_day: int = Field(default=5, ge=1)
    max_daily_drawdown: float = Field(default=0.02, gt=0, le=0.2)
    max_weekly_drawdown: float = Field(default=0.04, gt=0, le=0.3)
    max_strategy_drawdown: float = Field(default=0.04, gt=0, le=0.3)
    max_consecutive_losses: int = Field(default=3, ge=1)
    max_spread_pct: float = Field(default=0.002, gt=0)
    min_dollar_volume: float = Field(default=100000, ge=0)
    min_ev: float = Field(default=0, ge=0)
    max_quote_age: float = Field(default=5, gt=0)
    max_signal_age: float = Field(default=90, gt=0)
    min_price: float = Field(default=5, gt=0)
    max_price: float = Field(default=2000, gt=0)
    sizing: str = "fixed_fractional"
    fixed_dollars: float = Field(default=500, gt=0)
    allow_shorts: bool = False
    slippage_buffer_pct: float = Field(default=0.001, ge=0)


@dataclass
class Portfolio:
    equity: float
    buying_power: float
    day_start_equity: float
    week_start_equity: float
    positions: list[dict] = field(default_factory=list)
    reservations: list[dict] = field(default_factory=list)
    trades_today: int = 0
    losses_today: int = 0
    consecutive_losses: int = 0
    strategy_drawdown: float = 0
    paused_until: datetime | None = None
    reconciled: bool = False
    broker_connected: bool = False
    data_connected: bool = False
    market_open: bool = False
    clock_at: datetime | None = None
    kill_switch: bool = False
    account_blocked: bool = False
    strategy_eligible: bool = False
    asset_tradable: bool = False
    asset_shortable: bool = False
    drift_blocked: bool = False

    @property
    def exposure(self):
        return self.positions + self.reservations


class RiskDecision(Model):
    accepted: bool
    qty: int = 0
    reasons: list[str]
    entry_limit: float = 0
    notional: float = 0
    risk_dollars: float = 0


class RiskEngine:
    def __init__(self, limits=None):
        self.limits = limits or RiskLimits()

    def evaluate(
        self,
        candidate: Candidate,
        quote: Quote | None,
        portfolio: Portfolio,
        now,
        live_capital=None,
        live_order_cap=None,
        research=False,
    ):
        require_utc(now)
        p, c, limits = portfolio, candidate, self.limits
        reject = []
        values = [p.equity, p.buying_power, p.day_start_equity, p.week_start_equity, p.strategy_drawdown]
        if any(not math.isfinite(v) for v in values) or min(values[:4]) <= 0:
            return RiskDecision(accepted=False, reasons=["INVALID_ACCOUNT_OR_BUYING_POWER"])
        if p.kill_switch:
            reject.append("KILL_SWITCH")
        if p.account_blocked:
            reject.append("ACCOUNT_BLOCKED")
        if not p.reconciled or not p.broker_connected or not p.data_connected:
            reject.append("UNHEALTHY_OR_UNRECONCILED")
        if not p.market_open:
            reject.append("MARKET_CLOSED")
        if not p.clock_at or not 0 <= (now - p.clock_at).total_seconds() <= 30:
            reject.append("STALE_OR_FUTURE_CLOCK")
        if not 0 <= (now - c.timestamp).total_seconds() <= limits.max_signal_age:
            reject.append("STALE_OR_FUTURE_SIGNAL")
        if (p.day_start_equity - p.equity) / p.day_start_equity >= limits.max_daily_drawdown:
            reject.append("DAILY_LOSS_LIMIT")
        if (p.week_start_equity - p.equity) / p.week_start_equity >= limits.max_weekly_drawdown:
            reject.append("WEEKLY_LOSS_LIMIT")
        if p.strategy_drawdown >= limits.max_strategy_drawdown:
            reject.append("STRATEGY_DRAWDOWN")
        if p.trades_today >= limits.max_trades_day:
            reject.append("MAX_TRADES")
        if p.losses_today >= limits.max_losses_day:
            reject.append("MAX_LOSSES")
        if p.consecutive_losses >= limits.max_consecutive_losses or (p.paused_until and p.paused_until > now):
            reject.append("LOSS_COOLDOWN")
        if len(p.exposure) >= limits.max_positions:
            reject.append("MAX_POSITIONS")
        if any(x["symbol"] == c.symbol for x in p.exposure):
            reject.append("NO_AVERAGING_OR_DUPLICATE_EXPOSURE")
        if not p.asset_tradable:
            reject.append("ASSET_NOT_TRADABLE")
        if c.side == "sell" and not (limits.allow_shorts and p.asset_shortable):
            reject.append("SHORT_NOT_AUTHORIZED")
        if not limits.min_price <= c.entry <= limits.max_price:
            reject.append("PRICE_RANGE")
        if c.features.get("dollar_volume", 0) < limits.min_dollar_volume:
            reject.append("LIQUIDITY")
        if not research:
            if not p.strategy_eligible or p.drift_blocked:
                reject.append("STRATEGY_NOT_ELIGIBLE")
            if (
                c.expected_value is None
                or c.ev_lower_bound is None
                or not c.evidence_id
                or c.expected_value <= limits.min_ev
                or c.ev_lower_bound <= limits.min_ev
            ):
                reject.append("INSUFFICIENT_POSITIVE_EV_EVIDENCE")
        if quote is None:
            reject.append("MISSING_QUOTE")
        elif (
            quote.symbol != c.symbol
            or not 0 <= (now - quote.timestamp).total_seconds() <= limits.max_quote_age
        ):
            reject.append("STALE_FUTURE_OR_WRONG_QUOTE")
        elif quote.spread_pct > limits.max_spread_pct:
            reject.append("SPREAD")
        if reject:
            return RiskDecision(accepted=False, reasons=reject)
        capital = min(p.equity, live_capital) if live_capital is not None else p.equity
        # Limit is a strict price ceiling/floor; price rounding is adverse to the strategy.
        limit = (
            math.ceil(max(c.entry, quote.ask) * (1 + limits.slippage_buffer_pct) * 100) / 100
            if c.side == "buy"
            else math.floor(min(c.entry, quote.bid) * (1 - limits.slippage_buffer_pct) * 100) / 100
        )
        if not (c.stop < limit < c.target if c.side == "buy" else c.target < limit < c.stop):
            return RiskDecision(accepted=False, reasons=["BRACKET_INVALID_AT_CURRENT_QUOTE"])
        per_share = abs(limit - c.stop)
        exposure = p.exposure
        total_notional = sum(abs(x["notional"]) for x in exposure)
        total_risk = sum(x.get("risk_dollars", abs(x["notional"])) for x in exposure)
        sector = sum(
            abs(x["notional"]) for x in exposure if x.get("sector", "unknown") in (c.sector, "unknown")
        )
        correlated = sum(
            abs(x["notional"])
            for x in exposure
            if x.get("correlation_group", "US_EQUITIES") == c.correlation_group
        )
        reserved = sum(abs(x["notional"]) for x in p.reservations)
        caps = {
            "requested": c.requested_qty,
            "position": limits.max_position_dollars / limit,
            "allocation": capital * limits.max_allocation_pct / limit,
            "buying_power": max(0, p.buying_power - reserved) / limit,
            "trade_risk": capital * limits.max_trade_risk_pct / per_share,
            "open_risk": max(0, capital * limits.max_open_risk_pct - total_risk) / per_share,
            "sector": max(0, capital * limits.max_sector_pct - sector) / limit,
            "correlation": max(0, capital * limits.max_correlated_pct - correlated) / limit,
            "capital": max(0, capital - total_notional) / limit,
        }
        if live_order_cap is not None:
            caps["live_order_cap"] = live_order_cap / limit
        if limits.sizing == "fixed_dollar":
            caps["fixed_dollar"] = limits.fixed_dollars / limit
        elif limits.sizing == "volatility_adjusted":
            caps["volatility"] = (
                capital * limits.max_trade_risk_pct / max(c.features.get("atr", per_share) * 2, per_share)
            )
        elif limits.sizing != "fixed_fractional":
            return RiskDecision(accepted=False, reasons=["INVALID_SIZING_METHOD"])
        binding = min(caps, key=caps.get)
        qty = max(0, math.floor(caps[binding]))
        return RiskDecision(
            accepted=qty > 0,
            qty=qty,
            reasons=[f"CAP_{binding.upper()}" if qty else "NO_RISK_CAPACITY"],
            entry_limit=limit,
            notional=qty * limit,
            risk_dollars=qty * per_share,
        )
