import asyncio
import unittest

from participants.base import ParticipantRole, ParticipantState
from room.manager import RoomEventType, RoomManager


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


if __name__ == "__main__":
    unittest.main()
