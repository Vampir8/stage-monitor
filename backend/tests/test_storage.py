import asyncio
import sqlite3

import pytest

from stage_monitor.contracts import CommandRequest, CommandResult, Metric
from stage_monitor.events import EventBus
from stage_monitor.storage import Storage


def test_command_outbox_transaction_and_recovery(tmp_path):
    async def exercise():
        storage = Storage(tmp_path / "test.sqlite3")
        await storage.open()
        try:
            bus = EventBus()
            subscriber = bus.subscribe()
            result = CommandResult(device_id="device", actor_id="operator",
                                   request=CommandRequest(command="mute"), status="running")
            event = bus.event("device.command.completed", result.model_dump(mode="json"), "device")
            await storage.write("""CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox
                                BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
            with pytest.raises(sqlite3.IntegrityError):
                await storage.save_command(result, event, initial=True)
            assert await storage.get_command(result.command_id) is None
            assert await storage.rows("SELECT * FROM audit") == []
            await storage.write("DROP TRIGGER fail_outbox")
            await storage.save_command(result, event)
            assert subscriber.queue.empty()
            await bus.deliver_outbox(storage)
            assert (await subscriber.queue.get()).id == event.id
            await bus.deliver_outbox(storage)
            assert subscriber.queue.empty()
            await storage.recover_commands()
            assert (await storage.get_command(result.command_id)).status == "unknown"
        finally:
            await storage.close()
    asyncio.run(exercise())


def test_sampling_is_per_channel_and_keeps_zero(tmp_path):
    async def exercise():
        storage = Storage(tmp_path / "sample.sqlite3")
        await storage.open()
        try:
            samples = [
                Metric(device_id="d", key="level", channel="1", value=0),
                Metric(device_id="d", key="level", channel="2", value=10),
                Metric(device_id="d", key="level", channel="3", value=None, quality="unknown"),
                Metric(device_id="d", key="level", channel="4", value=10, quality="stale"),
            ]
            await storage.sample(samples, 1000, {})
            await storage.sample(samples, 1000, {})
            rows = await storage.rows("SELECT data FROM metrics")
            assert len(rows) == 2
            assert Metric.model_validate_json(rows[0]["data"]).value == 0
            await storage.sample(samples, 1000, {"level": 0})
            assert len(await storage.rows("SELECT data FROM metrics")) == 4
        finally:
            await storage.close()
    asyncio.run(exercise())
