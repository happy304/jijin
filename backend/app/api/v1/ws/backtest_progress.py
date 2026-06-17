"""WebSocket endpoint for backtest progress subscription.

Clients connect to ``/api/v1/backtests/{run_id}/progress`` to receive
real-time progress updates during backtest execution.

The endpoint:
1. Validates the run_id exists
2. Subscribes to the Redis pub/sub channel for that run
3. Pushes progress messages to the WebSocket client
4. Closes gracefully when the backtest completes or fails

Message format (JSON):
    {
        "run_id": 123,
        "progress": 45.5,
        "message": "回测进行中 (100/252)",
        "status": "running"
    }

Requirements: 7.4
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter()

# Redis key/channel patterns (must match app.tasks.backtest)
PROGRESS_KEY_PREFIX = "backtest:progress:"
PROGRESS_CHANNEL_PREFIX = "backtest:channel:"


@router.websocket("/backtests/{run_id}/progress")
async def backtest_progress_ws(websocket: WebSocket, run_id: int) -> None:
    """WebSocket endpoint for subscribing to backtest progress.

    Reads progress from Redis pub/sub and pushes to the connected client.
    Falls back to polling the Redis key if pub/sub is not available.
    """
    await websocket.accept()

    try:
        # Try to use Redis pub/sub for real-time updates
        await _stream_progress_pubsub(websocket, run_id)
    except WebSocketDisconnect:
        log.info("backtest_ws.client_disconnected", run_id=run_id)
    except Exception as e:
        log.warning("backtest_ws.error", run_id=run_id, error=str(e))
        try:
            await websocket.close(code=1011, reason=str(e)[:120])
        except Exception:
            pass


async def _stream_progress_pubsub(websocket: WebSocket, run_id: int) -> None:
    """Stream progress using Redis pub/sub.

    Subscribes to the backtest channel and forwards messages to the
    WebSocket client. Also sends the current state on connect.
    """
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    settings = get_settings()
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

    try:
        # Send current progress state on connect (if available)
        progress_key = f"{PROGRESS_KEY_PREFIX}{run_id}"
        current = await redis_client.get(progress_key)
        if current:
            await websocket.send_text(current)
            # Check if already completed
            data = json.loads(current)
            if data.get("status") in ("done", "failed"):
                await websocket.close(code=1000)
                return

        # Subscribe to the channel
        channel_key = f"{PROGRESS_CHANNEL_PREFIX}{run_id}"
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel_key)

        # Listen for messages with a timeout
        timeout_seconds = 3600  # 1 hour max connection
        end_time = asyncio.get_event_loop().time() + timeout_seconds

        while asyncio.get_event_loop().time() < end_time:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                # Send a ping/keepalive by checking current state
                current = await redis_client.get(progress_key)
                if current:
                    data = json.loads(current)
                    if data.get("status") in ("done", "failed"):
                        await websocket.send_text(current)
                        await websocket.close(code=1000)
                        return
                continue

            if message is None:
                continue

            if message["type"] == "message":
                payload = message["data"]
                await websocket.send_text(payload)

                # Check if this is a terminal message
                try:
                    data = json.loads(payload)
                    if data.get("status") in ("done", "failed"):
                        await websocket.close(code=1000)
                        return
                except (json.JSONDecodeError, TypeError):
                    pass

        # Timeout reached
        await websocket.close(code=1000, reason="timeout")

    finally:
        await redis_client.close()


async def _stream_progress_polling(websocket: WebSocket, run_id: int) -> None:
    """Fallback: stream progress by polling Redis key.

    Used when pub/sub is not available or as a simpler alternative.
    """
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    settings = get_settings()
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

    progress_key = f"{PROGRESS_KEY_PREFIX}{run_id}"
    last_payload = None

    try:
        for _ in range(7200):  # Max 1 hour at 0.5s intervals
            current = await redis_client.get(progress_key)

            if current and current != last_payload:
                await websocket.send_text(current)
                last_payload = current

                # Check terminal state
                try:
                    data = json.loads(current)
                    if data.get("status") in ("done", "failed"):
                        await websocket.close(code=1000)
                        return
                except (json.JSONDecodeError, TypeError):
                    pass

            await asyncio.sleep(0.5)

        await websocket.close(code=1000, reason="timeout")
    finally:
        await redis_client.close()
