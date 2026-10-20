import asyncio
import unittest

from participants.base import ParticipantRole, ParticipantState
from room.manager import RoomEventType, RoomManager
from room.model import ParticipantEvictedError


class RoomManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RoomManager(event_history_size=50)
        self.events: list[dict] = []
        self.manager.add_event_listener(self.events.append)

    def test_unique_ids_for_room_local_and_remote_participants(self) -> None:
        local = self.manager.register_participant(websocket=object(), name="Alice")
        remote = self.manager.register_remote_participant(name="Bob")
        room = self.manager.get_or_create_room(room_id=None, creator_id=local.participant_id)

        self.manager.join_room(local, room.room_id)
        self.manager.join_room(remote, room.room_id)

        self.assertTrue(room.room_id.startswith("r-"))
        self.assertNotEqual(local.participant_id, remote.participant_id)
        self.assertTrue(local.participant_id.startswith("p-"))
        self.assertTrue(remote.participant_id.startswith("p-"))
        self.assertEqual(local.role, ParticipantRole.LOCAL)
        self.assertEqual(remote.role, ParticipantRole.REMOTE)
        self.assertEqual(local.state, ParticipantState.IN_ROOM)
        self.assertEqual(remote.state, ParticipantState.IN_ROOM)

    def test_register_agent_participant_sets_agent_role(self) -> None:
        agent = self.manager.register_agent_participant(name="Helper")

        self.assertTrue(agent.participant_id.startswith("p-"))
        self.assertEqual(agent.role, ParticipantRole.AGENT)
        self.assertEqual(agent.name, "Helper")
        self.assertEqual(agent.state, ParticipantState.CONNECTING)

    def test_destroy_room_removes_room_regardless_of_occupancy(self) -> None:
        participant = self.manager.register_participant(websocket=object(), name="Alice")
        room = self.manager.get_or_create_room(room_id="doomed", creator_id=participant.participant_id)
        self.manager.join_room(participant, room.room_id)

        self.assertTrue(self.manager.destroy_room("doomed"))
        self.assertIsNone(self.manager.get_room("doomed"))
        self.assertFalse(self.manager.destroy_room("doomed"))

    def test_announces_join_and_leave_events(self) -> None:
        participant = self.manager.register_participant(websocket=object(), name="Alice")
        room = self.manager.join_room(participant, "room-events")
        self.manager.leave_room(participant)

        join_event = next(
            event for event in self.events if event["event_type"] == RoomEventType.PARTICIPANT_JOINED.value
        )
        leave_event = next(
            event for event in self.events if event["event_type"] == RoomEventType.PARTICIPANT_LEFT.value
        )

        self.assertEqual(join_event["room_id"], room.room_id)
        self.assertEqual(join_event["participant_id"], participant.participant_id)
        self.assertEqual(leave_event["room_id"], room.room_id)
        self.assertEqual(leave_event["participant_id"], participant.participant_id)

    def test_announces_speaking_events_only_on_state_change(self) -> None:
        participant = self.manager.register_participant(websocket=object(), name="Alice")
        self.manager.join_room(participant, "room-speaking")

        self.manager.update_participant_speaking(participant, speaking=True)
        self.manager.update_participant_speaking(participant, speaking=True)
        self.manager.update_participant_speaking(participant, speaking=False)

        speaking_events = [
            event
            for event in self.events
            if event["event_type"]
            in {
                RoomEventType.PARTICIPANT_SPEAKING_STARTED.value,
                RoomEventType.PARTICIPANT_SPEAKING_STOPPED.value,
            }
        ]
        self.assertEqual(len(speaking_events), 2)
        self.assertEqual(
            speaking_events[0]["event_type"],
            RoomEventType.PARTICIPANT_SPEAKING_STARTED.value,
        )
        self.assertEqual(
            speaking_events[1]["event_type"],
            RoomEventType.PARTICIPANT_SPEAKING_STOPPED.value,
        )

    def test_announces_transcript_events(self) -> None:
        participant = self.manager.register_participant(websocket=object(), name="Alice")
        self.manager.join_room(participant, "room-transcript")

        self.manager.emit_participant_transcript(
            participant,
            transcript_text="hello team",
            transcript_is_final=False,
            transcript_source="browser_web_speech",
        )

        transcript_event = next(
            event
            for event in self.events
            if event["event_type"] == RoomEventType.PARTICIPANT_TRANSCRIPT.value
        )
        self.assertEqual(transcript_event["participant_id"], participant.participant_id)
        self.assertEqual(transcript_event["transcript_text"], "hello team")
        self.assertFalse(transcript_event["transcript_is_final"])

    def test_disconnect_removes_participant_and_destroys_empty_room(self) -> None:
        websocket = object()
        participant = self.manager.register_participant(websocket=websocket, name="Alice")
        self.manager.join_room(participant, "room-disconnect")

        removed = self.manager.unregister_participant(websocket)

        self.assertIsNotNone(removed)
        self.assertEqual(removed.state, ParticipantState.DISCONNECTED)
        self.assertIsNone(self.manager.get_room("room-disconnect"))

        event_types = [event["event_type"] for event in self.events]
        self.assertIn(RoomEventType.PARTICIPANT_LEFT.value, event_types)
        self.assertIn(RoomEventType.ROOM_DESTROYED.value, event_types)

    def test_destroy_room_evicts_remaining_participants(self) -> None:
        alice = self.manager.register_participant(websocket=object(), name="Alice")
        bob = self.manager.register_participant(websocket=object(), name="Bob")
        self.manager.join_room(alice, "doomed")
        self.manager.join_room(bob, "doomed")

        self.manager.destroy_room("doomed")

        # Nobody may keep pointing at a room that no longer exists.
        self.assertIsNone(alice.room_id)
        self.assertIsNone(bob.room_id)
        self.assertEqual(alice.state, ParticipantState.CONNECTING)
        self.assertEqual(bob.state, ParticipantState.CONNECTING)

    def test_leave_room_clears_state_when_last_member_leaves(self) -> None:
        participant = self.manager.register_participant(websocket=object(), name="Alice")
        self.manager.join_room(participant, "solo")

        self.manager.leave_room(participant)

        self.assertIsNone(participant.room_id)
        self.assertEqual(participant.state, ParticipantState.CONNECTING)

    def test_leave_room_clears_stale_room_id_when_room_is_gone(self) -> None:
        participant = self.manager.register_participant(websocket=object(), name="Alice")
        self.manager.join_room(participant, "vanished")
        # Simulate the room disappearing without the participant being removed.
        self.manager._rooms.pop("vanished")

        self.manager.leave_room(participant)

        self.assertIsNone(participant.room_id)
        self.assertEqual(participant.state, ParticipantState.CONNECTING)

    def test_evict_records_participant_on_the_room(self) -> None:
        alice = self.manager.register_participant(websocket=object(), name="Alice")
        bob = self.manager.register_participant(websocket=object(), name="Bob")
        self.manager.join_room(alice, "kick-room")
        self.manager.join_room(bob, "kick-room")

        self.manager.leave_room(alice, evict=True)

        room = self.manager.get_room("kick-room")
        self.assertIsNotNone(room)
        self.assertIn(alice.participant_id, room.evicted_participant_ids)
        with self.assertRaises(ParticipantEvictedError):
            self.manager.join_room(alice, "kick-room")


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class RoomManagerBroadcastTests(unittest.IsolatedAsyncioTestCase):
    async def test_broadcasts_join_and_leave_to_other_participants(self) -> None:
        manager = RoomManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        ws3 = FakeWebSocket()

        p1 = manager.register_participant(websocket=ws1, name="Alice")
        p2 = manager.register_participant(websocket=ws2, name="Bob")
        p3 = manager.register_participant(websocket=ws3, name="Carol")

        manager.join_room(p1, "room-broadcast")
        await asyncio.sleep(0)
        self.assertEqual(ws1.messages, [])

        manager.join_room(p2, "room-broadcast")
        await asyncio.sleep(0)
        self.assertTrue(ws1.messages)
        self.assertEqual(ws1.messages[-1]["event"]["event_type"], RoomEventType.PARTICIPANT_JOINED.value)
        self.assertEqual(ws1.messages[-1]["event"]["participant_id"], p2.participant_id)
        self.assertEqual(ws2.messages, [])

        manager.join_room(p3, "room-broadcast")
        await asyncio.sleep(0)
        self.assertEqual(ws1.messages[-1]["event"]["participant_id"], p3.participant_id)
        self.assertEqual(ws2.messages[-1]["event"]["participant_id"], p3.participant_id)
        self.assertEqual(ws3.messages, [])

        manager.leave_room(p3)
        await asyncio.sleep(0)
        self.assertEqual(ws1.messages[-1]["event"]["event_type"], RoomEventType.PARTICIPANT_LEFT.value)
        self.assertEqual(ws2.messages[-1]["event"]["event_type"], RoomEventType.PARTICIPANT_LEFT.value)
        self.assertEqual(ws1.messages[-1]["event"]["participant_id"], p3.participant_id)
        self.assertEqual(ws2.messages[-1]["event"]["participant_id"], p3.participant_id)
        self.assertEqual(ws3.messages, [])

    async def test_disconnect_broadcasts_leave_to_remaining_participants(self) -> None:
        manager = RoomManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()

        p1 = manager.register_participant(websocket=ws1, name="Alice")
        p2 = manager.register_participant(websocket=ws2, name="Bob")
        manager.join_room(p1, "room-disconnect-broadcast")
        manager.join_room(p2, "room-disconnect-broadcast")
        await asyncio.sleep(0)

        manager.unregister_participant(ws2)
        await asyncio.sleep(0)

        self.assertTrue(ws1.messages)
        self.assertEqual(ws1.messages[-1]["event"]["event_type"], RoomEventType.PARTICIPANT_LEFT.value)
        self.assertEqual(ws1.messages[-1]["event"]["participant_id"], p2.participant_id)

    async def test_broadcasts_speaking_events_to_other_participants(self) -> None:
        manager = RoomManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()

        p1 = manager.register_participant(websocket=ws1, name="Alice")
        p2 = manager.register_participant(websocket=ws2, name="Bob")
        manager.join_room(p1, "room-speaking-broadcast")
        manager.join_room(p2, "room-speaking-broadcast")
        await asyncio.sleep(0)

        ws1.messages.clear()
        ws2.messages.clear()

        manager.update_participant_speaking(p2, speaking=True)
        await asyncio.sleep(0)

        self.assertTrue(ws1.messages)
        self.assertEqual(
            ws1.messages[-1]["event"]["event_type"],
            RoomEventType.PARTICIPANT_SPEAKING_STARTED.value,
        )
        self.assertEqual(ws1.messages[-1]["event"]["participant_id"], p2.participant_id)
        self.assertEqual(ws2.messages, [])

        manager.update_participant_speaking(p2, speaking=False)
        await asyncio.sleep(0)

        self.assertEqual(
            ws1.messages[-1]["event"]["event_type"],
            RoomEventType.PARTICIPANT_SPEAKING_STOPPED.value,
        )
        self.assertEqual(ws1.messages[-1]["event"]["participant_id"], p2.participant_id)

    async def test_broadcasts_transcript_events_to_other_participants(self) -> None:
        manager = RoomManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()

        p1 = manager.register_participant(websocket=ws1, name="Alice")
        p2 = manager.register_participant(websocket=ws2, name="Bob")
        manager.join_room(p1, "room-transcript-broadcast")
        manager.join_room(p2, "room-transcript-broadcast")
        await asyncio.sleep(0)

        ws1.messages.clear()
        ws2.messages.clear()

        manager.emit_participant_transcript(
            p2,
            transcript_text="draft sentence",
            transcript_is_final=False,
            transcript_source="browser_web_speech",
        )
        await asyncio.sleep(0)

        self.assertTrue(ws1.messages)
        event_payload = ws1.messages[-1]["event"]
        self.assertEqual(event_payload["event_type"], RoomEventType.PARTICIPANT_TRANSCRIPT.value)
        self.assertEqual(event_payload["transcript_text"], "draft sentence")
        self.assertFalse(event_payload["transcript_is_final"])
        self.assertEqual(ws2.messages, [])

    async def test_awaits_futures_returned_by_send_json(self) -> None:
        manager = RoomManager()
        delivered: list[dict] = []

        class FutureWebSocket:
            def send_json(self, payload: dict) -> asyncio.Future:
                future: asyncio.Future = asyncio.get_running_loop().create_future()
                future.set_result(None)
                delivered.append(payload)
                return future

        participant = manager.register_participant(websocket=FutureWebSocket(), name="Alice")
        await manager._send_to_websocket(participant.websocket, {"type": "ping"})

        self.assertEqual(delivered, [{"type": "ping"}])

    async def test_schedules_async_listeners_that_return_futures(self) -> None:
        manager = RoomManager()
        seen: list[dict] = []

        def listener(payload: dict) -> asyncio.Future:
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            future.set_result(None)
            seen.append(payload)
            return future

        manager.add_event_listener(listener)
        participant = manager.register_participant(websocket=object(), name="Alice")
        manager.join_room(participant, "room-listener")
        await asyncio.sleep(0)

        self.assertTrue(seen)
        self.assertEqual(seen[0]["event_type"], RoomEventType.ROOM_CREATED.value)

    async def test_background_tasks_are_tracked_and_drained_on_shutdown(self) -> None:
        manager = RoomManager()
        release = asyncio.Event()

        class SlowWebSocket:
            def __init__(self) -> None:
                self.messages: list[dict] = []

            async def send_json(self, payload: dict) -> None:
                await release.wait()
                self.messages.append(payload)

        slow = SlowWebSocket()
        alice = manager.register_participant(websocket=slow, name="Alice")
        bob = manager.register_participant(websocket=object(), name="Bob")
        manager.join_room(alice, "room-shutdown")
        manager.join_room(bob, "room-shutdown")

        await asyncio.sleep(0)
        self.assertTrue(manager._background_tasks)

        await manager.shutdown()
        self.assertEqual(manager._background_tasks, set())

    async def test_evicted_participant_is_notified(self) -> None:
        manager = RoomManager()
        ws = FakeWebSocket()
        alice = manager.register_participant(websocket=ws, name="Alice")
        bob = manager.register_participant(websocket=object(), name="Bob")
        manager.join_room(alice, "room-evict")
        manager.join_room(bob, "room-evict")
        await asyncio.sleep(0)
        ws.messages.clear()

        manager.leave_room(alice, evict=True)
        await asyncio.sleep(0)

        self.assertTrue(ws.messages)
        self.assertEqual(ws.messages[-1]["type"], "left_room")
        self.assertEqual(ws.messages[-1]["reason"], "evicted")
        self.assertTrue(ws.messages[-1]["room_exists"])
        self.assertFalse(ws.messages[-1]["room_deleted"])

    async def test_last_evicted_participant_is_notified_that_the_room_was_deleted(self) -> None:
        manager = RoomManager()
        ws = FakeWebSocket()
        alice = manager.register_participant(websocket=ws, name="Alice")
        manager.join_room(alice, "room-evict-last")
        await asyncio.sleep(0)
        ws.messages.clear()

        manager.leave_room(alice, evict=True)
        await asyncio.sleep(0)

        self.assertTrue(ws.messages)
        self.assertEqual(ws.messages[-1]["type"], "left_room")
        self.assertEqual(ws.messages[-1]["reason"], "evicted")
        self.assertTrue(ws.messages[-1]["room_deleted"])


if __name__ == "__main__":
    unittest.main()
