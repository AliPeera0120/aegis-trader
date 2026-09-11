"""Point-in-time metadata interfaces. Absence of a feed is explicit, never inferred away."""

from datetime import datetime
from typing import Literal
from aegis.domain import Model, require_utc
from pydantic import field_validator


class Membership(Model):
    universe_version: str
    symbol: str
    effective_from: datetime
    effective_to: datetime | None = None
    known_at: datetime
    source: str

    @field_validator("effective_from", "known_at", "effective_to")
    @classmethod
    def aware(cls, value):
        return require_utc(value) if value is not None else None


def historical_universe(memberships, at, version):
    require_utc(at)
    return sorted(
        {
            m.symbol
            for m in memberships
            if m.universe_version == version
            and m.known_at <= at
            and m.effective_from <= at
            and (m.effective_to is None or at < m.effective_to)
        }
    )


class CorporateEvent(Model):
    symbol: str
    event_type: Literal["split", "symbol_change", "delisting", "halt"]
    published_at: datetime
    effective_at: datetime
    source: str
    details: dict

    @field_validator("published_at", "effective_at")
    @classmethod
    def aware(cls, value):
        return require_utc(value)


def corporate_flags(events, symbol, start, end, as_of):
    return [
        e.model_dump(mode="json")
        for e in events
        if e.symbol == symbol and e.published_at <= as_of and start <= e.effective_at <= end
    ]
