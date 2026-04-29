import asyncio
import unittest

import signaling.server as signaling_server
from services.stt_service import STTService
from transport.webrtc.manager import RoomManager


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class SignalingServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        signaling_server.manager = RoomManager()
        signaling_server.stt_service = STTService()

    async def test_join_room_and_leave_room_flow(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")

        await signaling_server._handle_message(
            participant,
            {"type": "join_room", "room_id": "room-a", "name": "Alice"},
        )
        await signaling_server._handle_message(participant, {"type": "leave_room"})

        self.assertEqual(ws.messages[0]["type"], "joined_room")
        self.assertEqual(ws.messages[0]["room"]["room_id"], "room-a")
        self.assertEqual(ws.messages[1]["type"], "left_room")
        self.assertEqual(ws.messages[1]["room_id"], "room-a")
        self.assertTrue(ws.messages[1]["room_deleted"])

    async def test_relays_webrtc_offer_between_participants(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-b"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-b"})

        await signaling_server._handle_message(
            alice,
            {
                "type": "webrtc_offer",
                "target_participant_id": bob.participant_id,
                "sdp": {"type": "offer", "sdp": "fake-offer"},
            },
        )
        await asyncio.sleep(0)

        relayed = ws_bob.messages[-1]
        self.assertEqual(relayed["type"], "webrtc_offer")
        self.assertEqual(relayed["from_participant_id"], alice.participant_id)
        self.assertEqual(relayed["sdp"]["sdp"], "fake-offer")

    async def test_rejects_relay_when_target_not_in_same_room(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-c"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-d"})

        await signaling_server._handle_message(
            alice,
            {
                "type": "ice_candidate",
                "target_participant_id": bob.participant_id,
                "candidate": {"candidate": "fake-candidate"},
            },
        )

        error_message = ws_alice.messages[-1]
        self.assertEqual(error_message["type"], "error")
        self.assertEqual(error_message["code"], "target_not_in_room")

    async def test_participant_activity_broadcasts_speaking_events(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-speaking"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-speaking"})
        ws_alice.messages.clear()
        ws_bob.messages.clear()

        await signaling_server._handle_message(
            alice,
            {"type": "participant_activity", "activity": "speaking_started"},
        )
        await asyncio.sleep(0)

        started_event = ws_bob.messages[-1]
        self.assertEqual(started_event["type"], "room_event")
        self.assertEqual(
            started_event["event"]["event_type"],
            "participant_speaking_started",
        )
        self.assertEqual(started_event["event"]["participant_id"], alice.participant_id)

        await signaling_server._handle_message(
            alice,
            {"type": "participant_activity", "activity": "speaking_stopped"},
        )
        await asyncio.sleep(0)

        stopped_event = ws_bob.messages[-1]
        self.assertEqual(stopped_event["type"], "room_event")
        self.assertEqual(
            stopped_event["event"]["event_type"],
            "participant_speaking_stopped",
        )
        self.assertEqual(stopped_event["event"]["participant_id"], alice.participant_id)

    async def test_rejects_invalid_participant_activity(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        await signaling_server._handle_message(participant, {"type": "join_room", "room_id": "room-a"})
        ws.messages.clear()

        await signaling_server._handle_message(
            participant,
            {"type": "participant_activity", "activity": "invalid"},
        )

        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "invalid_activity")

    async def test_stt_transcript_broadcasts_transcript_event(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-stt"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-stt"})
        ws_alice.messages.clear()
        ws_bob.messages.clear()

        await signaling_server._handle_message(
            alice,
            {
                "type": "stt_transcript",
                "text": "hello from alice",
                "is_final": False,
                "source": "browser_web_speech",
            },
        )
        await asyncio.sleep(0)

        self.assertTrue(ws_bob.messages)
        event = ws_bob.messages[-1]
        self.assertEqual(event["type"], "room_event")
        self.assertEqual(event["event"]["event_type"], "participant_transcript")
        self.assertEqual(event["event"]["participant_id"], alice.participant_id)
        self.assertEqual(event["event"]["transcript_text"], "hello from alice")
        self.assertFalse(event["event"]["transcript_is_final"])

    async def test_stt_transcript_deduplicates_repeated_interim_updates(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-stt-dedupe"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-stt-dedupe"})

        ws_bob.messages.clear()

        payload = {
            "type": "stt_transcript",
            "text": "same interim",
            "is_final": False,
            "source": "browser_web_speech",
        }
        await signaling_server._handle_message(alice, payload)
        await signaling_server._handle_message(alice, payload)
        await asyncio.sleep(0)

        transcript_events = [
            msg
            for msg in ws_bob.messages
            if msg.get("type") == "room_event"
            and msg.get("event", {}).get("event_type") == "participant_transcript"
        ]
        self.assertEqual(len(transcript_events), 1)

    async def test_rejects_invalid_stt_payload(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        await signaling_server._handle_message(participant, {"type": "join_room", "room_id": "room-stt-error"})
        ws.messages.clear()

        await signaling_server._handle_message(
            participant,
            {"type": "stt_transcript", "text": 123, "is_final": False},
        )

        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "invalid_stt_payload")


if __name__ == "__main__":
    unittest.main()
