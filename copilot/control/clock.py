from __future__ import annotations

from datetime import UTC, datetime, timedelta


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Injected clock for deterministic expiry tests."""

    def __init__(self, start: datetime | None = None):
        self._t = start or datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._t

    def advance(self, **kw) -> None:
        self._t += timedelta(**kw)
