"""Explicit paper experiments and chronological training; no promotion or broker access."""

from datetime import datetime
import math
from aegis.data import NY
from aegis.domain import stable_id, utcnow
from aegis.ml import experiment

FEATURES = ["relative_volume", "vwap_distance", "return_5m", "realized_volatility", "minutes_open"]


def enabled_keys(settings):
    if settings.trading_mode != "PAPER" or not settings.paper_learning_enabled:
        return set()
    return {k.strip() for k in settings.paper_learning_strategies.split(",") if k.strip()}


def permitted(settings, store, strategy, version):
    key = "strategy:" + strategy + ":" + version
    record = store.get(key)
    return key in enabled_keys(settings) and bool(record and record["kind"] == "strategy_versions")


def status(settings, store):
    keys = enabled_keys(settings)
    trades = [r["payload"] for r in store.list("trades", "PAPER", 100000)]
    matching = [
        t
        for t in trades
        if t.get("execution_purpose") == "PAPER_EXPERIMENT"
        and "strategy:" + t["strategy"] + ":" + t["version"] in keys
    ]
    return {
        "enabled": bool(keys),
        "strategy_keys": sorted(keys),
        "purpose": "PAPER_EXPERIMENT",
        "max_shares_per_order": 1,
        "max_order_notional": settings.paper_learning_max_order_notional,
        "capital_limit": settings.paper_learning_capital,
        "daily_loss_limit": settings.paper_learning_daily_loss,
        "closed_trades": len(matching),
        "min_trades_per_strategy": settings.paper_learning_min_trades,
        "min_days_per_strategy": settings.paper_learning_min_days,
        "training": store.control("paper_training", {"status": "COLLECTING", "strategies": {}}),
        "live_eligible": False,
    }


def training_rows(trades, now):
    rows = []
    for t in trades:
        features = t.get("features", {})
        if not all(n in features and math.isfinite(features[n]) for n in FEATURES):
            continue
        start = t.get("signal_at")
        end = t.get("exit_at")
        if not start or not end or not datetime.fromisoformat(start) < datetime.fromisoformat(end) < now:
            continue
        # Label a closed, broker-observed round trip, with an explicit extra cost buffer.
        # Raw broker P&L remains unchanged and fees remain marked unreconciled.
        per_share = t["gross_pnl"] / t["qty"] - 0.01 - t["entry"] * 0.0004
        rows.append(
            {
                "timestamp": start,
                "label_end": end,
                "symbol": t["symbol"],
                "features": {n: features[n] for n in FEATURES},
                "target": int(per_share > 0),
                "future_return": per_share / t["entry"],
                "order_id": t["order_id"],
            }
        )
    return sorted(rows, key=lambda r: r["timestamp"])


def train_closed_outcomes(settings, store, now=None):
    """Run once per session. Insufficient/class-imbalanced samples are a normal collecting state."""
    now = now or utcnow()
    results = {}
    for key in sorted(enabled_keys(settings)):
        trades = [
            r["payload"]
            for r in store.list("trades", "PAPER", 100000)
            if r["payload"].get("execution_purpose") == "PAPER_EXPERIMENT"
            and "strategy:" + r["payload"]["strategy"] + ":" + r["payload"]["version"] == key
        ]
        rows = training_rows(trades, now)
        days = len({datetime.fromisoformat(r["timestamp"]).astimezone(NY).date() for r in rows})
        result = {"status": "COLLECTING", "samples": len(rows), "days": days}
        results[key] = result
        if len(rows) < settings.paper_learning_min_trades or days < settings.paper_learning_min_days:
            continue
        identifier = stable_id("paper-training-v1", key, rows)
        if store.get(identifier):
            result.update(status="TRAINED_RESEARCH_ONLY", model_id=identifier)
            continue
        try:
            _, metrics = experiment(rows, FEATURES, ablation=False)
        except ValueError:
            result.update(
                status="COLLECTING", reason="More temporally separated outcomes in both classes needed"
            )
            continue
        payload = {
            "strategy_key": key,
            "at": now,
            "training_data_id": identifier,
            "metrics": metrics,
            "execution_enabled": False,
            "synthetic": False,
            "source": "Alpaca paper closed trades",
            "order_ids": [r["order_id"] for r in rows],
            "label_costs": "Observed gross/share minus $0.01 and 4 bps of entry; fees unreconciled",
            "limitations": [
                "Selected paper trades only; IEX coverage and simulated fills",
                "Repeated temporal tests are research diagnostics, not fresh promotion evidence",
                "No automatic model deployment or real-money eligibility",
            ],
        }
        store.put("model_versions", identifier, payload, "PAPER")
        store.log("PAPER_RESEARCH_MODEL_TRAINED", {"id": identifier, "strategy_key": key})
        result.update(status="TRAINED_RESEARCH_ONLY", model_id=identifier)
    report = {"status": "REVIEWED" if results else "DISABLED", "at": now.isoformat(), "strategies": results}
    store.set_control("paper_training", report)
    return report
