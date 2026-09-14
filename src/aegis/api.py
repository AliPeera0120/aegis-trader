from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import secrets
import threading
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from aegis.config import Settings
from aegis.domain import utcnow, stable_id
from aegis.store import Store
from aegis.runtime import TradingRuntime
from aegis.backtest import Backtester, FillModel
from aegis.strategies import Baseline, DESCRIPTIONS
from aegis.research import synthetic_bars, persist_experiment
from aegis.data import DataRepository
from aegis.analytics import monte_carlo
from aegis.learning import status as learning_status, permitted


class ResearchRequest(BaseModel):
    strategy: str = "orb"
    parameters: dict = Field(default_factory=dict)
    source: str = "synthetic"
    days: int = Field(default=3, ge=1, le=30)
    seed: int = 7
    fill: str = "all"


class StopRequest(BaseModel):
    flatten: bool = False


class TradeNoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=4000)


class TokenRequest(BaseModel):
    token: str


def recent_stamp(payload, seconds, key="at"):
    try:
        return 0 <= (utcnow() - datetime.fromisoformat(payload[key])).total_seconds() <= seconds
    except (KeyError, TypeError, ValueError):
        return False


def create_app(settings=None, store=None, runtime=None):
    settings = settings or Settings()
    store = store or Store(settings.database_url.get_secret_value())
    runtime = runtime or TradingRuntime(settings, store)
    jobs, job_lock = {}, threading.Lock()
    sessions = {}

    @asynccontextmanager
    async def lifespan(app):
        if settings.service_enabled:
            runtime.start()
        yield
        runtime.stop()

    app = FastAPI(title="Aegis Trader", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.runtime = store, runtime
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts.split(","))

    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        secret = settings.aegis_control_token.get_secret_value()
        authorization = request.headers.get("authorization", "")
        bearer = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        cookie = request.cookies.get("aegis_session", "")
        authenticated = bool(
            secret
            and (
                secrets.compare_digest(bearer, secret)
                or (cookie in sessions and sessions[cookie] > utcnow().timestamp())
            )
        )
        origin = request.headers.get("origin")
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin
            and origin != str(request.base_url).rstrip("/")
        ):
            return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        # With no configured token only local research and read-only status are available.
        if path.startswith("/api/") and path not in {"/api/session", "/api/auth-status"}:
            controls = path.startswith("/api/control/") or path.startswith("/api/promote")
            if (controls or secret) and not authenticated:
                return JSONResponse({"detail": "Operator authentication required"}, status_code=401)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store" if path.startswith("/api") else "no-cache"
        return response

    @app.exception_handler(Exception)
    async def safe_error(request, exc):
        return JSONResponse({"detail": "Operation failed; inspect sanitized audit events"}, status_code=500)

    @app.get("/api/auth-status")
    def auth_status():
        return {"token_configured": bool(settings.aegis_control_token.get_secret_value())}

    @app.post("/api/session")
    def login(body: TokenRequest):
        configured = settings.aegis_control_token.get_secret_value()
        if not configured or not secrets.compare_digest(configured, body.token):
            raise HTTPException(401, "Invalid operator token")
        token = secrets.token_urlsafe(32)
        sessions[token] = utcnow().timestamp() + 3600
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            "aegis_session",
            token,
            httponly=True,
            samesite="strict",
            max_age=3600,
            secure=settings.secure_cookies,
        )
        return response

    @app.get("/healthz")
    def health():
        from sqlalchemy import text

        with store.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        service = store.control("service", {})
        beat = store.control("heartbeat", {})
        fresh = not service.get("running") or (
            beat.get("at") and (utcnow() - datetime.fromisoformat(beat["at"])).total_seconds() < 60
        )
        return JSONResponse(
            {"database": "ok", "service_heartbeat": "ok" if fresh else "stale"},
            status_code=200 if fresh else 503,
        )

    @app.get("/api/status")
    def status():
        mode = settings.trading_mode
        snapshots = store.list("account_snapshots", mode, 1)
        positions = store.get("positions:" + mode)
        health = store.control("health:" + mode, {"broker_connected": False, "reconciled": False})
        if not recent_stamp(health, 60):
            health = {**health, "broker_connected": False, "reconciled": False, "stale": True}
        service = store.control("service", {"running": False})
        heartbeat = store.control("heartbeat", {})
        if service.get("running") and not recent_stamp(heartbeat, 60):
            service = {**service, "running": False, "stale": True}
        service = {**service, "heartbeat_at": heartbeat.get("at")}
        data = store.control("data_health", {"connected": False})
        if not recent_stamp(data, settings.max_quote_age_seconds):
            data = {**data, "connected": False, "stale": True}
        streams = store.control("streams", {})
        if not recent_stamp(streams, 60):
            streams = {
                **streams,
                "market_data_authenticated": False,
                "trade_updates_authenticated": False,
                "stale": True,
            }
        from aegis.data import NY

        baseline = store.control(f"day:{mode}:{utcnow().astimezone(NY).date()}", {})
        position_rows = positions["payload"] if positions else []
        daily_trades = [
            r["payload"]
            for r in store.list("trades", mode, 10000)
            if datetime.fromisoformat(r["payload"]["exit_at"]).astimezone(NY).date()
            == utcnow().astimezone(NY).date()
        ]
        pnl = {
            "day": snapshots[0]["payload"]["equity"] - baseline["equity"]
            if snapshots and baseline.get("equity")
            else None,
            "open": sum(float(p.get("unrealized_pl", 0)) for p in position_rows) if snapshots else None,
            "realized": sum(t["net_pnl"] for t in daily_trades) if snapshots else None,
            "fees_reconciled": all(t.get("fees_status") == "operator_reconciled" for t in daily_trades),
        }
        return {
            "mode": mode,
            "pnl": pnl,
            "account": snapshots[0]["payload"] if snapshots else None,
            "positions": positions["payload"] if positions else [],
            "broker": health,
            "data": data,
            "service": service,
            "critical_error": store.control("critical_error", False),
            "kill_switch": store.control("stop:" + mode, {"stopped": False}),
            "live": runtime.registry.live_readiness(settings),
            "credentials_configured": all(settings.credentials()),
            "operator_configured": bool(settings.aegis_control_token.get_secret_value()),
            "risk_limits": runtime.risk.limits.model_dump(),
            "feed": settings.data_feed,
            "time": utcnow().isoformat(),
            "market_clock": store.control("market_clock", {}),
            "streams": streams,
            "session": store.control("session", {}),
            "paper_learning": learning_status(settings, store),
        }

    @app.get("/api/equity/{mode}")
    def equity_history(mode: str):
        if mode not in {"PAPER", "LIVE"}:
            raise HTTPException(422, "Choose PAPER or LIVE")
        rows = store.list("account_snapshots", mode, 10000)
        return sorted([r["payload"] for r in rows], key=lambda r: r["timestamp"])

    @app.get("/api/strategies")
    def strategies():
        return [
            {
                **s.metadata(),
                **runtime.registry.stage("strategy:" + s.name + ":" + s.version),
                "monitor": store.control("drift:strategy:" + s.name + ":" + s.version, {}),
                "paper_experiment": permitted(settings, store, s.name, s.version),
            }
            for s in runtime.strategies
        ]

    @app.get("/api/records/{kind}")
    def records(kind: str, mode: str | None = None, limit: int = 100):
        allowed = {
            "ranks",
            "trades",
            "experiments",
            "reports",
            "datasets",
            "evidence",
            "risk_decisions",
            "promotions",
            "models",
            "model_versions",
            "market_regimes",
        }
        if kind not in allowed:
            raise HTTPException(404, "Unknown record collection")
        rows = store.list(kind, mode, max(1, min(limit, 1000)))
        if kind == "experiments":
            return [
                {
                    **r,
                    "payload": {
                        k: v
                        for k, v in r["payload"].items()
                        if k not in {"signals", "events", "equity_curve", "trades"}
                    },
                }
                for r in rows
            ]
        return rows

    @app.get("/api/experiments/{identifier}")
    def experiment(identifier: str):
        result = store.get(identifier)
        if not result or result["kind"] != "experiments":
            raise HTTPException(404, "Experiment not found")
        return result["payload"]

    @app.get("/api/comparison")
    def comparison():
        evidence = [r["payload"] for r in store.list("evidence") if not r["payload"].get("synthetic")]
        rows = []
        for strategy in runtime.strategies:
            key = "strategy:" + strategy.name + ":" + strategy.version
            matching = [e for e in evidence if e.get("strategy_key") == key]

            def metric(stage):
                row = next((e for e in matching if e.get("evaluation") == stage), None)
                return row.get("ev") if row else None

            paper = [
                r["payload"]
                for r in store.list("trades", "PAPER", 100000)
                if r["payload"].get("strategy") == strategy.name
                and r["payload"].get("version") == strategy.version
            ]
            import numpy as np

            rows.append(
                {
                    "strategy": strategy.name,
                    "version": strategy.version,
                    "backtest_ev": metric("BACKTEST"),
                    "oos_ev": metric("OOS"),
                    "paper_ev": metric("PAPER"),
                    "paper_slippage": float(np.mean([t.get("slippage", 0) for t in paper]))
                    if paper
                    else None,
                    "max_drawdown": max((e.get("max_drawdown", 0) for e in matching), default=None),
                    "discrepancy": metric("PAPER") is not None
                    and metric("OOS") is not None
                    and abs(metric("PAPER") - metric("OOS")) > abs(metric("OOS")) * 0.5,
                    "units": "EV and slippage in dollars per share; evidence excludes synthetic data",
                }
            )
        return rows

    @app.get("/api/audit")
    def audit():
        return {"verification": store.verify_audit(), "events": store.events(150)}

    @app.post("/api/research")
    def research(body: ResearchRequest):
        if body.strategy not in DESCRIPTIONS or body.fill not in {
            "all",
            "optimistic",
            "standard",
            "conservative",
        }:
            raise HTTPException(422, "Invalid strategy or fill model")
        with job_lock:
            if any(j["status"] == "RUNNING" for j in jobs.values()):
                raise HTTPException(409, "A research experiment is already running")
            identifier = stable_id(utcnow(), body.model_dump())
            jobs[identifier] = {
                "id": identifier,
                "status": "RUNNING",
                "results": [],
                "progress": "Preparing dataset",
            }

        def run():
            try:
                if body.source == "synthetic":
                    bars = synthetic_bars(days=body.days, seed=body.seed)
                    dataset_id = DataRepository(store).save(
                        bars,
                        {
                            "synthetic": True,
                            "source": f"synthetic-{body.seed}",
                            "seed": body.seed,
                            "days": body.days,
                        },
                    )
                else:
                    bars = DataRepository(store).load(source=body.source)
                    if not bars:
                        raise ValueError("No data found; ingest data with the CLI first")
                    dataset_id = None
                models = ["optimistic", "standard", "conservative"] if body.fill == "all" else [body.fill]
                for name in models:
                    jobs[identifier]["progress"] = "Running " + name + " fill model"
                    result = Backtester(Baseline(body.strategy, body.parameters), FillModel.named(name)).run(
                        bars, dataset_id, body.source == "synthetic"
                    )
                    if result["trades"]:
                        result["monte_carlo"] = monte_carlo(result["trades"], runs=500, seed=body.seed)
                    run_id = persist_experiment(
                        store,
                        result,
                        "Evaluate " + body.strategy + " under " + name + " execution assumptions",
                    )
                    jobs[identifier]["results"].append(run_id)
                jobs[identifier]["status"] = "COMPLETE"
                jobs[identifier]["progress"] = "Complete"
            except Exception:
                jobs[identifier]["status"] = "FAILED"
                jobs[identifier]["progress"] = "Research failed; check dataset quality and parameters"
                store.log("RESEARCH_FAILED", {"job": identifier})

        threading.Thread(target=run, daemon=True).start()
        return jobs[identifier]

    @app.get("/api/jobs/{identifier}")
    def job(identifier: str):
        if identifier not in jobs:
            raise HTTPException(404, "Job not found; completed experiments persist in the database")
        return jobs[identifier]

    @app.post("/api/control/trades/{identifier}/note")
    def trade_note(identifier: str, body: TradeNoteRequest):
        record = store.get(identifier)
        if not record or record["kind"] != "trades":
            raise HTTPException(404, "Trade not found")
        payload = {
            "trade_id": identifier,
            "note": body.note.strip(),
            "at": utcnow().isoformat(),
            "author": "authenticated-operator",
        }
        note_id = stable_id("trade-note", identifier, payload["at"])
        store.put("trade_notes", note_id, payload, record["mode"])
        store.log("OPERATOR_TRADE_NOTE", payload)
        return {"saved": True, "note_id": note_id}

    @app.get("/api/trades/{identifier}/notes")
    def trade_notes(identifier: str):
        return [
            r["payload"]
            for r in store.list("trade_notes", limit=10000)
            if r["payload"]["trade_id"] == identifier
        ]

    @app.post("/api/control/connect")
    def connect():
        return runtime.connect()

    @app.post("/api/control/start")
    def start():
        try:
            return runtime.start()
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/api/control/stop")
    def stop(body: StopRequest):
        # Persist the latch before contacting any broker, including when credentials are absent.
        store.set_control(
            "stop:" + settings.trading_mode,
            {"stopped": True, "reason": "OPERATOR_STOP", "at": utcnow().isoformat()},
        )
        if runtime.execution:
            return runtime.execution.emergency_stop(flatten=body.flatten)
        store.log(
            "KILL_SWITCH_ACTIVATED",
            {"mode": settings.trading_mode, "broker_connected": False, "flatten_requested": body.flatten},
        )
        return {"stopped": True, "broker_action": "unavailable", "flatten_confirmed": False}

    @app.post("/api/control/resume")
    def resume():
        if not runtime.execution:
            raise HTTPException(409, "Connect and reconcile the broker first")
        try:
            return runtime.execution.resume()
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/api/control/approve/{candidate_id}")
    def approve(candidate_id: str):
        result = store.get(candidate_id)
        if not result or result["kind"] != "candidates" or not runtime.execution:
            raise HTTPException(404, "Candidate or execution connection unavailable")
        from aegis.domain import Candidate

        candidate = Candidate.model_validate(result["payload"])
        quote = runtime.data.latest_quotes([candidate.symbol]).get(candidate.symbol)
        return runtime.execution.submit(candidate, quote, manual_approval=True)

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
