from concurrent.futures import ThreadPoolExecutor
import time
import pytest
from fastapi.testclient import TestClient
from aegis.api import create_app
from aegis.store import audit
from aegis.config import Settings
from aegis.evidence import Registry
from aegis.strategies import Baseline


def test_hash_chain_concurrent_and_tamper(store):
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda i: store.log("TEST", {"i": i}), range(20)))
    assert store.verify_audit()["valid"] and store.verify_audit()["count"] == 20
    with store.transaction() as c:
        c.execute(audit.update().where(audit.c.sequence == 1).values(payload={"modified": True}))
    assert not store.verify_audit()["valid"]


def test_audit_rejects_secret_fields(store):
    with pytest.raises(ValueError):
        store.log("BAD", {"nested": {"api_key": "not-logged"}})


def test_no_synthetic_promotion(store):
    registry = Registry(store)
    key = registry.register(Baseline("orb"))
    store.put(
        "evidence",
        "synthetic",
        {"verified": True, "synthetic": True, "strategy_key": key, "evaluation": "BACKTEST"},
    )
    with pytest.raises(ValueError):
        registry.promote(key, "BACKTEST", ["synthetic"], "test")


def test_no_promotion_skip(store):
    registry = Registry(store)
    key = registry.register(Baseline("orb"))
    with pytest.raises(ValueError):
        registry.promote(key, "LIVE_ELIGIBLE", [], "test")


def test_api_empty_truthful_and_controls_locked(store, settings):
    with TestClient(create_app(settings, store)) as client:
        status = client.get("/api/status")
        assert status.status_code == 200
        value = status.json()
        assert value["account"] is None and value["live"]["locked"]
        assert value["mode"] == "PAPER"
        assert "alpaca_paper_secret" not in status.text
        assert client.post("/api/control/stop", json={}).status_code == 401
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert "frame-ancestors" in client.get("/").headers["content-security-policy"]


def test_api_auth_csrf_host_stop(store, tmp_path):
    token = "a" * 48
    settings = Settings(
        _env_file=None, aegis_control_token=token, database_url="sqlite:///:memory:", runtime_dir=tmp_path
    )
    with TestClient(create_app(settings, store)) as client:
        assert client.get("/api/status").status_code == 401
        assert client.post("/api/session", json={"token": "wrong"}).status_code == 401
        assert client.post("/api/session", json={"token": token}).status_code == 200
        assert client.get("/api/status").status_code == 200
        assert (
            client.post("/api/control/stop", headers={"Origin": "https://evil.example"}, json={}).status_code
            == 403
        )
        assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
        assert client.post("/api/control/stop", json={}).json()["stopped"]
        assert store.control("stop:PAPER")["stopped"]


def test_research_api_job_and_persistence(store, settings):
    with TestClient(create_app(settings, store)) as client:
        response = client.post(
            "/api/research", json={"strategy": "orb", "days": 1, "fill": "standard", "seed": 51}
        )
        assert response.status_code == 200
        job = response.json()
        for _ in range(300):
            job = client.get("/api/jobs/" + job["id"]).json()
            if job["status"] != "RUNNING":
                break
            time.sleep(0.05)
        assert job["status"] == "COMPLETE"
        result = client.get("/api/experiments/" + job["results"][0]).json()
        assert result["synthetic"] and "metrics" in result
        assert len(client.get("/api/records/experiments").json()) == 1


def test_postgresql_migration_compiles_offline(monkeypatch):
    import io
    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://fixture:placeholder@localhost/aegis")
    output = io.StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE orders" in sql and "INSERT INTO controls" in sql and "COMMIT" in sql


def test_manual_trade_note_requires_auth_and_is_audited(store, tmp_path):
    token = "n" * 48
    settings = Settings(
        _env_file=None, aegis_control_token=token, database_url="sqlite:///:memory:", runtime_dir=tmp_path
    )
    store.put("trades", "trade-record", {"order_id": "paper-order"}, "PAPER")
    with TestClient(create_app(settings, store)) as client:
        assert (
            client.post(
                "/api/control/trades/trade-record/note", json={"note": "Reviewed execution"}
            ).status_code
            == 401
        )
        client.post("/api/session", json={"token": token})
        assert client.post(
            "/api/control/trades/trade-record/note", json={"note": "Reviewed execution"}
        ).json()["saved"]
        assert client.get("/api/trades/trade-record/notes").json()[0]["note"] == "Reviewed execution"
        assert store.events()[0]["event"] == "OPERATOR_TRADE_NOTE"
