"""Durable, serialized order ownership, broker reconciliation and fail-closed submission."""

from datetime import datetime, timedelta
import threading
from sqlalchemy import select
from aegis.broker import ApprovedOrder
from aegis.data import NY
from aegis.domain import stable_id, transition, utcnow
from aegis.risk import Portfolio, RiskEngine
from aegis.store import orders, clean
from aegis.learning import permitted

ACTIVE = {"RISK_APPROVED", "SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "ERROR"}
STATUS = {
    "new": "ACKNOWLEDGED",
    "accepted": "ACKNOWLEDGED",
    "pending_new": "ACKNOWLEDGED",
    "partially_filled": "PARTIALLY_FILLED",
    "filled": "FILLED",
    "canceled": "CANCELED",
    "expired": "CANCELED",
    "rejected": "REJECTED",
    "done_for_day": "CANCELED",
}


class ExecutionService:
    def __init__(self, store, broker, risk, settings, registry):
        self.store, self.broker, self.risk, self.settings, self.registry = (
            store,
            broker,
            risk,
            settings,
            registry,
        )
        self.lock = threading.RLock()
        self.healthy = False
        self.account, self.positions, self.clock = {}, [], {}
        self.open_broker_orders = []
        self.mode = settings.trading_mode

    @property
    def stop_key(self):
        return "stop:" + self.mode

    def _local_orders(self):
        with self.store.engine.connect() as c:
            return [dict(r) for r in c.execute(select(orders).where(orders.c.mode == self.mode)).mappings()]

    def reconcile(self):
        with self.lock:
            self.healthy = False
            try:
                account, positions, clock = (
                    self.broker.get_account(),
                    self.broker.get_positions(),
                    self.broker.get_clock(),
                )
                remote = self.broker.get_orders()
                if len(remote) >= 500:
                    raise ValueError("Order listing may be truncated")
                now = utcnow()
                clock_at = datetime.fromisoformat(clock["timestamp"].replace("Z", "+00:00"))
                if abs((now - clock_at).total_seconds()) > 30:
                    raise ValueError("Broker clock mismatch")
                for request in self.store.control("liquidations:" + self.mode, []):
                    result = self.broker.get_order_by_id(request["broker_id"])
                    self.apply_liquidation(result, request["symbol"])
                for local in self._local_orders():
                    if local["state"] in ACTIVE | {"FILLED", "EXIT_PENDING"} or (
                        local["state"] == "CANCELED" and float(local["payload"].get("filled_qty", 0)) > 0
                    ):
                        result = self.broker.get_order(local["id"])
                        self.apply_update(result)
                known = {r["id"] for r in self._local_orders()}
                unknown = [
                    o
                    for o in remote
                    if o.get("client_order_id") not in known and not o.get("parent_order_id")
                ]
                # Protective child orders can appear standalone; match stored bracket leg ids as well.
                leg_ids = {
                    str(leg["id"])
                    for r in self._local_orders()
                    for leg in r["payload"].get("broker", {}).get("legs", []) or []
                }
                liquidation_ids = {
                    r["broker_id"] for r in self.store.control("liquidations:" + self.mode, [])
                }
                unknown = [o for o in unknown if str(o["id"]) not in leg_ids | liquidation_ids]
                self.account, self.positions, self.clock, self.open_broker_orders = (
                    account,
                    positions,
                    clock,
                    remote,
                )
                day = now.astimezone(NY).date()
                week = day - timedelta(days=day.weekday())
                equity = float(account["equity"])
                if not self.store.control(f"day:{self.mode}:{day}"):
                    self.store.set_control(
                        f"day:{self.mode}:{day}", {"equity": float(account.get("last_equity") or equity)}
                    )
                if not self.store.control(f"week:{self.mode}:{week}"):
                    self.store.set_control(
                        f"week:{self.mode}:{week}",
                        {
                            "equity": float(account.get("last_equity") or equity),
                            "partial_week": day.weekday() != 0,
                        },
                    )
                self.store.put(
                    "account_snapshots",
                    stable_id(self.mode, now),
                    {
                        "timestamp": now,
                        "equity": equity,
                        "cash": float(account["cash"]),
                        "buying_power": float(account["buying_power"]),
                    },
                    self.mode,
                )
                self.store.put("positions", "positions:" + self.mode, positions, self.mode)
                if unknown:
                    raise ValueError("Unowned broker orders require operator reconciliation")
                self.healthy = True
                self.store.set_control(
                    "health:" + self.mode,
                    {"broker_connected": True, "reconciled": True, "at": now.isoformat()},
                )
                return True
            except Exception:
                self.store.set_control(
                    "health:" + self.mode,
                    {"broker_connected": False, "reconciled": False, "at": utcnow().isoformat()},
                )
                self.store.log("RECONCILIATION_FAILED", {"mode": self.mode, "entries_disabled": True})
                return False

    def portfolio(self, candidate, quote, now):
        day = now.astimezone(NY).date()
        week = day - timedelta(days=day.weekday())
        local = self._local_orders()
        exposures, reservations = [], []
        for p in self.positions:
            related = next(
                (
                    r
                    for r in local
                    if r["payload"]["candidate"]["symbol"] == p["symbol"]
                    and r["state"] not in {"CLOSED", "REJECTED"}
                ),
                None,
            )
            c = related["payload"]["candidate"] if related else {}
            notional = abs(float(p["market_value"]))
            exposures.append(
                {
                    "symbol": p["symbol"],
                    "notional": notional,
                    "sector": c.get("sector", "unknown"),
                    "correlation_group": c.get("correlation_group", "US_EQUITIES"),
                    "risk_dollars": abs(float(p["qty"])) * abs(float(p["current_price"]) - c["stop"])
                    if c
                    else notional,
                }
            )
        for r in local:
            if r["state"] in ACTIVE:
                d, c = r["payload"]["decision"], r["payload"]["candidate"]
                remaining = max(0, d["qty"] - float(r["payload"].get("filled_qty", 0)))
                reservations.append(
                    {
                        "symbol": c["symbol"],
                        "notional": remaining * d["entry_limit"],
                        "risk_dollars": remaining * abs(d["entry_limit"] - c["stop"]),
                        "sector": c["sector"],
                        "correlation_group": c["correlation_group"],
                    }
                )
        trades = [r["payload"] for r in self.store.list("trades", self.mode, 100000)]
        daily = [t for t in trades if datetime.fromisoformat(t["exit_at"]).astimezone(NY).date() == day]
        daily.sort(key=lambda t: t["exit_at"])
        streak = 0
        for t in reversed(daily):
            if t["net_pnl"] >= 0:
                break
            streak += 1
        key = "strategy:" + candidate.strategy + ":" + candidate.version
        stage = self.registry.stage(key)
        asset = self.broker.get_asset(candidate.symbol)
        eligible_stages = (
            {"PAPER_CANDIDATE", "PAPER_VERIFIED", "LIVE_ELIGIBLE"}
            if self.mode == "PAPER"
            else {"LIVE_ELIGIBLE"}
        )
        health = self.store.control("data_health", {})
        data_at = datetime.fromisoformat(health["at"]) if health.get("at") else now - timedelta(days=1)
        start_day = self.store.control(f"day:{self.mode}:{day}", {"equity": 0})
        start_week = self.store.control(f"week:{self.mode}:{week}", {"equity": 0})
        return Portfolio(
            equity=float(self.account.get("equity", 0)),
            buying_power=float(self.account.get("buying_power", 0)),
            day_start_equity=start_day["equity"],
            week_start_equity=start_week["equity"],
            positions=exposures,
            reservations=reservations,
            trades_today=sum(
                datetime.fromisoformat(r["payload"]["candidate"]["timestamp"]).astimezone(NY).date() == day
                and r["state"] != "REJECTED"
                for r in local
            ),
            losses_today=sum(t["net_pnl"] < 0 for t in daily),
            consecutive_losses=streak,
            strategy_drawdown=self.store.control("drawdown:" + key, 0),
            reconciled=self.healthy,
            broker_connected=self.healthy,
            data_connected=health.get("connected", False) and 0 <= (now - data_at).total_seconds() < 90,
            market_open=bool(self.clock.get("is_open")),
            clock_at=datetime.fromisoformat(self.clock["timestamp"].replace("Z", "+00:00"))
            if self.clock
            else None,
            kill_switch=self.store.control(self.stop_key, {}).get("stopped", False),
            account_blocked=bool(
                self.account.get("trading_blocked")
                or self.account.get("account_blocked")
                or self.account.get("trade_suspended_by_user")
            ),
            strategy_eligible=stage["stage"] in eligible_stages
            and stage.get("enabled", False)
            and not self.store.control("critical_error", False),
            asset_tradable=asset.get("tradable", False) and asset.get("status") == "active",
            asset_shortable=asset.get("shortable", False),
            drift_blocked=self.store.control("drift:" + key, {}).get("blocked", False),
        )

    def submit(self, candidate, quote, manual_approval=False):
        with self.lock:
            now = utcnow()
            if any(r["candidate_id"] == candidate.id for r in self._local_orders()):
                return {"status": "DUPLICATE", "candidate_id": candidate.id}
            self.store.put("candidates", candidate.id, candidate, self.mode)
            if self.broker.mode != self.mode:
                return {"status": "REJECTED", "reasons": ["BROKER_MODE_MISMATCH"]}
            if not self.reconcile():
                return {"status": "REJECTED", "reasons": ["RECONCILIATION_FAILED"]}
            # Reconciliation obtained a newer broker timestamp over the network.
            # Compare it with a decision time sampled after that response.
            now = utcnow()
            p = self.portfolio(candidate, quote, now)
            learning = self.broker.mode == "PAPER" and permitted(
                self.settings, self.store, candidate.strategy, candidate.version
            )
            risk = self.risk
            if learning:
                candidate = candidate.model_copy(update={"requested_qty": min(candidate.requested_qty, 1)})
                p.strategy_eligible = not self.store.control("critical_error", False)
                p.kill_switch = p.kill_switch or (
                    p.day_start_equity - p.equity >= self.settings.paper_learning_daily_loss
                )
                # Exposure and trade-count limits also apply outside TradingRuntime.
                risk = RiskEngine(
                    self.risk.limits.model_copy(
                        update={
                            "max_positions": min(self.risk.limits.max_positions, 2),
                            "max_trades_day": min(self.risk.limits.max_trades_day, 10),
                        }
                    )
                )
            decision = risk.evaluate(
                candidate,
                quote,
                p,
                utcnow(),
                live_capital=self.settings.live_capital_limit if self.mode == "LIVE" else None,
                live_order_cap=self.settings.live_max_order_notional
                if self.mode == "LIVE"
                else (self.settings.paper_learning_max_order_notional if learning else None),
                paper_learning=learning,
                paper_capital_cap=self.settings.paper_learning_capital if learning else None,
            )
            # Evidence payload cannot be supplied by a strategy or by a frontend candidate body.
            evidence = self.store.get(candidate.evidence_id) if candidate.evidence_id else None
            if (
                decision.accepted
                and not learning
                and (
                    not evidence
                    or evidence["kind"] != "evidence"
                    or evidence["payload"].get("synthetic")
                    or not evidence["payload"].get("verified")
                    or evidence["payload"].get("strategy_key")
                    != "strategy:" + candidate.strategy + ":" + candidate.version
                    or datetime.fromisoformat(evidence["payload"]["as_of"]) >= candidate.timestamp
                    or candidate.expected_value != evidence["payload"].get("ev")
                    or candidate.ev_lower_bound != evidence["payload"].get("ev_lower_bound")
                )
            ):
                decision = decision.model_copy(
                    update={"accepted": False, "qty": 0, "reasons": ["UNVERIFIED_EV"]}
                )
            if learning:
                decision = decision.model_copy(update={"reasons": [*decision.reasons, "PAPER_EXPERIMENT"]})
            self.store.put("risk_decisions", stable_id(candidate.id, "risk"), decision, self.mode)
            self.store.log(
                "RISK_DECISION", {"candidate_id": candidate.id, "mode": self.mode, **clean(decision)}
            )
            if "DAILY_LOSS_LIMIT" in decision.reasons:
                self.emergency_stop("DAILY_LOSS_LIMIT", self.settings.flatten_on_kill)
            if not decision.accepted:
                return {"status": "REJECTED", **clean(decision)}
            if self.mode == "LIVE":
                if self.registry.live_readiness(self.settings)["locked"]:
                    return {"status": "REJECTED", "reasons": ["LIVE_LOCKED"]}
                if self.settings.live_stage in {"OBSERVE", "SHADOW"}:
                    return {"status": self.settings.live_stage, "decision": clean(decision)}
                if self.settings.live_stage == "APPROVAL" and not manual_approval:
                    return {"status": "AWAITING_APPROVAL", "candidate_id": candidate.id}
            identifier = "aegis-" + stable_id(self.mode, candidate.id)
            payload = {
                "candidate": clean(candidate),
                "decision": clean(decision),
                "filled_qty": 0,
                "submitted_at": now.isoformat(),
                "broker": {},
                "execution_purpose": "PAPER_EXPERIMENT" if learning else "QUALIFIED_STRATEGY",
            }
            with self.store.transaction() as c:
                c.execute(
                    orders.insert().values(
                        id=identifier,
                        candidate_id=candidate.id,
                        mode=self.mode,
                        state="RISK_APPROVED",
                        payload=payload,
                    )
                )
            self.store.log(
                "ORDER_RISK_APPROVED",
                {"id": identifier, "candidate": clean(candidate), "decision": clean(decision)},
            )
            self._state(identifier, "SUBMITTED")
            try:
                result = self.broker.submit_order(ApprovedOrder(identifier, candidate, decision))
                self.apply_update(result)
                self.store.log("BROKER_ACKNOWLEDGMENT", {"id": identifier, "broker": result})
                return {"status": "SUBMITTED", "id": identifier, "broker_id": result["id"]}
            except Exception:
                self.healthy = False
                self._state(identifier, "ERROR")
                self.store.log("SUBMISSION_UNCERTAIN", {"id": identifier, "retry_allowed": False})
                return {
                    "status": "ERROR",
                    "id": identifier,
                    "reason": "Reconcile client order ID before any further entry",
                }

    def _state(self, identifier, target):
        with self.store.transaction() as c:
            current = c.execute(select(orders.c.state).where(orders.c.id == identifier)).scalar_one()
            c.execute(
                orders.update().where(orders.c.id == identifier).values(state=transition(current, target))
            )
        self.store.log("ORDER_STATE", {"id": identifier, "from": current, "to": target})

    def apply_update(self, broker_order):
        with self.lock:
            client_id = broker_order.get("client_order_id")
            local = next(
                (
                    r
                    for r in self._local_orders()
                    if r["id"] == client_id or r["broker_id"] == str(broker_order.get("id"))
                ),
                None,
            )
            if local is None:
                # Child fill notifications are reconciled from the parent bracket and its legs.
                return False
            if local["state"] == "CLOSED":
                return False
            payload = local["payload"]
            old_qty = float(payload.get("filled_qty", 0))
            qty = float(broker_order.get("filled_qty") or 0)
            if qty < old_qty:
                return False
            target = STATUS.get(broker_order.get("status"))
            if (
                target
                and target != local["state"]
                and not (local["state"] == "EXIT_PENDING" and target == "FILLED")
            ):
                # Out-of-order acknowledgements never regress a filled order.
                if (
                    target in {"ACKNOWLEDGED", "PARTIALLY_FILLED"}
                    and local["state"] in {"FILLED", "EXIT_PENDING", "CLOSED", "CANCELED"}
                    and qty <= old_qty
                ):
                    return False
                self._state(local["id"], target)
            if qty > old_qty:
                avg = float(broker_order["filled_avg_price"])
                old_avg = float(payload.get("filled_avg_price", 0))
                delta_price = (qty * avg - old_qty * old_avg) / (qty - old_qty)
                fill = {
                    "order_id": local["id"],
                    "qty": qty - old_qty,
                    "price": delta_price,
                    "timestamp": broker_order.get("filled_at") or utcnow().isoformat(),
                    "cumulative_qty": qty,
                }
                self.store.put("fills", stable_id(local["id"], qty), fill, self.mode)
                self.store.log("ENTRY_FILL", fill)
                payload["filled_avg_price"] = avg
            payload["filled_qty"], payload["broker"] = qty, broker_order
            with self.store.transaction() as c:
                c.execute(
                    orders.update()
                    .where(orders.c.id == local["id"])
                    .values(broker_id=str(broker_order["id"]), payload=payload)
                )
            legs = broker_order.get("legs") or []
            legs = legs + list(payload.get("liquidation_fills", {}).values())
            filled_legs = [leg for leg in legs if float(leg.get("filled_qty") or 0) > 0]
            exit_qty = sum(float(leg["filled_qty"]) for leg in filled_legs)
            if qty > 0 and exit_qty >= qty and local["state"] != "CLOSED":
                exit_price = (
                    sum(float(leg["filled_qty"]) * float(leg["filled_avg_price"]) for leg in filled_legs)
                    / exit_qty
                )
                c = payload["candidate"]
                sign = 1 if c["side"] == "buy" else -1
                gross = (exit_price - payload["filled_avg_price"]) * qty * sign
                trade = {
                    "symbol": c["symbol"],
                    "strategy": c["strategy"],
                    "version": c["version"],
                    "regime": c["regime"],
                    "sector": c["sector"],
                    "side": c["side"],
                    "qty": qty,
                    "entry": payload["filled_avg_price"],
                    "exit": exit_price,
                    "entry_at": broker_order.get("filled_at") or payload["submitted_at"],
                    "exit_at": max(leg.get("filled_at") or utcnow().isoformat() for leg in filled_legs),
                    "gross_pnl": gross,
                    "net_pnl": gross,
                    "fees": 0,
                    "fees_status": "unreconciled",
                    "slippage": (payload["filled_avg_price"] - c["entry"]) * sign,
                    "model_version": c["model_version"],
                    "features": c["features"],
                    "order_id": local["id"],
                    "signal_id": c["id"],
                    "signal_at": c["timestamp"],
                    "execution_purpose": payload.get("execution_purpose", "QUALIFIED_STRATEGY"),
                }
                self.store.put("trades", stable_id(local["id"], "closed"), trade, self.mode)
                self._state(local["id"], "CLOSED")
                self.store.log("TRADE_CLOSED", trade)
            return True

    def expire_entries(self, now=None):
        """Cancel stale unfilled entries; leave protective brackets intact after any fill."""
        now = now or utcnow()
        for local in self._local_orders():
            payload = local["payload"]
            if local["state"] not in {"ACKNOWLEDGED", "SUBMITTED"} or float(payload.get("filled_qty", 0)):
                continue
            if (
                now - datetime.fromisoformat(payload["submitted_at"])
            ).total_seconds() < self.settings.entry_ttl_seconds:
                continue
            remote = self.broker.get_order(local["id"])
            self.apply_update(remote)
            if float(remote.get("filled_qty") or 0) == 0 and remote.get("status") in {
                "new",
                "accepted",
                "pending_new",
            }:
                self.broker.cancel_order(remote["id"])
                self.store.log("STALE_ENTRY_CANCEL_REQUESTED", {"id": local["id"], "mode": self.mode})

    def apply_liquidation(self, broker_order, symbol):
        """Fold only explicitly requested liquidation fills into owned round trips."""
        if float(broker_order.get("filled_qty") or 0) <= 0:
            return
        for local in self._local_orders():
            if local["state"] == "CLOSED" or local["payload"]["candidate"]["symbol"] != symbol:
                continue
            if float(local["payload"].get("filled_qty", 0)) <= 0:
                continue
            payload = local["payload"]
            payload.setdefault("liquidation_fills", {})[str(broker_order["id"])] = broker_order
            with self.store.transaction() as c:
                c.execute(orders.update().where(orders.c.id == local["id"]).values(payload=payload))
            self.apply_update(payload["broker"])
            return

    def emergency_stop(self, reason="OPERATOR_STOP", flatten=False):
        with self.lock:
            self.store.set_control(
                self.stop_key, {"stopped": True, "reason": reason, "at": utcnow().isoformat()}
            )
            self.store.log("KILL_SWITCH_ACTIVATED", {"mode": self.mode, "reason": reason, "flatten": flatten})
            canceled, failures, retained = [], [], []
            try:
                remote = self.broker.get_orders()
                local_ids = {r["id"] for r in self._local_orders()}
                for order in remote:
                    if order.get("client_order_id") in local_ids and float(order.get("filled_qty") or 0) == 0:
                        try:
                            self.broker.cancel_order(order["id"])
                            canceled.append(str(order["id"]))
                        except Exception:
                            failures.append(str(order["id"]))
                    elif order.get("client_order_id") in local_ids:
                        retained.append(str(order["id"]))
                if flatten:
                    results = self.broker.close_all_positions() or []
                    requests = self.store.control("liquidations:" + self.mode, [])
                    for response in results:
                        body = response.get("body", {})
                        if response.get("status", 500) >= 300 or not body.get("id"):
                            failures.append("FLATTEN_REJECTED:" + str(response.get("symbol", "unknown")))
                            continue
                        requests.append({"broker_id": str(body["id"]), "symbol": response["symbol"]})
                        self.store.log("LIQUIDATION_SUBMITTED", {"mode": self.mode, "broker_order": body})
                        for local in self._local_orders():
                            if local["payload"]["candidate"]["symbol"] == response["symbol"] and local[
                                "state"
                            ] in {"FILLED", "PARTIALLY_FILLED", "CANCELED"}:
                                self._state(local["id"], "EXIT_PENDING")
                    self.store.set_control("liquidations:" + self.mode, requests)
            except Exception:
                failures.append("BROKER_UNAVAILABLE")
            result = {
                "stopped": True,
                "canceled": canceled,
                "failures": failures,
                "retained_partial_brackets": retained,
                "flatten_requested": flatten,
                "flatten_confirmed": False,
            }
            self.store.log("EMERGENCY_ACTIONS", result)
            return result

    def resume(self):
        with self.lock:
            if not self.reconcile():
                raise ValueError("Cannot resume before successful reconciliation")
            now = utcnow()
            day = now.astimezone(NY).date()
            baseline = self.store.control(f"day:{self.mode}:{day}", {"equity": 0})["equity"]
            if (
                baseline <= 0
                or (baseline - float(self.account["equity"])) / baseline
                >= self.risk.limits.max_daily_drawdown
            ):
                raise ValueError("Daily loss latch cannot be reset in a breached session")
            self.store.set_control("critical_error", False)
            self.store.set_control(self.stop_key, {"stopped": False, "at": now.isoformat()})
            self.store.log("OPERATOR_RESUME", {"mode": self.mode})
            return {"resumed": True}
