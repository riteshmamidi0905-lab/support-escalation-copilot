"""Mock Status, Carrier and Ticketing APIs with fault injection, and the guarded client the workflow calls them through (bounded retries + a small circuit breaker).
Every API call carries the account id taken from the trusted scope; the mocks answer only for that account's objects (404 otherwise: no cross-tenant oracle)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from copilot.control.mocks import CallTimeout, FaultScript, PermanentError, TransientError


class NotFound(Exception):
    pass


class ExternalUnavailable(Exception):
    def __init__(self, api: str, code: str):
        super().__init__(f"{api}: {code}")
        self.api, self.code = api, code


class _Api:
    name = "api"

    def __init__(self, faults: FaultScript | None = None):
        self.faults, self.calls = faults or FaultScript(), 0

    def _fault(self) -> None:
        self.calls += 1
        o = self.faults.next()
        if o == "transient":
            raise TransientError("HTTP 500")
        if o == "permanent":
            raise PermanentError("HTTP 422")
        if o in ("timeout_before_effect", "timeout_after_effect"):
            raise CallTimeout("timeout")


class StatusAPI(_Api):
    name = "status_api"

    def __init__(self, integrations: list[dict[str, Any]], faults: FaultScript | None = None):
        super().__init__(faults)
        self._by = {i["integration_id"]: i for i in integrations}

    def get_integration_status(self, account_id: str, integration_id: str) -> dict[str, Any]:
        self._fault()
        i = self._by.get(integration_id)
        if i is None or i["account_id"] != account_id:
            raise NotFound(integration_id)
        return {"integration_id": integration_id, "status": i["status"], "last_sync_at": i["last_sync_at"], "source": "status_api"}


class CarrierAPI(_Api):
    name = "carrier_api"

    def __init__(self, integrations: list[dict[str, Any]], faults: FaultScript | None = None):
        super().__init__(faults)
        self._by = {i["integration_id"]: i for i in integrations}

    def get_feed_state(self, account_id: str, integration_id: str) -> dict[str, Any]:
        self._fault()
        i = self._by.get(integration_id)
        if i is None or i["account_id"] != account_id:
            raise NotFound(integration_id)
        return {"integration_id": integration_id, "provider": i["provider"], "reachable": i["status"] != "paused", "source": "carrier_api"}


class TicketingAPI(_Api):
    name = "ticketing_api"

    def __init__(self, tickets: list[dict[str, Any]], faults: FaultScript | None = None):
        super().__init__(faults)
        self._by = {t["ticket_id"]: t for t in tickets}

    def get_ticket(self, account_id: str, ticket_id: str) -> dict[str, Any]:
        self._fault()
        t = self._by.get(ticket_id)
        if t is None or t["account_id"] != account_id:
            raise NotFound(ticket_id)
        return {k: t[k] for k in ("ticket_id", "account_id", "created_at", "product_area", "severity", "subject", "body")} | {"history": t["history"]}


class GuardedClient:
    """Bounded retries on transient failures/timeouts, a permanent failure is not retried, and N consecutive failed calls open a breaker that fails fast."""

    def __init__(self, attempts: int = 3, breaker_after: int = 6, sleep: Callable[[float], None] = lambda s: None, clock=None, cooldown_s: float = 30.0):
        self.attempts, self.breaker_after, self.sleep, self.clock, self.cooldown_s = attempts, breaker_after, sleep, clock, cooldown_s
        self._consecutive: dict[str, int] = {}
        self._opened_at: dict[str, Any] = {}

    def call(self, api: _Api, fn: Callable[..., Any], *args: Any) -> Any:
        if self._consecutive.get(api.name, 0) >= self.breaker_after:
            t0 = self._opened_at.get(api.name)
            if self.clock is None or t0 is None or (self.clock.now() - t0).total_seconds() < self.cooldown_s:
                raise ExternalUnavailable(api.name, "BREAKER_OPEN")
            self._consecutive[api.name] = self.breaker_after - 1             # half-open: one trial call; a failure re-opens the breaker at once
        last = "UNAVAILABLE"
        for i in range(self.attempts):
            try:
                out = fn(*args)
                self._consecutive[api.name] = 0
                return out
            except NotFound:
                self._consecutive[api.name] = 0
                raise
            except PermanentError:
                self._consecutive[api.name] = self._consecutive.get(api.name, 0) + 1
                if self._consecutive[api.name] >= self.breaker_after and self.clock is not None:
                    self._opened_at[api.name] = self.clock.now()
                raise ExternalUnavailable(api.name, "REJECTED") from None
            except (TransientError, CallTimeout) as e:
                last = "TIMEOUT" if isinstance(e, CallTimeout) else "SERVER_ERROR"
                self._consecutive[api.name] = self._consecutive.get(api.name, 0) + 1
                if self._consecutive[api.name] >= self.breaker_after and self.clock is not None:
                    self._opened_at[api.name] = self.clock.now()
                self.sleep(0.05 * 2 ** i)
        raise ExternalUnavailable(api.name, last + "_RETRIES_EXHAUSTED")
