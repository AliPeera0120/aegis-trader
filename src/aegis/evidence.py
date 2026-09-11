"""Chronological empirical EV, immutable versions, and evidence-driven promotion."""

from datetime import datetime
import math
import numpy as np
from aegis.domain import stable_id

STAGES = ["IDEA", "BACKTEST", "OUT_OF_SAMPLE", "PAPER_CANDIDATE", "PAPER_VERIFIED", "LIVE_ELIGIBLE"]


def estimate_ev(trades, as_of, min_samples=30, cost_per_share=0):
    eligible = [t for t in trades if datetime.fromisoformat(t["exit_at"]) < as_of]
    if len(eligible) < min_samples:
        return {"eligible": False, "samples": len(eligible), "reason": "INSUFFICIENT_CLOSED_PRIOR_TRADES"}
    values = np.array([t["gross_pnl"] / t["qty"] for t in eligible])
    wins, losses = values[values > 0], values[values <= 0]
    p = len(wins) / len(values)
    avg_win = float(wins.mean()) if len(wins) else 0
    avg_loss = float(-losses.mean()) if len(losses) else 0
    ev = p * avg_win - (1 - p) * avg_loss - cost_per_share
    # Empirical t approximation; independence and stationarity are explicit assumptions.
    from scipy.stats import t as student_t

    lower = ev - float(student_t.ppf(0.975, len(values) - 1)) * float(values.std(ddof=1)) / math.sqrt(
        len(values)
    )
    return {
        "eligible": ev > 0 and lower > 0,
        "samples": len(values),
        "p_win": p,
        "p_loss": 1 - p,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "cost_per_share": cost_per_share,
        "ev": ev,
        "lower_bound": lower,
        "as_of": as_of.isoformat(),
        "assumptions": "Gross P&L/share, supplied costs, independent stationary trades; confidence bounds can be optimistic for clustered trades.",
    }


class Registry:
    def __init__(self, store):
        self.store = store

    def register(self, strategy):
        key = "strategy:" + strategy.name + ":" + strategy.version
        existing = self.store.get(key)
        if not existing:
            self.store.put("strategy_versions", key, strategy.metadata())
            self.store.log("STRATEGY_REGISTERED", strategy.metadata())
        return key

    def stage(self, key):
        return self.store.control("stage:" + key, {"stage": "IDEA", "enabled": False})

    def promote(self, key, target, evidence_ids, actor):
        source = self.stage(key)["stage"]
        if key is None or not self.store.get(key):
            raise ValueError("Registered strategy version required")
        if source == "LIVE_ELIGIBLE" or target != STAGES[STAGES.index(source) + 1]:
            raise ValueError("Promotion cannot skip stages")
        evidence = [self.store.get(i) for i in evidence_ids]
        if not evidence or any(e is None or e["kind"] != "evidence" for e in evidence):
            raise ValueError("Stored evidence required")
        for e in evidence:
            p = e["payload"]
            if p.get("strategy_key") != key or p.get("synthetic") or not p.get("verified"):
                raise ValueError("Real, verified, version-matched evidence required")
        modes = {e["payload"].get("evaluation") for e in evidence}
        required = {
            "BACKTEST": "BACKTEST",
            "OUT_OF_SAMPLE": "OOS",
            "PAPER_CANDIDATE": "OOS",
            "PAPER_VERIFIED": "PAPER",
            "LIVE_ELIGIBLE": "PAPER",
        }[target]
        if required not in modes:
            raise ValueError("Wrong evidence evaluation stage")
        if target in {"PAPER_CANDIDATE", "PAPER_VERIFIED", "LIVE_ELIGIBLE"} and not all(
            e["payload"].get("ev_lower_bound", 0) > 0 for e in evidence
        ):
            raise ValueError("Positive lower-bound expectancy required")
        if target in {"PAPER_VERIFIED", "LIVE_ELIGIBLE"} and not all(
            e["payload"].get("trade_count", 0) >= 100 and e["payload"].get("days", 0) >= 20 for e in evidence
        ):
            raise ValueError("At least 20 paper days and 100 trades required")
        record = {
            "strategy_key": key,
            "from": source,
            "to": target,
            "evidence_ids": evidence_ids,
            "actor": actor,
        }
        self.store.log("STRATEGY_PROMOTED", record)
        self.store.put("promotions", stable_id(key, target), record)
        self.store.set_control(
            "stage:" + key,
            {"stage": target, "enabled": target in {"PAPER_CANDIDATE", "PAPER_VERIFIED", "LIVE_ELIGIBLE"}},
        )
        return record

    def live_readiness(self, settings):
        locks = settings.live_locks()
        verified = [
            r["payload"]
            for r in self.store.list("evidence", limit=10000)
            if r["payload"].get("verified") and not r["payload"].get("synthetic")
        ]
        papers = [
            e
            for e in verified
            if e.get("evaluation") == "PAPER"
            and e.get("days", 0) >= settings.min_paper_days
            and e.get("trade_count", 0) >= settings.min_paper_trades
            and e.get("ev_lower_bound", 0) > 0
            and e.get("max_drawdown", 1) < 0.02
        ]
        oos = [e for e in verified if e.get("evaluation") == "OOS" and e.get("ev_lower_bound", 0) > 0]
        matching_keys = {e["strategy_key"] for e in papers} & {e["strategy_key"] for e in oos}
        if not matching_keys:
            locks.append("Paper and OOS evidence must match the same strategy version")
        if not papers:
            locks.append("Verified paper duration, trade count, expectancy and drawdown evidence missing")
        if not oos:
            locks.append("Positive out-of-sample evidence missing")
        if not any(
            self.stage(r["id"])["stage"] == "LIVE_ELIGIBLE" for r in self.store.list("strategy_versions")
        ):
            locks.append("No strategy version is LIVE_ELIGIBLE")
        checks = self.store.control("operational_checks", {})
        for test in ("paper_auth", "data_stream", "reconciliation", "kill_switch", "order_states"):
            if not checks.get(test):
                locks.append(f"Operational verification missing: {test}")
        if self.store.control("critical_error", False):
            locks.append("Unresolved critical system error")
        override = self.store.control("readiness_override", {})
        from aegis.domain import utcnow

        active = bool(
            override.get("expires_at") and datetime.fromisoformat(override["expires_at"]) > utcnow()
        )
        waived = []
        if active:
            config_locks = settings.live_locks()
            waived = [
                item
                for item in locks
                if item not in config_locks and item != "Unresolved critical system error"
            ]
            locks = [item for item in locks if item not in waived]
        return {
            "locked": bool(locks),
            "unmet": locks,
            "override_active": active,
            "waived": waived,
            "override": override if active else None,
            "guarantees_profitability": False,
        }


