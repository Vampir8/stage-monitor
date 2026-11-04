import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .contracts import Event, new_id


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))
    overflow: asyncio.Event = field(default_factory=asyncio.Event)
    predicate: Callable[[Event], bool] | None = None


class EventBus:
    def __init__(self):
        self.subscribers: set[Subscriber] = set()
        self.sequences = defaultdict(int)
        self.epoch = new_id()
        self.recorder = None
        self.durable_recorder = None
        self.recording_error = False

    def event(self, event_type, payload, device_id=None, actor_id=None, correlation_id=None):
        stream = f"{self.epoch}:{device_id or 'system'}"
        self.sequences[stream] += 1
        return Event(
            type=event_type, source="core", device_id=device_id, actor_id=actor_id,
            correlation_id=correlation_id, stream_id=stream,
            sequence=self.sequences[stream], payload=payload,
        )

    def subscribe(self, predicate=None, *, queue_size=128):
        if type(queue_size) is not int or not 1 <= queue_size <= 4096:
            raise ValueError('Invalid subscriber queue size')
        subscriber = Subscriber(predicate=predicate, queue=asyncio.Queue(maxsize=queue_size))
        self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber):
        self.subscribers.discard(subscriber)

    def publish(self, event: Event):
        if self.recorder:
            try:
                self.recorder(event)
            except Exception:
                self.recording_error = True
                logging.getLogger(__name__).exception("Timeline projection failed; event delivery continues")
        for subscriber in tuple(self.subscribers):
            try:
                if subscriber.predicate and not subscriber.predicate(event):
                    continue
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Explicitly force resynchronization instead of silently losing durable events.
                subscriber.overflow.set()
                self.unsubscribe(subscriber)
            except Exception:
                subscriber.overflow.set()
                self.unsubscribe(subscriber)

    async def deliver_outbox(self, storage):
        for row in await storage.rows("SELECT id,data FROM outbox WHERE delivered=0 ORDER BY rowid LIMIT 100"):
            event = Event.model_validate_json(row["data"])
            if self.durable_recorder:
                await self.durable_recorder(event)
            self.publish(event)
            await storage.write("UPDATE outbox SET delivered=1 WHERE id=?", (row["id"],))
