from __future__ import annotations

import sys
from types import SimpleNamespace

from app.services.runtime_health import check_queue_health


class FakeRedisClient:
    def __init__(self, lengths: dict[str, int] | None = None) -> None:
        self.lengths = lengths or {}
        self.closed = False

    def ping(self) -> bool:
        return True

    def llen(self, queue: str) -> int:
        return self.lengths.get(queue, 0)

    def close(self) -> None:
        self.closed = True


class FakeRedisFactory:
    def __init__(self, client: FakeRedisClient) -> None:
        self.client = client

    def from_url(self, *_args, **_kwargs) -> FakeRedisClient:
        return self.client


def test_check_queue_health_healthy(monkeypatch) -> None:
    client = FakeRedisClient({"ingest": 1, "backtest": 0, "ai": 0, "notify": 0})
    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=FakeRedisFactory(client)))

    health = check_queue_health(("ingest", "backtest", "ai", "notify"))

    assert health.status == "healthy"
    assert health.redis_available is True
    assert health.queues["ingest"] == 1


def test_check_queue_health_unavailable(monkeypatch) -> None:
    class BrokenRedisFactory:
        def from_url(self, *_args, **_kwargs):
            raise RuntimeError("redis down")

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=BrokenRedisFactory()))

    health = check_queue_health(("backtest",))

    assert health.status == "unavailable"
    assert health.redis_available is False
    assert health.warnings
