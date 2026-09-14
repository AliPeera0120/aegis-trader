"""Bounded SDK network waits. Transport never retries an uncertain order submission."""

from requests.adapters import HTTPAdapter


class TimeoutAdapter(HTTPAdapter):
    def __init__(self, timeout=(5, 10)):
        self.timeout = timeout
        super().__init__(max_retries=0)

    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)


def bound_requests(client):
    # alpaca-py 0.44 uses a requests Session but supplies no request timeout.
    client._session.mount("https://", TimeoutAdapter())
    return client
