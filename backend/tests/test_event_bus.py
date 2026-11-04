import asyncio

from stage_monitor.events import EventBus


def test_slow_consumer_does_not_block_other_consumers():
    async def exercise():
        bus = EventBus()
        slow = bus.subscribe()
        fast = bus.subscribe()
        for index in range(140):
            event = bus.event("device.state", {"revision": index}, "device-1")
            bus.publish(event)
            assert (await fast.queue.get()).payload["revision"] == index
        assert slow.overflow.is_set()
        assert slow not in bus.subscribers
        assert fast in bus.subscribers
    asyncio.run(exercise())


def test_subscription_filter_runs_before_queue_capacity():
    bus = EventBus()
    subscriber = bus.subscribe(lambda event: event.device_id == "watched")
    for index in range(1000):
        bus.publish(bus.event("device.state", {"revision": index}, "other"))
    assert subscriber.queue.empty()
    assert not subscriber.overflow.is_set()
    bus.publish(bus.event("device.state", {"revision": 1}, "watched"))
    assert subscriber.queue.qsize() == 1


def test_large_metric_burst_is_ordered_and_still_bounded():
    bus = EventBus()
    subscriber = bus.subscribe(queue_size=1024)
    for index in range(500):
        bus.publish(bus.event('device.metric', {'index': index}, 'switch'))
    assert not subscriber.overflow.is_set()
    for index in range(500):
        assert subscriber.queue.get_nowait().payload['index'] == index
    for index in range(1025):
        bus.publish(bus.event('device.metric', {'index': index}, 'switch'))
    assert subscriber.queue.qsize() == 1024
    assert subscriber.overflow.is_set()
    assert subscriber not in bus.subscribers
