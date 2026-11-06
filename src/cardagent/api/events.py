"""In-process event bus (per conversation) and SSE formatting, plus a rate limiter.

The graph runs in a worker thread; subscribers are asyncio queues in the web server's
event loop. `publish()` is thread-safe: it hands each event to the subscriber's own loop.

Scaling note (documented limitation): this bus lives in one process. Several API replicas
would need a shared broker (e.g. Redis pub/sub) so an auditor's approval on replica A reaches
a customer connected to replica B.
"""

import asyncio
import json
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

TERMINAL_EVENTS = {"final", "approval_required", "error"}


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]


def sse(event: Event) -> str:
    return f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(
            list
        )
        self._lock = threading.Lock()

    def subscribe(self, thread_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs[thread_id].append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs[thread_id] = [(lp, q) for lp, q in self._subs[thread_id] if q is not queue]

    def publish(self, thread_id: str, type_: str, data: dict[str, Any]) -> None:
        event = Event(type_, data)
        with self._lock:
            targets = list(self._subs.get(thread_id, []))
        for loop, queue in targets:
            loop.call_soon_threadsafe(queue.put_nowait, event)


class RateLimiter:
    """Sliding window: at most `limit` requests per `window_s` seconds per key."""

    def __init__(
        self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic
    ):
        self._limit, self._window, self._clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True
