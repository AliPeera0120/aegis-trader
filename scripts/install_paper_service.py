"""Install a macOS user service for the configured PAPER app. No credentials in the plist."""

import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time
from aegis.config import Settings
from dotenv import set_key


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    settings = Settings()
    if sys.platform != "darwin" or settings.trading_mode != "PAPER" or not settings.service_enabled:
        raise SystemExit("Requires macOS, TRADING_MODE=PAPER and SERVICE_ENABLED=true")
    if not settings.aegis_control_token.get_secret_value() or not all(settings.credentials("PAPER")):
        raise SystemExit("Configure private operator token and PAPER credentials first")
    label = "com.aegis-trader.paper"
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain + "/" + label], capture_output=True)
    deadline = time.monotonic() + 30
    while subprocess.run(["launchctl", "print", domain + "/" + label], capture_output=True).returncode == 0:
        if time.monotonic() >= deadline:
            raise SystemExit(
                "Previous service is still unloading; inspect launchctl status before restarting"
            )
        time.sleep(0.25)
    # LaunchAgents cannot reliably access macOS-protected Documents directories.
    # Deploy a private runtime snapshot to the standard user application-data location.
    app = Path.home() / "Library/Application Support/AegisTrader"
    app.mkdir(parents=True, exist_ok=True, mode=0o700)
    for folder in ("src", "config"):
        shutil.copytree(
            root / folder, app / folder, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__")
        )
    venv = app / "venv"
    if not venv.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        packages = Path(sys.prefix) / "lib/python3.13/site-packages"
        shutil.copytree(
            packages,
            venv / "lib/python3.13/site-packages",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "__editable__*"),
        )
    var = app / "var"
    var.mkdir(exist_ok=True, mode=0o700)
    source_var = settings.runtime_dir.resolve()
    database = var / "aegis.db"
    if not database.exists():
        if not settings.database_url.get_secret_value().startswith("sqlite:///"):
            raise SystemExit("This local installer requires SQLite")
        old_db = Path(settings.database_url.get_secret_value().removeprefix("sqlite:///"))
        with sqlite3.connect(old_db) as source, sqlite3.connect(database) as destination:
            source.backup(destination)
        shutil.copytree(
            source_var,
            var,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.db", "*.db-*", "*.lock", "service"),
        )
        database.chmod(0o600)
    # Development CLI and deployed worker share one authoritative data store.
    set_key(str(root / ".env"), "DATABASE_URL", "sqlite:///" + str(database))
    set_key(str(root / ".env"), "RUNTIME_DIR", str(var))
    shutil.copy2(root / ".env", app / ".env")
    (app / ".env").chmod(0o600)
    target = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    target.parent.mkdir(parents=True, exist_ok=True)
    logs = var / "service"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("stdout.log", "stderr.log"):
        (logs / name).touch(mode=0o600, exist_ok=True)
        (logs / name).chmod(0o600)
    data = {
        "Label": label,
        "ProgramArguments": [
            "/usr/bin/caffeinate",
            "-i",
            str(venv / "bin/python"),
            "-m",
            "aegis.cli",
            "serve",
        ],
        "WorkingDirectory": str(app),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(logs / "stdout.log"),
        "StandardErrorPath": str(logs / "stderr.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONPATH": str(app / "src")},
        "Umask": 0o077,
    }
    target.write_bytes(plistlib.dumps(data))
    target.chmod(0o600)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    print("Installed PAPER service: " + label)
    print("Dashboard: http://127.0.0.1:8000")
    print("Status: launchctl print " + domain + "/" + label)
    print("Idle sleep is prevented while running. Keep the Mac powered on, lid open, and online.")


if __name__ == "__main__":
    main()
