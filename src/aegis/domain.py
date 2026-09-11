from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from hashlib import sha256
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow():
    return datetime.now(timezone.utc)


def require_utc(value: datetime):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid", hide_input_in_errors=True)


class Bar(Model):
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,14}$")
    start: datetime
    end: datetime
    available_at: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    vwap: float | None = Field(default=None, gt=0)
    timeframe: str = "1Min"
    source: str = "unknown"
    adjustment: str = "raw"

    @field_validator("start", "end", "available_at")
    @classmethod
    def timestamps(cls, value):
        return require_utc(value)

    @model_validator(mode="after")
    def validate_bar(self):
        if self.end <= self.start or self.available_at < self.end:
            raise ValueError("A bar cannot be available before completion")
        if (
            self.low > min(self.open, self.close)
            or self.high < max(self.open, self.close)
            or self.low > self.high
        ):
            raise ValueError("Impossible OHLC relationship")
        return self


class Quote(Model):
    symbol: str
    timestamp: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    bid_size: float = Field(default=0, ge=0)
    ask_size: float = Field(default=0, ge=0)

    @field_validator("timestamp")
    @classmethod
    def timestamp_utc(cls, value):
        return require_utc(value)

    @model_validator(mode="after")
    def validate_spread(self):
        if self.ask < self.bid:
            raise ValueError("Crossed quote")
        return self

    @property
    def spread_pct(self):
        return (self.ask - self.bid) / ((self.ask + self.bid) / 2)


class Candidate(Model):
    id: str
    symbol: str
    strategy: str
    version: str
    timestamp: datetime
    side: Literal["buy", "sell"] = "buy"
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float = Field(gt=0)
    requested_qty: int = Field(default=100000, gt=0)
    order_type: Literal["market", "limit", "stop"] = "limit"
    score: float = 0
    features: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    regime: str = "unknown"
    sector: str = "unknown"
    correlation_group: str = "US_EQUITIES"
    model_version: str | None = None
    expected_value: float | None = None
    ev_lower_bound: float | None = None
    evidence_id: str | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_utc(cls, value):
        return require_utc(value)

    @model_validator(mode="after")
    def bracket(self):
        valid = (
            self.stop < self.entry < self.target
            if self.side == "buy"
            else self.target < self.entry < self.stop
        )
        if not valid:
            raise ValueError("Invalid protective bracket")
        return self


class OrderState(StrEnum):
    PROPOSED = "PROPOSED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


TRANSITIONS = {
    "PROPOSED": {"RISK_APPROVED", "REJECTED"},
    "RISK_APPROVED": {"SUBMITTED", "REJECTED", "ERROR"},
    "SUBMITTED": {"ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "ERROR"},
    "ACKNOWLEDGED": {"PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "ERROR"},
    "PARTIALLY_FILLED": {"FILLED", "CANCELED", "EXIT_PENDING", "ERROR"},
    "FILLED": {"EXIT_PENDING", "CLOSED"},
    "CANCELED": {"PARTIALLY_FILLED", "FILLED", "EXIT_PENDING", "CLOSED"},
    "EXIT_PENDING": {"CLOSED", "ERROR"},
    "ERROR": {"ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXIT_PENDING", "CLOSED"},
    "REJECTED": set(),
    "CLOSED": set(),
}


def transition(current: str, target: str):
    if current == target:
        return current
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid order transition: {current} -> {target}")
    return target


def stable_id(*parts: Any):
    return sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]
