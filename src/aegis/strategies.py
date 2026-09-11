"""Research hypotheses, not claims of profitability. No broker imports or handles."""

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from aegis.domain import Candidate, stable_id


@lru_cache(maxsize=1)
def source_hash():
    return stable_id(Path(__file__).read_text())


class Strategy(Protocol):
    def generate_signal(self, symbol, at, features, market_regime="unknown"): ...
    def calculate_entry(self, features): ...
    def calculate_invalidation(self, features): ...
    def calculate_exit(self, features): ...
    def metadata(self): ...
    def required_features(self): ...


@dataclass
class Baseline:
    name: str = "orb"
    params: dict = field(default_factory=dict)

    @property
    def version(self):
        import json

        return stable_id(source_hash(), self.name, json.dumps(self.params, sort_keys=True))[:12]

    def metadata(self):
        return {
            "name": self.name,
            "version": self.version,
            "parameters": self.params,
            "status": "IDEA",
            "enabled": False,
            "research_only": True,
            "description": DESCRIPTIONS[self.name],
        }

    def required_features(self):
        return ["price", "atr", "relative_volume", "vwap_distance", "return_5m", "minutes_open"]

    def calculate_entry(self, f):
        return f["price"]

    def calculate_invalidation(self, f):
        distance = max(f["atr"] * self.params.get("stop_atr", 1.5), f["price"] * 0.003)
        if self.params.get("stop_type") == "percentage":
            distance = f["price"] * self.params.get("stop_pct", 0.005)
        if self.params.get("stop_type") == "technical":
            return min(f["prior_low"], f["price"] * 0.997)
        return max(0.01, f["price"] - distance)

    def calculate_exit(self, f):
        return f["price"] + (f["price"] - self.calculate_invalidation(f)) * self.params.get("reward_risk", 2)

    def generate_signal(self, symbol, at, features, market_regime="unknown"):
        f = features
        if not f or f["minutes_open"] < 5 or f["data_age_seconds"] > 90:
            return None
        p = self.params
        reasons, condition = [], False
        if self.name == "orb":
            n = p.get("opening_minutes", 15)
            if n not in (5, 15, 30):
                raise ValueError("Opening range must be 5, 15 or 30 minutes")
            condition = bool(
                f[f"or_{n}_ready"]
                and f["price"] > f[f"or_{n}_high"]
                and f["previous_close"] <= f[f"or_{n}_high"]
                and f["vwap_distance"] > 0
                and f["relative_volume"] >= p.get("min_rvol", 1.2)
            )
            reasons = [f"Closed above completed {n}-minute opening range", f"RVOL {f['relative_volume']:.3f}"]
        elif self.name == "vwap_continuation":
            condition = (
                f["vwap_distance"] > 0
                and f["vwap_slope"] > 0
                and f["return_5m"] > p.get("min_momentum", 0.002)
            )
            reasons = ["Above rising VWAP", f"5-minute return {f['return_5m']:.5f}"]
        elif self.name == "vwap_reclaim":
            condition = bool(f["vwap_reclaim"] and f["relative_volume"] >= p.get("min_rvol", 1))
            reasons = ["Completed close reclaimed VWAP", f"VWAP {f['vwap']:.4f}"]
        elif self.name == "vwap_reversion":
            condition = (
                f["vwap_distance"] < -p.get("distance", 0.005)
                and f["return_1m"] > 0
                and market_regime == "range_bound"
            )
            reasons = ["Below VWAP with positive completed-bar return in range regime"]
        elif self.name == "momentum":
            condition = (
                f["relative_volume"] >= p.get("min_rvol", 1.5)
                and f["acceleration"] > 0
                and f["price"] > f["prior_high"]
                and f["relative_strength"] >= p.get("min_relative_strength", 0)
            )
            reasons = [
                "Completed close above prior range",
                f"RVOL {f['relative_volume']:.3f}",
                f"Relative strength {f['relative_strength']:.5f}",
            ]
        elif self.name == "gap_continuation":
            condition = bool(
                f["gap_known"]
                and f["gap"] > p.get("min_gap", 0.005)
                and f["return_5m"] > 0.002
                and f["minutes_open"] < 60
            )
            reasons = [f"Opening gap {f['gap']:.5f}", "Positive first-hour momentum"]
        elif self.name == "gap_fade":
            condition = bool(
                f["gap_known"]
                and f["gap"] < -p.get("min_gap", 0.005)
                and f["return_5m"] > 0.002
                and f["minutes_open"] < 60
            )
            reasons = [f"Negative opening gap {f['gap']:.5f}", "Recovery toward prior close"]
        else:
            raise ValueError("Unknown strategy")
        if not condition:
            return None
        entry, stop, target = self.calculate_entry(f), self.calculate_invalidation(f), self.calculate_exit(f)
        if not stop < entry < target:
            return None
        return Candidate(
            id=stable_id(symbol, at, self.name, self.version),
            symbol=symbol,
            strategy=self.name,
            version=self.version,
            timestamp=at,
            entry=entry,
            stop=stop,
            target=target,
            score=min(100, 30 + f["relative_volume"] * 10 + max(0, f["return_5m"]) * 1000),
            features=f,
            reasons=reasons,
            regime=market_regime,
        )


DESCRIPTIONS = {
    "orb": "Completed-bar breakout above a finished 5, 15 or 30 minute opening range.",
    "vwap_continuation": "Continuation above rising session VWAP with positive momentum.",
    "vwap_reclaim": "A completed close crosses back above session VWAP.",
    "vwap_reversion": "Recovery from below VWAP in a contemporaneous range regime.",
    "momentum": "Relative-volume acceleration and a completed-bar range breakout.",
    "gap_continuation": "Positive opening gap followed by first-hour continuation.",
    "gap_fade": "Negative opening gap followed by first-hour recovery.",
}


def baselines():
    return [Baseline(name) for name in DESCRIPTIONS]
