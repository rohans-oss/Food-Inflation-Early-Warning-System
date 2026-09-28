"""Live-update fan-out.

Channels: "trip:{id}", "fleet:{org_id}", "mandi:{id}".
In-process asyncio queues by default. With REDIS_URL set, publishes go through Redis
pub/sub and every API process relays them to its own WebSocket clients, so several
uvicorn workers (or the worker container) all see the same stream.
"""
import asyncio
import json
import logging
from collections import defaultdict

from agripulse_api.config import get_settings

log = logging.getLogger("agripulse.hub")


class Hub:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._redis = None
        self._relay_task = None

    async def start(self):
        self._loop = asyncio.get_running_loop()
        url = get_settings().redis_url
        if url:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(url)
                await self._redis.ping()
                self._relay_task = asyncio.create_task(self._relay())
                log.info("hub using redis pub/sub")
            except Exception:
                log.exception("redis unavailable, falling back to in-process hub")
                self._redis = None

    async def stop(self):
        if self._relay_task:
            self._relay_task.cancel()
        if self._redis:
            await self._redis.aclose()
        self._redis, self._loop = None, None

    async def _relay(self):
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe("agripulse:*")
        async for msg in pubsub.listen():
            if msg.get("type") != "pmessage":
                continue
            channel = msg["channel"].decode().removeprefix("agripulse:")
            self._deliver(channel, json.loads(msg["data"]))

    def _deliver(self, channel: str, payload: dict):
        for q in list(self._subs.get(channel, ())):
            if q.full():
                try:
                    q.get_nowait()  # drop the oldest; live views only need the latest
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(payload)

    def subscribe(self, *channels: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        for c in channels:
            self._subs[c].add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        for subs in self._subs.values():
            subs.discard(q)

    def publish(self, channel: str, payload: dict):
        """Safe to call from sync code running in a worker thread."""
        if self._loop is None or self._loop.is_closed():
            return
        data = json.loads(json.dumps(payload, default=str))
        if self._redis is not None:
            asyncio.run_coroutine_threadsafe(self._redis.publish(f"agripulse:{channel}", json.dumps(data)), self._loop)
        else:
            self._loop.call_soon_threadsafe(self._deliver, channel, data)


hub = Hub()
