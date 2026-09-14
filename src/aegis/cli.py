import argparse
import json
import os
from datetime import datetime
from pathlib import Path
import sys
from aegis.config import Settings
from aegis.store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="aegis", description="Aegis Trader: research and risk-gated execution"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create database and versioned strategy registry")
    serve = sub.add_parser("serve", help="Run dashboard and API")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--host", default="127.0.0.1")
    demo = sub.add_parser("demo", help="Run a clearly labeled synthetic validation experiment")
    demo.add_argument("--days", type=int, default=3)
    demo.add_argument("--strategy", default="orb")
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--fill", choices=["optimistic", "standard", "conservative", "all"], default="all")
    ingest = sub.add_parser("ingest", help="Download Alpaca historical raw bars")
    ingest.add_argument("--symbols", default="SPY,QQQ,IWM")
    ingest.add_argument("--start", required=True)
    ingest.add_argument("--end", required=True)
    ingest.add_argument("--timeframe", choices=["1Min", "5Min", "15Min", "1Day"], default="1Min")
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--source", required=True)
    backtest.add_argument("--strategy", default="orb")
    backtest.add_argument("--fill", default="standard", choices=["optimistic", "standard", "conservative"])
    walk = sub.add_parser("walk-forward")
    walk.add_argument("--source", required=True)
    walk.add_argument("--strategy", default="orb")
    walk.add_argument("--grid", default='{"opening_minutes":[5,15,30],"min_rvol":[1.0,1.5]}')
    walk.add_argument("--train-days", type=int, default=20)
    walk.add_argument("--validation-days", type=int, default=5)
    walk.add_argument("--test-days", type=int, default=5)
    walk.add_argument("--embargo-days", type=int, default=1)
    walk.add_argument("--random-count", type=int)
    ml = sub.add_parser("ml")
    ml.add_argument("--rows", required=True, help="JSON feature/label rows with timestamp and label_end")
    ml.add_argument("--features", required=True)
    ml.add_argument("--model", default="logistic", choices=["logistic", "random_forest", "gradient_boosting"])
    ml.add_argument("--calibration", default="platt", choices=["platt", "isotonic"])
    replay = sub.add_parser("replay")
    replay.add_argument("--source", required=True)
    replay.add_argument("--date", required=True)
    replay.add_argument("--strategy", default="orb")
    replay.add_argument("--speed", type=float, default=60)
    replay.add_argument("--output", default="var/replay.jsonl")
    gaps = sub.add_parser("gap-study")
    gaps.add_argument("--source", required=True)
    sub.add_parser("paper", help="Run continuous PAPER service (never LIVE)")
    sub.add_parser("live", help="Run configured staged LIVE service; observe is default")
    sub.add_parser("status")
    sub.add_parser("verify-audit")
    sub.add_parser("connect-check", help="Read-only PAPER authentication, clock and reconciliation check")
    stop = sub.add_parser("stop", help="Persist emergency latch and cancel eligible entries")
    stop.add_argument("--flatten", action="store_true")
    sub.add_parser("resume")
    report = sub.add_parser("report")
    report.add_argument("--date", required=True)
    fee = sub.add_parser("reconcile-fees")
    fee.add_argument("--statement", required=True)
    certify = sub.add_parser("certify-paper-checks")
    certify.add_argument("--report", required=True)
    certify.add_argument("--attestation", required=True)
    paper_evidence = sub.add_parser("derive-paper-evidence")
    paper_evidence.add_argument("--strategy-key", required=True)
    override = sub.add_parser("override-readiness")
    override.add_argument("--reason", required=True)
    override.add_argument("--hours", type=float, required=True)
    override.add_argument("--acknowledgment", required=True)
    register = sub.add_parser("register-strategy")
    register.add_argument("--name", required=True)
    register.add_argument("--parameters", default="{}")
    evidence = sub.add_parser("derive-evidence")
    evidence.add_argument("--experiment", required=True)
    evidence.add_argument("--strategy-key", required=True)
    evidence.add_argument("--evaluation", choices=["BACKTEST", "OOS"], required=True)
    promote = sub.add_parser("promote")
    promote.add_argument("--strategy-key", required=True)
    promote.add_argument("--target", required=True)
    promote.add_argument("--evidence", required=True, help="Comma-separated immutable stored evidence IDs")
    token = sub.add_parser(
        "create-control-token", help="Write a new random token to a private file; never print it"
    )
    token.add_argument("--output", default="var/operator-token.txt")
    args = parser.parse_args(argv)
    store = None
    try:
        settings = Settings()
        settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        store = Store(settings.database_url.get_secret_value())
        if args.command == "serve":
            if (
                args.host not in {"127.0.0.1", "localhost"}
                and not settings.aegis_control_token.get_secret_value()
            ):
                raise ValueError("A control token is required before binding outside loopback")
            import uvicorn
            from aegis.api import create_app

            uvicorn.run(create_app(settings, store), host=args.host, port=args.port, access_log=False)
            return
        if args.command == "create-control-token":
            import secrets

            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as file:
                file.write(secrets.token_urlsafe(48) + "\n")
            print(
                "Operator token created in a private file. Set AEGIS_CONTROL_TOKEN locally; do not commit it."
            )
            return
        if args.command in {"demo", "backtest", "walk-forward", "ingest", "gap-study", "replay"}:
            from aegis.research import synthetic_bars, persist_experiment, walk_forward, gap_study
            from aegis.backtest import Backtester, FillModel, replay as replay_events
            from aegis.strategies import Baseline
            from aegis.data import AlpacaData, DataRepository, NY

            repo = DataRepository(store, settings.runtime_dir / "raw")
            if args.command == "ingest":
                bars = AlpacaData(settings, repo).history(
                    args.symbols.split(","),
                    datetime.fromisoformat(args.start),
                    datetime.fromisoformat(args.end),
                    args.timeframe,
                )
                print(json.dumps({"bars_ingested": len(bars), "source": "alpaca-" + settings.data_feed}))
                return
            bars = (
                synthetic_bars(days=args.days, seed=args.seed)
                if args.command == "demo"
                else repo.load(source=args.source)
            )
            synthetic = args.command == "demo" or any(b.source.startswith("synthetic") for b in bars)
            if args.command == "walk-forward":
                result = walk_forward(
                    bars,
                    args.strategy,
                    json.loads(args.grid),
                    args.train_days,
                    args.validation_days,
                    args.test_days,
                    args.embargo_days,
                    args.random_count,
                    synthetic=synthetic,
                )
                identifier = persist_experiment(store, result, "Purged walk-forward parameter sensitivity")
                print(
                    json.dumps(
                        {"experiment": identifier, "folds": len(result["folds"]), "synthetic": synthetic}
                    )
                )
                return
            if args.command == "gap-study":
                result = gap_study(bars)
                output = settings.runtime_dir / "gap-study.json"
                output.write_text(json.dumps(result, indent=2, default=str))
                print("Gap study saved to " + str(output))
                return
            if args.command == "replay":
                bars = [b for b in bars if str(b.start.astimezone(NY).date()) == args.date]
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("w") as file:
                    for event in replay_events(bars, Baseline(args.strategy), args.speed):
                        file.write(json.dumps(event) + "\n")
                print("Replay event tape saved; delay_seconds controls playback pacing.")
                return
            if args.command == "demo":
                repo.save(
                    bars,
                    {
                        "synthetic": True,
                        "source": f"synthetic-{args.seed}",
                        "seed": args.seed,
                        "days": args.days,
                    },
                )
            for name in ["optimistic", "standard", "conservative"] if args.fill == "all" else [args.fill]:
                result = Backtester(Baseline(args.strategy), FillModel.named(name)).run(
                    bars, synthetic=synthetic
                )
                identifier = persist_experiment(
                    store,
                    result,
                    "Infrastructure validation" if synthetic else "Baseline hypothesis evaluation",
                )
                print(
                    json.dumps(
                        {
                            "experiment": identifier,
                            "synthetic": synthetic,
                            "fill": name,
                            "metrics": result["metrics"],
                        }
                    )
                )
            return
        if args.command == "ml":
            from aegis.ml import experiment

            rows = json.loads(Path(args.rows).read_text())
            _, result = experiment(rows, args.features.split(","), args.model, args.calibration)
            store.put("model_versions", result["id"], result)
            path = settings.runtime_dir / ("ml-" + result["id"] + ".json")
            path.write_text(json.dumps(result, indent=2))
            print(json.dumps({"model_report": str(path), "metrics": result["metrics"]}))
            return
        if args.command == "verify-audit":
            result = store.verify_audit()
            print(json.dumps(result))
            if not result["valid"]:
                sys.exit(1)
            return
        from aegis.runtime import TradingRuntime

        runtime = TradingRuntime(settings, store)
        if args.command == "init":
            print("Database and strategy registry initialized. PAPER default; LIVE locked.")
        elif args.command == "status":
            print(
                json.dumps(
                    {
                        "mode": settings.trading_mode,
                        "service": store.control("service", {}),
                        "live": runtime.registry.live_readiness(settings),
                    },
                    indent=2,
                )
            )
        elif args.command == "reconcile-fees":
            from aegis.certification import reconcile_fees

            print(json.dumps(reconcile_fees(store, args.statement, settings.trading_mode)))
        elif args.command == "certify-paper-checks":
            from aegis.certification import certify_paper_checks

            if settings.trading_mode != "PAPER":
                raise ValueError("Paper certification requires PAPER mode")
            print(json.dumps(certify_paper_checks(store, args.report, args.attestation)))
        elif args.command == "derive-paper-evidence":
            from aegis.certification import derive_paper_evidence

            print(
                derive_paper_evidence(
                    store, args.strategy_key, settings.min_paper_days, settings.min_paper_trades
                )
            )
        elif args.command == "override-readiness":
            from aegis.certification import readiness_override

            print(
                "WARNING: readiness evidence checks will be temporarily waived; this does not guarantee safety or profitability.",
                file=sys.stderr,
            )
            print(json.dumps(readiness_override(store, args.reason, args.hours, args.acknowledgment)))
        elif args.command == "register-strategy":
            from aegis.strategies import Baseline

            print(runtime.registry.register(Baseline(args.name, json.loads(args.parameters))))
        elif args.command == "derive-evidence":
            from aegis.evidence import evidence_from_experiment

            print(evidence_from_experiment(store, args.experiment, args.strategy_key, args.evaluation))
        elif args.command == "promote":
            print(
                json.dumps(
                    runtime.registry.promote(
                        args.strategy_key, args.target, args.evidence.split(","), "cli-operator"
                    )
                )
            )
        elif args.command == "connect-check":
            if settings.trading_mode != "PAPER":
                raise ValueError("Integration checks only run in PAPER mode")
            print(json.dumps(runtime.connect()))
        elif args.command in {"paper", "live"}:
            required = "PAPER" if args.command == "paper" else "LIVE"
            if settings.trading_mode != required or not settings.service_enabled:
                raise ValueError("Explicit matching TRADING_MODE and SERVICE_ENABLED=true required")
            runtime.run()
        elif args.command == "stop":
            from aegis.domain import utcnow

            store.set_control(
                "stop:" + settings.trading_mode,
                {"stopped": True, "at": utcnow().isoformat(), "reason": "CLI_STOP"},
            )
            store.log(
                "KILL_SWITCH_ACTIVATED",
                {"mode": settings.trading_mode, "source": "CLI", "flatten_requested": args.flatten},
            )
            runtime.connect()
            print(json.dumps(runtime.execution.emergency_stop("CLI_STOP", args.flatten)))
        elif args.command == "resume":
            runtime.connect()
            print(json.dumps(runtime.execution.resume()))
        elif args.command == "report":
            print(json.dumps(runtime.report(datetime.fromisoformat(args.date).date()), indent=2))
    except KeyboardInterrupt:
        if "runtime" in locals():
            runtime.stop()
    except Exception as exc:
        # Do not print arbitrary exceptions, config validation inputs, provider requests or secrets.
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": "Command failed. Review configuration and sanitized audit events; entries remain gated.",
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    finally:
        if store is not None:
            store.engine.dispose()


if __name__ == "__main__":
    main()
