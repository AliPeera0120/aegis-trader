"""Single-owner service for PAPER and staged LIVE; shared scanner and execution path."""

from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
import json
import queue
import threading
from aegis.broker import AlpacaPaperBroker, AlpacaLiveBroker, normalize
from aegis.data import AlpacaData, Calendar, DataRepository, NY, select_universe
from aegis.domain import Bar, Quote, stable_id, utcnow
from aegis.evidence import Registry
from aegis.execution import ExecutionService
from aegis.features import FeatureEngine, regime
from aegis.risk import RiskEngine, RiskLimits
from aegis.strategies import baselines, Baseline
from aegis.backtest import Backtester, FillModel
from aegis.analytics import performance, decay, classify_trade, paper_comparison
from aegis.ml import drift_report
from aegis.learning import enabled_keys, status as learning_status, train_closed_outcomes


class TradingRuntime:
    def __init__(self, settings, store, broker=None, data=None):
        self.settings, self.store = settings, store
        self.registry = Registry(store)
        self.strategies = baselines()
        for strategy in self.strategies:
            self.registry.register(strategy)
        seen = {(s.name, s.version) for s in self.strategies}
        for record in store.list("strategy_versions"):
            info = record["payload"]
            strategy = Baseline(info["name"], info.get("parameters", {}))
            if strategy.version == info["version"] and (strategy.name, strategy.version) not in seen:
                self.strategies.append(strategy)
                seen.add((strategy.name, strategy.version))
        limits_path = Path("config/risk.json")
        self.risk = RiskEngine(
            RiskLimits.model_validate_json(limits_path.read_text()) if limits_path.exists() else RiskLimits()
        )
        if enabled_keys(settings):
            self.risk = RiskEngine(
                self.risk.limits.model_copy(
                    update={
                        "max_positions": min(self.risk.limits.max_positions, 2),
                        "max_trades_day": min(self.risk.limits.max_trades_day, 10),
                        "max_position_dollars": min(
                            self.risk.limits.max_position_dollars, settings.paper_learning_max_order_notional
                        ),
                    }
                )
            )
        self.broker, self.data = broker, data
        self.execution = None
        self.thread = None
        self.stop_event = threading.Event()
        self.queue = queue.Queue(maxsize=10000)
        self.history = defaultdict(lambda: deque(maxlen=10000))
        self.quotes, self.streams, self.stream_threads = {}, [], []
        self.symbols = [s.strip().upper() for s in settings.symbols.split(",") if s.strip()]
        self.calendar, self.feature_engine = Calendar(), FeatureEngine()
        self.session_date, self.last_report = None, None
        self.scan_lock = threading.RLock()
        self.eligible_symbols = set()
        self.metadata = {}
        self.event_lock = threading.Lock()
        self.pending_quotes = {}

    def connect(self):
        if self.broker is None:
            klass = AlpacaPaperBroker if self.settings.trading_mode == "PAPER" else AlpacaLiveBroker
            self.broker = klass(self.settings, readiness=lambda: self.registry.live_readiness(self.settings))
        self.execution = ExecutionService(self.store, self.broker, self.risk, self.settings, self.registry)
        if not self.execution.reconcile():
            raise RuntimeError("Broker connection or reconciliation failed")
        if self.data is None:
            self.data = AlpacaData(
                self.settings, DataRepository(self.store, self.settings.runtime_dir / "raw")
            )
        return {"connected": True, "mode": self.settings.trading_mode}

    def _enqueue(self, kind, value):
        if kind == "quote":
            # Persist one quote snapshot per symbol per cycle, not an unbounded stale tick backlog.
            row = normalize(value)
            with self.event_lock:
                previous = self.pending_quotes.get(row.get("symbol"))
                if not previous or row.get("timestamp", "") > previous.get("timestamp", ""):
                    self.pending_quotes[row.get("symbol")] = row
            return
        try:
            self.queue.put_nowait((kind, value))
        except queue.Full:
            self.store.set_control(
                "data_health", {"connected": False, "at": utcnow().isoformat(), "reason": "QUEUE_OVERFLOW"}
            )
            if self.execution:
                self.execution.healthy = False

    def _start_streams(self):
        async def on_bar(value):
            self._enqueue("bar", value)

        async def on_quote(value):
            self._enqueue("quote", value)

        async def on_trade(value):
            self._enqueue("trade", value)

        async def on_order(value):
            self._enqueue("order", value)

        self.streams = [
            self.data.stream(self.symbols, on_bar, on_quote, on_trade),
            self.broker.stream_trade_updates(on_order),
        ]

        def run_stream(stream, label):
            delay = 1
            while not self.stop_event.is_set():
                try:
                    stream.run()
                except Exception:
                    self.store.log("STREAM_DISCONNECTED", {"stream": label, "entries_disabled": True})
                if self.execution:
                    self.execution.healthy = False
                self.store.set_control("data_health", {"connected": False, "at": utcnow().isoformat()})
                if self.stop_event.wait(delay):
                    break
                delay = min(delay * 2, 60)

        for stream, label in zip(self.streams, ["market_data", "trade_updates"]):
            t = threading.Thread(target=run_stream, args=(stream, label), daemon=True)
            t.start()
            self.stream_threads.append(t)

    def initialize_session(self, now):
        day = now.astimezone(NY).date()
        # Historical context ends strictly before the current decision time. Vendor revisions are documented.
        past = self.data.history(self.symbols, now - timedelta(days=35), now, "1Day")
        daily = defaultdict(list)
        for b in past:
            if b.end < now:
                daily[b.symbol].append(b)
        recent = self.data.history(self.symbols, now - timedelta(days=10), now, "1Min")
        self.history.clear()
        for b in sorted(recent, key=lambda b: b.start):
            if b.available_at <= now:
                self.history[b.symbol].append(b)
        quotes = self.data.latest_quotes(self.symbols)
        instruments = []
        for symbol in self.symbols:
            asset = self.broker.get_asset(symbol)
            rows = sorted(daily[symbol], key=lambda b: b.start)[-20:]
            if len(rows) < 10:
                continue
            import numpy as np

            q = quotes.get(symbol)
            quote_is_fresh = bool(q and 0 <= (now - q.timestamp).total_seconds() <= 5)
            item = {
                "symbol": symbol,
                "price": (q.ask + q.bid) / 2 if quote_is_fresh else rows[-1].close,
                "adv": float(np.mean([b.volume for b in rows])),
                "dollar_volume": float(np.mean([b.close * b.volume for b in rows])),
                "spread_pct": q.spread_pct if quote_is_fresh else 0,
                "spread_check": "fresh_quote" if quote_is_fresh else "deferred_to_entry_risk_check",
                "volume_feed": self.settings.data_feed,
                "exchange": asset.get("exchange"),
                "shortable": asset.get("shortable", False),
                "tradable": asset.get("tradable", False),
                "active": asset.get("status") == "active",
                "volatility": float(np.std(np.diff(np.log([b.close for b in rows])))),
                "sector": "unknown",
            }
            instruments.append(item)
            self.store.put("instruments", symbol, item, "REFERENCE")
            self.metadata[symbol] = item
        path = Path("config/universe.json")
        if self.settings.data_feed == "iex" and Path("config/universe-iex.json").exists():
            path = Path("config/universe-iex.json")
        config = (
            json.loads(path.read_text())
            if path.exists()
            else {
                "min_price": 5,
                "max_price": 2000,
                "min_adv": 1000000,
                "min_dollar_volume": 20000000,
                "max_spread_pct": 0.002,
                "min_market_cap": None,
                "exchanges": ["NASDAQ", "NYSE", "ARCA", "AMEX"],
                "min_volatility": 0,
                "max_volatility": 0.2,
                "require_shortable": False,
            }
        )
        accepted, rejected = select_universe(instruments, config)
        self.eligible_symbols = {i["symbol"] for i in accepted}
        self.store.log(
            "UNIVERSE_BUILT",
            {
                "date": str(day),
                "accepted": sorted(self.eligible_symbols),
                "rejected": rejected,
                "point_in_time": False,
            },
        )
        stopped = self.store.control("stop:" + self.settings.trading_mode, {})
        if stopped.get("reason") == "END_OF_DAY" and stopped.get("at", "")[:10] != now.isoformat()[:10]:
            self.execution.resume()
        self.session_date = day
        self.store.set_control(
            "session",
            {
                "date": str(day),
                "prepared_at": now.isoformat(),
                "eligible_symbols": sorted(self.eligible_symbols),
                "feed": self.settings.data_feed,
            },
        )

    def process_events(self):
        with self.event_lock:
            quotes = list(self.pending_quotes.values())
            self.pending_quotes.clear()
        batch = [("quote", row) for row in quotes]
        for _ in range(5000):
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break
        for kind, obj in batch:
            now = utcnow()
            value = normalize(obj)
            try:
                if kind == "quote":
                    q = Quote(
                        symbol=value["symbol"],
                        timestamp=value["timestamp"],
                        bid=value["bid_price"],
                        ask=value["ask_price"],
                        bid_size=value.get("bid_size", 0),
                        ask_size=value.get("ask_size", 0),
                    )
                    if q.symbol in self.quotes and q.timestamp <= self.quotes[q.symbol].timestamp:
                        continue
                    self.quotes[q.symbol] = q
                    self.store.put("quotes", stable_id(q.symbol, q.timestamp), q, self.settings.trading_mode)
                    self.store.set_control(
                        "data_health",
                        {
                            "connected": 0 <= (now - q.timestamp).total_seconds() <= 5,
                            "at": q.timestamp.isoformat(),
                            "feed": self.settings.data_feed,
                        },
                    )
                elif kind == "bar":
                    start = datetime.fromisoformat(value["timestamp"].replace("Z", "+00:00"))
                    if not self.calendar.is_open(start):
                        continue  # Premarket bars must not contaminate regular-session VWAP/opening ranges.
                    b = Bar(
                        symbol=value["symbol"],
                        start=start,
                        end=start + timedelta(minutes=1),
                        available_at=max(now, start + timedelta(minutes=1)),
                        open=value["open"],
                        high=value["high"],
                        low=value["low"],
                        close=value["close"],
                        volume=value["volume"],
                        vwap=value.get("vwap"),
                        source=f"stream-{self.settings.data_feed}",
                    )
                    previous = self.history[b.symbol][-1] if self.history[b.symbol] else None
                    if previous and b.start <= previous.start:
                        continue
                    if previous and abs(b.close / previous.close - 1) > 0.35:
                        raise ValueError("Outlier print")
                    self.history[b.symbol].append(b)
                    DataRepository(self.store).save(
                        [b], {"source": b.source, "synthetic": False, "stream": True}
                    )
                elif kind == "order":
                    self.execution.apply_update(value.get("order", value))
                    self.execution.reconcile()
                elif kind == "trade":
                    self.store.put(
                        "system_events",
                        stable_id("print", value.get("symbol"), value.get("id"), value.get("timestamp")),
                        {"event": "TRADE_PRINT", **value},
                        self.settings.trading_mode,
                    )
            except Exception:
                self.store.set_control(
                    "data_health", {"connected": False, "at": now.isoformat(), "reason": "INVALID_EVENT"}
                )
                self.store.log(
                    "EVENT_QUARANTINED",
                    {"kind": kind, "symbol": value.get("symbol"), "entries_disabled": True},
                )

    def scan(self, now=None):
        with self.scan_lock:
            now = now or utcnow()
            ranked = []
            benchmark = list(self.history.get("SPY", []))
            market = regime(self.feature_engine.calculate(benchmark, now))
            self.store.put(
                "market_regimes",
                stable_id("regime", now),
                {"at": now, "regime": market},
                self.settings.trading_mode,
            )
            for symbol in sorted(self.eligible_symbols):
                q = self.quotes.get(symbol)
                features = self.feature_engine.calculate(
                    list(self.history[symbol]),
                    now,
                    benchmark if symbol != "SPY" else None,
                    q if q and q.timestamp <= now else None,
                )
                if not features:
                    continue
                for strategy in self.strategies:
                    candidate = strategy.generate_signal(symbol, now, features, market)
                    if not candidate:
                        continue
                    # Identity uses completed data timestamp, not scan time, to deduplicate repeated scans.
                    latest = self.history[symbol][-1]
                    candidate = candidate.model_copy(
                        update={"id": stable_id(symbol, latest.end, strategy.name, strategy.version)}
                    )
                    key = "strategy:" + strategy.name + ":" + strategy.version
                    evidence = next(
                        (
                            r
                            for r in self.store.list("evidence")
                            if r["payload"].get("strategy_key") == key
                            and r["payload"].get("verified")
                            and not r["payload"].get("synthetic")
                            and r["payload"].get("evaluation") in {"OOS", "PAPER"}
                        ),
                        None,
                    )
                    if evidence:
                        ev = evidence["payload"]
                        candidate = candidate.model_copy(
                            update={
                                "evidence_id": evidence["id"],
                                "expected_value": ev.get("ev"),
                                "ev_lower_bound": ev.get("ev_lower_bound"),
                            }
                        )
                    self.store.put(
                        "signals", stable_id(candidate.id, "signal"), candidate, self.settings.trading_mode
                    )
                    ranked.append(candidate)
            ranked.sort(
                key=lambda c: (
                    -(c.expected_value if c.expected_value is not None else -1e9),
                    -c.score,
                    c.symbol,
                )
            )
            results = []
            for rank, candidate in enumerate(ranked, 1):
                if self.settings.trading_mode == "LIVE" and self.settings.live_stage in {"OBSERVE", "SHADOW"}:
                    self.store.put("candidates", candidate.id, candidate, "LIVE")
                    decision = {"status": self.settings.live_stage, "reasons": ["NO_ORDER_STAGE"]}
                    self.store.log(
                        "LIVE_SHADOW_CANDIDATE",
                        {"candidate": candidate.model_dump(mode="json"), "mode": "LIVE"},
                    )
                else:
                    decision = self.execution.submit(candidate, self.quotes.get(candidate.symbol))
                result = {"rank": rank, "candidate": candidate.model_dump(mode="json"), "decision": decision}
                self.store.put(
                    "ranks", stable_id(candidate.id, "rank", now), result, self.settings.trading_mode
                )
                results.append(result)
            return results

    def report(self, date):
        mode = self.settings.trading_mode
        training = train_closed_outcomes(self.settings, self.store)
        trades = [
            r["payload"]
            for r in self.store.list("trades", mode, 100000)
            if datetime.fromisoformat(r["payload"]["exit_at"]).astimezone(NY).date() == date
        ]
        snapshots = sorted(
            [
                r["payload"]
                for r in self.store.list("account_snapshots", mode, 100000)
                if datetime.fromisoformat(r["payload"]["timestamp"]).astimezone(NY).date() == date
            ],
            key=lambda r: r["timestamp"],
        )
        initial = self.store.control(f"day:{mode}:{date}", {"equity": 0})["equity"]
        metrics = performance(trades, snapshots, initial) if initial > 0 else None
        events = [
            e
            for e in self.store.events(100000)
            if datetime.fromisoformat(e["timestamp"]).astimezone(NY).date() == date
        ]
        expected = []
        comparison_flags = []
        try:
            historical = DataRepository(self.store).load(source="alpaca-" + self.settings.data_feed)
            streamed = DataRepository(self.store).load(source="stream-" + self.settings.data_feed)
            combined = {
                (b.symbol, b.start): b for b in historical + streamed if b.start.astimezone(NY).date() == date
            }
            replay_bars = [b.model_copy(update={"source": "session-replay"}) for b in combined.values()]
            if replay_bars:
                for strategy in self.strategies:
                    replay_result = Backtester(strategy, FillModel.named("standard"), self.risk).run(
                        replay_bars, synthetic=False
                    )
                    expected.extend(replay_result["trades"])
            else:
                comparison_flags.append("No session bars available for expected-fill replay")
        except Exception:
            comparison_flags.append("Expected-fill replay unavailable; review data quality")
        report = {
            "date": str(date),
            "mode": mode,
            "metrics": metrics,
            "trades": [{**t, "review": classify_trade(t)} for t in trades],
            "risk_rejections": sum(
                e["event"] == "RISK_DECISION" and not e["payload"].get("accepted") for e in events
            ),
            "system_errors": sum(
                e["event"]
                in {"RECONCILIATION_FAILED", "EVENT_QUARANTINED", "SUBMISSION_UNCERTAIN", "SERVICE_ERROR"}
                for e in events
            ),
            "flags": ["Broker fee reconciliation required before net results qualify as evidence"]
            + comparison_flags,
            "account_start": initial or None,
            "account_end": snapshots[-1]["equity"] if snapshots else None,
            "comparison": paper_comparison(expected, trades),
            "paper_learning": learning_status(self.settings, self.store),
            "training": training,
        }
        self.store.put("reports", stable_id(date, mode, "report"), report, mode)
        self.settings.runtime_dir.joinpath("reports").mkdir(parents=True, exist_ok=True)
        self.settings.runtime_dir.joinpath("reports", f"{date}-{mode}.json").write_text(
            json.dumps(report, indent=2, default=str)
        )
        all_trades = [r["payload"] for r in self.store.list("trades", mode, 100000)]
        self.update_monitors(all_trades)
        return report

    def update_monitors(self, trades):
        for strategy in self.strategies:
            key = "strategy:" + strategy.name + ":" + strategy.version
            matching = [
                t for t in trades if t["strategy"] == strategy.name and t["version"] == strategy.version
            ]
            evidence = next(
                (
                    r["payload"]
                    for r in self.store.list("evidence")
                    if r["payload"].get("strategy_key") == key
                ),
                {},
            )
            normalized_trades = [{**t, "net_pnl": t["net_pnl"] / t["qty"]} for t in matching]
            result = decay(normalized_trades, evidence.get("ev", 0), utcnow())
            ordered = sorted(matching, key=lambda t: t["exit_at"])
            account = self.execution.account if self.execution else {}
            baseline = float(account.get("equity", 0))
            if baseline > 0:
                import numpy as np

                pnl = np.r_[0, np.cumsum([t["net_pnl"] for t in ordered])]
                self.store.set_control(
                    "drawdown:" + key, float(np.max(np.maximum.accumulate(pnl) - pnl) / baseline)
                )
            if evidence.get("feature_reference") and matching:
                current = {
                    name: [t["features"][name] for t in matching if name in t.get("features", {})]
                    for name in evidence["feature_reference"]
                }
                result["feature_drift"] = drift_report(evidence["feature_reference"], current)
                result["blocked"] = result["blocked"] or result["feature_drift"]["blocked"]
            self.store.set_control("drift:" + key, result)
            if result["blocked"]:
                self.store.log("STRATEGY_AUTO_SUSPENDED", {"strategy_key": key, "monitor": result})

    def run_cycle(self, now=None):
        now = now or utcnow()
        self.store.set_control(
            "streams",
            {
                "at": now.isoformat(),
                "market_data_authenticated": bool(
                    self.streams and getattr(self.streams[0], "_running", False)
                ),
                "trade_updates_authenticated": bool(
                    len(self.streams) > 1 and getattr(self.streams[1], "_running", False)
                ),
            },
        )
        self.process_events()
        if not self.execution.reconcile():
            return
        self.execution.expire_entries()
        self.store.set_control("market_clock", self.execution.clock)
        day = now.astimezone(NY).date()
        session = self.calendar.session(now)
        if session and session[0] - timedelta(minutes=30) <= now < session[1] and self.session_date != day:
            self.initialize_session(now)
        if enabled_keys(self.settings) and daily_loss_breached(
            self.execution, self.settings, self.store, day
        ):
            if not self.store.control(self.execution.stop_key, {}).get("stopped"):
                self.execution.emergency_stop("PAPER_LEARNING_DAILY_LOSS", flatten=True)
        daily = self.store.control(f"day:{self.settings.trading_mode}:{day}", {"equity": 0})["equity"]
        if (
            daily > 0
            and (daily - float(self.execution.account["equity"])) / daily
            >= self.risk.limits.max_daily_drawdown
        ):
            if not self.store.control(self.execution.stop_key, {}).get("stopped"):
                self.execution.emergency_stop("DAILY_LOSS_LIMIT", self.settings.flatten_on_kill)
        if self.execution.clock.get("is_open") and session:
            if now >= session[1] - timedelta(minutes=5):
                stopped = self.store.control(self.execution.stop_key, {})
                if not stopped.get("stopped") or self.execution.positions:
                    self.execution.emergency_stop(
                        "END_OF_DAY", flatten=self.settings.overnight_policy == "FLATTEN"
                    )
            else:
                self.scan()
        if session and now >= session[1] and self.last_report != day:
            self.report(day)
            self.last_report = day

    def run(self):
        import fcntl

        self.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        lease = (self.settings.runtime_dir / ("execution-" + self.settings.trading_mode + ".lock")).open("a+")
        try:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lease.close()
            self.store.log("SECOND_EXECUTION_OWNER_REJECTED", {"mode": self.settings.trading_mode})
            return
        self.store.set_control(
            "service", {"running": True, "mode": self.settings.trading_mode, "at": utcnow().isoformat()}
        )
        try:
            self.connect()
            self._start_streams()
            while not self.stop_event.is_set():
                now = utcnow()
                self.store.set_control(
                    "heartbeat", {"at": now.isoformat(), "mode": self.settings.trading_mode}
                )
                try:
                    self.run_cycle(now)
                except Exception as exc:
                    self.execution.healthy = False
                    self.store.set_control("critical_error", True)
                    self.store.log(
                        "SERVICE_ERROR",
                        {
                            "entries_disabled": True,
                            "at": utcnow().isoformat(),
                            "error_type": type(exc).__name__,
                        },
                    )
                self.stop_event.wait(self.settings.scan_interval_seconds)
        finally:
            for stream in self.streams:
                try:
                    stream.stop()
                except Exception:
                    pass
            lease.close()
            self.store.set_control(
                "service", {"running": False, "mode": self.settings.trading_mode, "at": utcnow().isoformat()}
            )
            self.store.set_control("data_health", {"connected": False, "at": utcnow().isoformat()})

    def start(self):
        if not self.settings.service_enabled:
            raise ValueError("Set SERVICE_ENABLED=true explicitly before starting the trading service")
        if self.settings.paper_learning_enabled:
            keys = enabled_keys(self.settings)
            current = {"strategy:" + s.name + ":" + s.version for s in self.strategies}
            if not keys or not keys <= current:
                raise ValueError("Paper learning requires exact current registered strategy keys")
        if self.thread and self.thread.is_alive():
            return {"running": True}
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return {"running": True}

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        return {"stop_requested": True}


def daily_loss_breached(execution, settings, store, day):
    baseline = store.control(f"day:PAPER:{day}", {"equity": 0})["equity"]
    return (
        baseline > 0 and baseline - float(execution.account["equity"]) >= settings.paper_learning_daily_loss
    )
