import json
import pytest
from aegis.certification import (
    reconcile_fees,
    certify_paper_checks,
    readiness_override,
    derive_paper_evidence,
    CHECKS,
)
from aegis.domain import utcnow
from aegis.evidence import Registry
from aegis.strategies import Baseline


def test_fee_reconciliation_preserves_source(store, tmp_path):
    store.put(
        "trades",
        "t1",
        {"order_id": "o1", "gross_pnl": 12, "net_pnl": 12, "fees": 0, "fees_status": "unreconciled"},
        "PAPER",
    )
    path = tmp_path / "fees.json"
    path.write_text(
        json.dumps(
            {
                "mode": "PAPER",
                "source": "operator-exported broker statement",
                "period": "2025-01-06",
                "fees": [{"order_id": "o1", "total_fees": 0.7}],
            }
        )
    )
    result = reconcile_fees(store, path)
    assert result["trades"] == 1
    assert store.get("t1")["payload"]["net_pnl"] == pytest.approx(11.3)
    assert store.get("t1")["payload"]["fees_status"] == "operator_reconciled"
    assert store.get("t1")["payload"]["fee_statement_hash"] == result["statement_hash"]


def test_bad_fee_statement_is_atomic(store, tmp_path):
    store.put("trades", "t1", {"order_id": "o1", "gross_pnl": 12, "net_pnl": 12, "fees": 0}, "PAPER")
    path = tmp_path / "fees.json"
    path.write_text(
        json.dumps(
            {
                "mode": "PAPER",
                "source": "fixture",
                "period": "2025-01-06",
                "fees": [{"order_id": "o1", "total_fees": 0.7}, {"order_id": "unknown", "total_fees": 2}],
            }
        )
    )
    with pytest.raises(ValueError):
        reconcile_fees(store, path)
    assert store.get("t1")["payload"]["net_pnl"] == 12


def test_operator_certification_requires_attestation(store, tmp_path):
    path = tmp_path / "checks.json"
    path.write_text(
        json.dumps(
            {
                "mode": "PAPER",
                "operator": "test operator",
                "verified_at": utcnow().isoformat(),
                "checks": dict.fromkeys(CHECKS, True),
                "evidence_notes": "Recorded paper lifecycle reviewed",
            }
        )
    )
    with pytest.raises(ValueError):
        certify_paper_checks(store, path, "")
    result = certify_paper_checks(store, path, "I VERIFIED THESE CHECKS ON ALPACA PAPER")
    assert result["report_hash"] and store.control("operational_checks")["paper_auth"]


def test_override_cannot_waive_live_configuration_or_critical_error(store, settings):
    with pytest.raises(ValueError):
        readiness_override(store, "short", 1, "OVERRIDE READINESS AT MY OWN RISK")
    readiness_override(
        store,
        "Operator accepts temporary evidence shortfall for controlled qualification",
        1,
        "OVERRIDE READINESS AT MY OWN RISK",
    )
    store.set_control("critical_error", True)
    result = Registry(store).live_readiness(settings)
    assert result["locked"] and result["override_active"] and result["waived"]
    assert "TRADING_MODE must be LIVE" in result["unmet"]
    assert "Unresolved critical system error" in result["unmet"]


def test_paper_evidence_requires_fees_and_history(store):
    key = Registry(store).register(Baseline("orb"))
    with pytest.raises(ValueError):
        derive_paper_evidence(store, key)
