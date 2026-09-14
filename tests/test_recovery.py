from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import requests
import pytest
from fastapi.testclient import TestClient
from aegis.api import create_app
from aegis.domain import utcnow
from aegis.transport import TimeoutAdapter
from test_execution import ready


def test_reconciliation_latency_does_not_make_new_clock_future(store, settings, candidate, quote):
    service, broker, c, q = ready(store, settings, candidate, quote)
    # Real broker timestamps are obtained after submit() begins, unlike a frozen fixture clock.
    broker.get_clock = lambda: {"timestamp": utcnow().isoformat(), "is_open": True}
    assert service.submit(c, q)["status"] == "SUBMITTED"


def test_hung_response_times_out_without_retry():
    release = threading.Event()
    attempts = []

    class HangingHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            attempts.append(1)
            release.wait(2)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), HangingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = requests.Session()
    session.mount("http://", TimeoutAdapter(timeout=(0.2, 0.05)))
    try:
        with pytest.raises(requests.exceptions.ReadTimeout):
            session.get(f"http://127.0.0.1:{server.server_port}/")
        assert len(attempts) == 1
    finally:
        release.set()
        session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_stale_status_cannot_advertise_running_worker_or_fresh_streams(store, settings):
    old = (utcnow() - timedelta(hours=2)).isoformat()
    store.set_control("service", {"running": True, "mode": "PAPER"})
    store.set_control("heartbeat", {"at": old})
    store.set_control("data_health", {"at": old, "connected": True})
    store.set_control(
        "streams", {"at": old, "market_data_authenticated": True, "trade_updates_authenticated": True}
    )
    with TestClient(create_app(settings, store)) as client:
        status = client.get("/api/status").json()
        assert not status["service"]["running"] and status["service"]["stale"]
        assert not status["data"]["connected"]
        assert not status["streams"]["market_data_authenticated"]
        assert client.get("/healthz").status_code == 503
