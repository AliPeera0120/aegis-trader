"""Relational records, transactional execution state, append-only hash-chained audit."""

import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import (
    JSON,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    select,
    update,
    event,
)
from sqlalchemy.pool import StaticPool
from aegis.domain import utcnow

metadata = MetaData()
records = Table(
    "records",
    metadata,
    Column("id", String(96), primary_key=True),
    Column("kind", String(40), index=True, nullable=False),
    Column("mode", String(20), index=True, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("payload", JSON, nullable=False),
)
orders = Table(
    "orders",
    metadata,
    Column("id", String(96), primary_key=True),
    Column("candidate_id", String(96), unique=True, nullable=False),
    Column("mode", String(20), nullable=False),
    Column("state", String(32), nullable=False),
    Column("broker_id", String(96)),
    Column("payload", JSON, nullable=False),
)
audit = Table(
    "audit_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", String(40), nullable=False),
    Column("event", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("previous_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
)
controls = Table(
    "controls", metadata, Column("key", String(80), primary_key=True), Column("payload", JSON, nullable=False)
)
bars_table = Table(
    "bars",
    metadata,
    Column("id", String(96), primary_key=True),
    Column("symbol", String(20), index=True),
    Column("start", String(40), index=True),
    Column("timeframe", String(16)),
    Column("source", String(24)),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("symbol", "start", "timeframe", "source"),
)
# Research/operational entities share records' versioned payload schema; order and bar hot paths are normalized.
KINDS = {
    "instruments",
    "quotes",
    "strategies",
    "strategy_versions",
    "models",
    "model_versions",
    "signals",
    "candidates",
    "risk_decisions",
    "fills",
    "positions",
    "trades",
    "sessions",
    "account_snapshots",
    "performance",
    "market_regimes",
    "system_events",
    "experiments",
    "datasets",
    "promotions",
    "reports",
    "evidence",
    "ranks",
    "quality",
    "trade_notes",
}


def clean(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return json.loads(json.dumps(value, default=str, allow_nan=False))


class Store:
    def __init__(self, url="sqlite:///var/aegis.db", initialize=True):
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs = (
            {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
        )
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def pragmas(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")

        self.lock = threading.RLock()
        if initialize:
            metadata.create_all(self.engine)
            with self.engine.begin() as c:
                if not c.execute(select(controls).where(controls.c.key == "audit_lock")).first():
                    c.execute(controls.insert().values(key="audit_lock", payload={"n": 0}))

    @contextmanager
    def transaction(self):
        with self.lock, self.engine.begin() as conn:
            yield conn

    def put(self, kind, identifier, payload, mode="RESEARCH"):
        if kind not in KINDS:
            raise ValueError("Unknown record kind")
        data = dict(
            id=identifier, kind=kind, mode=mode, created_at=utcnow().isoformat(), payload=clean(payload)
        )
        with self.transaction() as c:
            old = c.execute(select(records).where(records.c.id == identifier)).first()
            if old:
                if old.kind != kind or old.mode != mode:
                    raise ValueError("Record identity collision")
                if kind in {"strategy_versions", "model_versions", "evidence", "experiments", "promotions"}:
                    if old.payload != data["payload"]:
                        raise ValueError("Immutable versioned record cannot be overwritten")
                else:
                    c.execute(
                        update(records).where(records.c.id == identifier).values(payload=data["payload"])
                    )
            else:
                c.execute(records.insert().values(**data))
        return data

    def get(self, identifier):
        with self.engine.connect() as c:
            row = c.execute(select(records).where(records.c.id == identifier)).mappings().first()
            return dict(row) if row else None

    def list(self, kind, mode=None, limit=500):
        stmt = select(records).where(records.c.kind == kind)
        if mode:
            stmt = stmt.where(records.c.mode == mode)
        with self.engine.connect() as c:
            return [
                dict(r) for r in c.execute(stmt.order_by(records.c.created_at.desc()).limit(limit)).mappings()
            ]

    def control(self, key, default=None):
        with self.engine.connect() as c:
            row = c.execute(select(controls.c.payload).where(controls.c.key == key)).first()
            return row[0] if row else default

    def set_control(self, key, payload):
        with self.transaction() as c:
            old = c.execute(select(controls.c.key).where(controls.c.key == key)).first()
            if old:
                c.execute(controls.update().where(controls.c.key == key).values(payload=clean(payload)))
            else:
                c.execute(controls.insert().values(key=key, payload=clean(payload)))

    def log(self, event_name, payload):
        payload = clean(payload)

        # Never accept secrets in audit payloads, even from accidentally supplied config objects.
        def reject_secrets(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if any(
                        s in k.lower()
                        for s in ("secret", "password", "api_key", "authorization", "control_token")
                    ):
                        raise ValueError("Sensitive audit field rejected")
                    reject_secrets(v)
            elif isinstance(obj, list):
                for v in obj:
                    reject_secrets(v)

        reject_secrets(payload)
        with self.transaction() as c:
            # Database row lock serializes the hash chain across PostgreSQL writers.
            c.execute(update(controls).where(controls.c.key == "audit_lock").values(payload={"n": 0}))
            prev = (
                c.execute(select(audit.c.hash).order_by(audit.c.sequence.desc()).limit(1)).scalar()
                or "0" * 64
            )
            timestamp = utcnow().isoformat()
            digest = self._digest(prev, timestamp, event_name, payload)
            c.execute(
                audit.insert().values(
                    timestamp=timestamp, event=event_name, payload=payload, previous_hash=prev, hash=digest
                )
            )
        return digest

    @staticmethod
    def _digest(previous, timestamp, event_name, payload):
        return hashlib.sha256(
            json.dumps(
                [previous, timestamp, event_name, payload], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    def verify_audit(self):
        previous, count = "0" * 64, 0
        with self.engine.connect() as c:
            for r in c.execute(select(audit).order_by(audit.c.sequence)).mappings():
                if r["previous_hash"] != previous or r["hash"] != self._digest(
                    previous, r["timestamp"], r["event"], r["payload"]
                ):
                    return {"valid": False, "at": r["sequence"]}
                previous, count = r["hash"], count + 1
        return {"valid": True, "count": count, "head": previous}

    def events(self, limit=100):
        with self.engine.connect() as c:
            return [
                dict(r)
                for r in c.execute(select(audit).order_by(audit.c.sequence.desc()).limit(limit)).mappings()
            ]
