"""Verified TLS for SDK websockets, including Python installs without a system CA bundle."""

import ssl
import certifi


def websocket_params():
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return {"ssl": context, "ping_interval": 10, "ping_timeout": 180, "max_queue": 1024}
