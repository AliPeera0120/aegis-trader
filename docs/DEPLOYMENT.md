# Deployment

The local validated environment is Python 3.13 on macOS. `requirements.lock` pins the full tested dependency set, including development tools. The Docker image targets Python 3.13 slim, installs the lock, installs the source package without dependency resolution, runs as UID 10001, and exposes a health check. Docker was not installed on the build machine, so image build/runtime behavior must be checked on your server or CI.

Local setup:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
alembic upgrade head
aegis init
aegis serve
```

For Docker, set AEGIS_CONTROL_TOKEN and any paper/data credentials in a private environment, then run `docker compose up --build -d`. The port is published only on loopback. Use `docker compose logs --tail=100` for process diagnostics; trading events are in the database/audit dashboard. Set SERVICE_ENABLED=true only when you want the worker to start with the web process. A volume persists the database, raw data, audit and reports. Restart policy is unless-stopped. Credentials are passed at runtime, not baked into layers. The compose file does not mount a real `.env` inside the image.

PostgreSQL: set `DATABASE_URL=postgresql+psycopg://...` through a private secret mechanism, run `alembic upgrade head`, then start one service owner. SQLAlchemy JSON, transactions and the audit lock row are portable; PostgreSQL runtime has not been tested in this environment. Back up both database and raw files. SQLite uses WAL, a busy timeout and a single writer; use durable local storage rather than an unreliable shared network filesystem.

Migrations: on a fresh database use Alembic before starting the app. Local first-run convenience can create the current schema through SQLAlchemy. If such a v0.1 database already exists and matches the initial schema, use `alembic stamp 0001` before future migrations; do not run the initial create migration over existing tables. Inspect migrations before applying them. Never run `downgrade` on trading records without a verified backup.

The process needs network access to official Alpaca HTTPS/WebSocket endpoints and accurate system time. Run one API worker: do not use uvicorn multi-worker mode or multiple host replicas against the same account. A local lock is not a distributed lease. A laptop that sleeps cannot run an unattended session; use an always-on host. `/healthz` checks the database and worker heartbeat; application broker/data/readiness state is separate.
