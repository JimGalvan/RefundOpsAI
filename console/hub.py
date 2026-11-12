"""In-memory fan-out of live investigation updates (log lines, evidence, status) and the latest browser frame.

The app runs as one process (one uvicorn worker), so a module-level hub is enough. Everything that matters is also
stored in the database, so a page that connects late replays history from there.
"""

import asyncio
from collections import defaultdict
from typing import Any


class Hub:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self.frames: dict[int, bytes] = {}
        self.running: set[int] = set()

    def subscribe(self, case_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[case_id].add(queue)
        return queue

    def unsubscribe(self, case_id: int, queue: asyncio.Queue) -> None:
        self._subscribers[case_id].discard(queue)

    def publish(self, case_id: int, name: str, payload: dict[str, Any]) -> None:
        for queue in list(self._subscribers[case_id]):
            queue.put_nowait((name, payload))


hub = Hub()
