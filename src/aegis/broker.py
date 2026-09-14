"""Official Alpaca SDK boundary. Only execution.py may request entry orders."""

from dataclasses import dataclass
from typing import Protocol
from aegis.domain import Candidate
from aegis.risk import RiskDecision


class BrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovedOrder:
    client_order_id: str
    candidate: Candidate
    decision: RiskDecision

    # Created after the execution service persists its authorization decision.
    def __post_init__(self):
        if not self.decision.accepted or self.decision.qty <= 0:
            raise ValueError("Risk approval required")


class BrokerAdapter(Protocol):
    mode: str

    def get_account(self): ...
    def get_positions(self): ...
    def get_orders(self): ...
    def get_order(self, client_order_id): ...
    def submit_order(self, approved: ApprovedOrder): ...
    def replace_order(self, order_id, **changes): ...
    def cancel_order(self, order_id): ...
    def close_position(self, symbol): ...
    def close_all_positions(self): ...
    def get_clock(self): ...
    def get_asset(self, symbol): ...
    def get_calendar(self, start, end): ...
    def stream_trade_updates(self, handler): ...


def normalize(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [normalize(x) for x in value]
    return value


class _AlpacaBroker:
    def __init__(self, settings, mode, client=None, readiness=None):
        self.settings, self.mode, self.readiness = settings, mode, readiness
        key, secret = settings.credentials(mode)
        if not key or not secret:
            raise BrokerError(f"{mode} credentials are not configured")
        from alpaca.trading.client import TradingClient
        from aegis.transport import bound_requests

        self._client = client or bound_requests(TradingClient(key, secret, paper=mode == "PAPER"))

    def _call(self, name, *args, **kwargs):
        try:
            return normalize(getattr(self._client, name)(*args, **kwargs))
        except Exception:
            # SDK errors can contain request/response material. Never propagate them to logs or API clients.
            raise BrokerError(
                f"Alpaca {name} failed; entries must remain paused until reconciliation"
            ) from None

    def get_account(self):
        return self._call("get_account")

    def get_positions(self):
        return self._call("get_all_positions")

    def get_orders(self):
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        return self._call(
            "get_orders", GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True, limit=500)
        )

    def get_order(self, client_order_id):
        order = self._call("get_order_by_client_id", client_order_id)
        if order.get("order_class") in {"bracket", "oco", "oto"}:
            from alpaca.trading.requests import GetOrderByIdRequest

            return self._call("get_order_by_id", order["id"], filter=GetOrderByIdRequest(nested=True))
        return order

    def get_order_by_id(self, identifier):
        return self._call("get_order_by_id", identifier)

    def get_clock(self):
        return self._call("get_clock")

    def get_asset(self, symbol):
        return self._call("get_asset", symbol)

    def get_calendar(self, start, end):
        from alpaca.trading.requests import GetCalendarRequest

        return self._call("get_calendar", GetCalendarRequest(start=start, end=end))

    def submit_order(self, approved):
        if not isinstance(approved, ApprovedOrder):
            raise ValueError("Only a persisted risk-approved order may be submitted")
        if self.mode == "LIVE":
            if "PAPER_EXPERIMENT" in approved.decision.reasons:
                raise BrokerError("Paper experiments cannot be routed to LIVE")
            locks = self.settings.live_locks()
            readiness = self.readiness() if self.readiness else {"locked": True}
            if locks or readiness["locked"]:
                raise BrokerError("Live execution is locked")
            if approved.decision.notional > self.settings.live_max_order_notional:
                raise BrokerError("Live order notional cap exceeded")
        from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        c, d = approved.candidate, approved.decision
        # Broker execution always uses a bounded limit entry and native protective bracket.
        # Market and stop entries remain research fill models until their execution risk is qualified.
        req = LimitOrderRequest(
            symbol=c.symbol,
            qty=d.qty,
            side=OrderSide(c.side),
            time_in_force=TimeInForce.DAY,
            limit_price=d.entry_limit,
            order_class=OrderClass.BRACKET,
            extended_hours=False,
            take_profit=TakeProfitRequest(limit_price=round(c.target, 2)),
            stop_loss=StopLossRequest(stop_price=round(c.stop, 2)),
            client_order_id=approved.client_order_id,
        )
        return self._call("submit_order", order_data=req)

    def replace_order(self, order_id, **changes):
        # Replacement must not loosen stops or enlarge pending entries through this general boundary.
        if set(changes) != {"qty"} or changes["qty"] != 0:
            raise BrokerError("Order replacement requires a new risk review; use cancel and a new candidate")
        return self.cancel_order(order_id)

    def cancel_order(self, order_id):
        return self._call("cancel_order_by_id", order_id)

    def close_position(self, symbol):
        return self._call("close_position", symbol)

    def close_all_positions(self):
        return self._call("close_all_positions", cancel_orders=True)

    def stream_trade_updates(self, handler):
        from alpaca.trading.stream import TradingStream
        from aegis.tls import websocket_params

        key, secret = self.settings.credentials(self.mode)
        stream = TradingStream(key, secret, paper=self.mode == "PAPER", websocket_params=websocket_params())
        stream.subscribe_trade_updates(handler)
        return stream


class AlpacaPaperBroker(_AlpacaBroker):
    def __init__(self, settings, client=None, readiness=None):
        super().__init__(settings, "PAPER", client, readiness)


class AlpacaLiveBroker(_AlpacaBroker):
    def __init__(self, settings, client=None, readiness=None):
        super().__init__(settings, "LIVE", client, readiness)
