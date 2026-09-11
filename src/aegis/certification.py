"""Explicit operator certification of real paper records. Never invents broker results."""

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import numpy as np
from aegis.domain import utcnow, stable_id
from aegis.evidence import estimate_ev

CHECKS = ("paper_auth", "data_stream", "reconciliation", "kill_switch", "order_states")


def reconcile_fees(store, statement_path, mode="PAPER"):
    """Consume an operator-supplied exported fee allocation, preserving its source hash."""
    if mode not in {"PAPER", "LIVE"}:
        raise ValueError("Broker record mode required")
    content = Path(statement_path).read_bytes()
    manifest = json.loads(content)
    if manifest.get("mode") != mode or not manifest.get("source") or not manifest.get("period"):
        raise ValueError("Statement mode, source and period required")
    digest = hashlib.sha256(content).hexdigest()
    trades = {r["payload"]["order_id"]: r for r in store.list("trades", mode, 100000)}
    staged = []
    seen = set()
    for fee in manifest["fees"]:
        identifier = fee["order_id"]
        amount = float(fee["total_fees"])
        if identifier in seen or identifier not in trades or not np.isfinite(amount) or amount < 0:
            raise ValueError("Unknown/duplicate order or invalid fee")
        seen.add(identifier)
        trade = trades[identifier]
        p = {
            **trade["payload"],
            "fees": amount,
            "net_pnl": trade["payload"]["gross_pnl"] - amount,
            "fees_status": "operator_reconciled",
            "fee_statement_hash": digest,
            "fee_source": manifest["source"],
        }
        staged.append((trade["id"], p))
    for identifier, payload in staged:
        store.put("trades", identifier, payload, mode)
    store.log(
        "FEES_RECONCILED",
        {"mode": mode, "statement_hash": digest, "source": manifest["source"], "trades": len(staged)},
    )
    return {"trades": len(staged), "statement_hash": digest}


def certify_paper_checks(store, report_path, attestation):
    if attestation != "I VERIFIED THESE CHECKS ON ALPACA PAPER":
        raise ValueError("Exact operator attestation required")
    content = Path(report_path).read_bytes()
    report = json.loads(content)
    if report.get("mode") != "PAPER" or not report.get("operator") or not report.get("evidence_notes"):
        raise ValueError("Paper mode, operator and evidence notes required")
    at = datetime.fromisoformat(report["verified_at"])
    if at.tzinfo is None or not 0 <= (utcnow() - at).total_seconds() <= 86400 * 7:
        raise ValueError("Verification must be aware and from the last seven days")
    if not all(report.get("checks", {}).get(key) is True for key in CHECKS):
        raise ValueError("All operational checks must be explicitly verified")
    if not store.verify_audit()["valid"]:
        raise ValueError("Audit integrity check failed")
    saved = {
        **report["checks"],
        "verified_at": report["verified_at"],
        "operator": report["operator"],
        "report_hash": hashlib.sha256(content).hexdigest(),
        "evidence_notes": report["evidence_notes"],
    }
    store.log("OPERATOR_PAPER_CERTIFICATION", saved)
    store.set_control("operational_checks", saved)
    return saved


def derive_paper_evidence(store, strategy_key, min_days=20, min_trades=100):
    version = store.get(strategy_key)
    if not version or version["kind"] != "strategy_versions":
        raise ValueError("Registered strategy required")
    spec = version["payload"]
    all_trades = [r["payload"] for r in store.list("trades", "PAPER", 100000)]
    trades = [
        t for t in all_trades if t.get("strategy") == spec["name"] and t.get("version") == spec["version"]
    ]
    if len(trades) < min_trades or any(t.get("fees_status") != "operator_reconciled" for t in trades):
        raise ValueError("Enough real paper trades with reconciled fees required")
    days = {t["exit_at"][:10] for t in trades}
    if len(days) < min_days:
        raise ValueError("Insufficient completed paper trading days")
    now = utcnow()
    as_of = max(datetime.fromisoformat(t["exit_at"]) for t in trades) + timedelta(microseconds=1)
    if as_of >= now:
        raise ValueError("Only completed past trades can qualify")
    estimated = estimate_ev(
        trades, as_of, cost_per_share=float(np.mean([t["fees"] / t["qty"] for t in trades]))
    )
    snapshots = sorted(
        [r["payload"] for r in store.list("account_snapshots", "PAPER", 100000)], key=lambda x: x["timestamp"]
    )
    if len(snapshots) < 2:
        raise ValueError("Broker account equity history required")
    equity = np.array([s["equity"] for s in snapshots])
    drawdown = float(np.max((np.maximum.accumulate(equity) - equity) / np.maximum.accumulate(equity)))
    record = {
        "strategy_key": strategy_key,
        "verified": True,
        "verification": "broker_fills_and_operator_reconciled_fees",
        "evaluation": "PAPER",
        "synthetic": False,
        "as_of": as_of.isoformat(),
        "ev": estimated["ev"],
        "ev_lower_bound": estimated["lower_bound"],
        "days": len(days),
        "trade_count": len(trades),
        "max_drawdown": drawdown,
        "statistics": estimated,
        "fee_statement_hashes": sorted({t["fee_statement_hash"] for t in trades}),
    }
    identifier = stable_id("paper-evidence", strategy_key, as_of, record)
    store.put("evidence", identifier, record, "PAPER")
    store.log("PAPER_EVIDENCE_DERIVED", {"id": identifier, **record})
    return identifier


def readiness_override(store, reason, hours, acknowledgment):
    if (
        acknowledgment != "OVERRIDE READINESS AT MY OWN RISK"
        or len(reason.strip()) < 20
        or not 0 < hours <= 24
    ):
        raise ValueError("Explicit acknowledgment, substantive reason and expiry up to 24 hours required")
    record = {
        "reason": reason,
        "created_at": utcnow().isoformat(),
        "expires_at": (utcnow() + timedelta(hours=hours)).isoformat(),
        "scope": "readiness evidence checklist only; mode, credentials, capital, stage, strategy promotion and risk still apply",
    }
    store.log("MANUAL_READINESS_OVERRIDE", record)
    store.set_control("readiness_override", record)
    return record
