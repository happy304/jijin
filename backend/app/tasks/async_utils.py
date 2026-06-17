"""Shared async utilities for Celery tasks.

Provides a thread-safe event loop runner that reuses loops per-thread,
enabling httpx connection pool reuse across multiple async calls within
the same Celery task invocation.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

_thread_local = threading.local()


def run_async(coro: Any) -> Any:
    """Run an async coroutine from a synchronous Celery task.

    Celery tasks are synchronous by default. This helper reuses a
    per-thread event loop to allow httpx connection pool reuse across
    multiple provider calls within the same task invocation.

    On error, the loop is closed and discarded to avoid stale state
    propagating to subsequent calls.

    Usage::

        from app.tasks.async_utils import run_async

        @celery_app.task(...)
        def my_task():
            return run_async(_my_async_impl())
    """
    loop = getattr(_thread_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _thread_local.loop = loop

    try:
        return loop.run_until_complete(coro)
    except Exception:
        # On error, close and discard the loop to avoid stale state
        loop.close()
        _thread_local.loop = None
        raise


__all__ = ["run_async"]