def evidence_from_experiment(store, experiment_id, strategy_key, evaluation, minimum_trades=30):
    """Derive evidence exclusively from an immutable stored, nonsynthetic experiment."""
    from datetime import timedelta
    from aegis.domain import utcnow

    record = store.get(experiment_id)
    strategy = store.get(strategy_key)
    if (
        not record
        or record["kind"] != "experiments"
        or not strategy
        or strategy["kind"] != "strategy_versions"
    ):
        raise ValueError("Stored experiment and registered strategy version required")
    result = record["payload"]
    if result.get("synthetic") or evaluation not in {"BACKTEST", "OOS"}:
        raise ValueError("Only real historical BACKTEST or OOS results qualify")
    if evaluation == "OOS":
        if not result.get("folds") or not all(
            f.get("test_evaluations") == 1 and f.get("qualified") for f in result["folds"]
        ):
            raise ValueError("Qualified untouched walk-forward test folds required")
        raw_trades = result["test_trades"]
    else:
        if result.get("fill_model", {}).get("name") not in {"standard", "conservative"}:
            raise ValueError("Optimistic fills cannot provide promotion evidence")
        raw_trades = result.get("trades", [])
    trades = [
        t
        for t in raw_trades
        if t["strategy"] == strategy["payload"]["name"] and t["version"] == strategy["payload"]["version"]
    ]
    if len(trades) < minimum_trades:
        raise ValueError("Insufficient version-matched closed trades")
    at = max(datetime.fromisoformat(t["exit_at"]) for t in trades) + timedelta(microseconds=1)
    if at >= utcnow():
        raise ValueError("Future outcomes cannot qualify as historical evidence")
    costs = float(np.mean([t.get("fees", 0) / t["qty"] for t in trades]))
    estimated = estimate_ev(trades, at, min_samples=minimum_trades, cost_per_share=costs)
    days = len({t["exit_at"][:10] for t in trades})
    payload = {
        "strategy_key": strategy_key,
        "verified": True,
        "verification": "derived_from_immutable_experiment",
        "evaluation": evaluation,
        "synthetic": False,
        "experiment_id": experiment_id,
        "as_of": at.isoformat(),
        "ev": estimated["ev"],
        "ev_lower_bound": estimated["lower_bound"],
        "trade_count": len(trades),
        "days": days,
        "statistics": estimated,
        "max_drawdown": result.get("metrics", {}).get(
            "max_drawdown", max((f["test"]["max_drawdown"] for f in result.get("folds", [])), default=1)
        ),
    }
    identifier = stable_id("evidence", experiment_id, strategy_key, evaluation)
    store.put("evidence", identifier, payload, "RESEARCH")
    store.log("EVIDENCE_DERIVED", {"id": identifier, **payload})
    return identifier
