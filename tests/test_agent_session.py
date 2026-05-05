import asyncio
import unittest

from room.control import RoomControlModule
from room.manager import RoomManager
from rtc_room_framework.agent import AgentSession, EchoTranscriptResponder


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


def _agent_transcript_messages(messages: list[dict], *, agent_participant_id: str) -> list[dict]:
    return [
        message
        for message in messages
        if message.get("type") == "room_event"
        and message.get("event", {}).get("event_type") == "participant_transcript"
        and message.get("event", {}).get("participant_id") == agent_participant_id
    ]


class AgentSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_session_joins_room_and_replies_to_final_transcripts(self) -> None:
        manager = RoomManager()
        room_control = RoomControlModule(manager)
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = manager.register_participant(ws_alice, name="Alice")
        bob = manager.register_participant(ws_bob, name="Bob")
        room_control.join_participant(alice, room_name="agent-room", create_if_missing=True)
        room_control.join_participant(bob, room_name="agent-room", create_if_missing=False)
        await asyncio.sleep(0)

        agent = AgentSession(
            manager,
            room_control=room_control,
            name="Room Agent",
            responder=EchoTranscriptResponder(prefix="Ack: "),
        )
        participant = await agent.start("agent-room", create_if_missing=False)
        self.assertEqual(participant.role.value, "agent")
        self.assertEqual(participant.room_id, "agent-room")

        ws_alice.messages.clear()
        ws_bob.messages.clear()

        manager.emit_participant_transcript(
            alice,
            transcript_text="hello team",
            transcript_is_final=True,
            transcript_source="browser_web_speech",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        alice_agent_events = _agent_transcript_messages(
            ws_alice.messages, agent_participant_id=participant.participant_id
        )
        bob_agent_events = _agent_transcript_messages(
            ws_bob.messages, agent_participant_id=participant.participant_id
        )
        self.assertTrue(alice_agent_events)
        self.assertTrue(bob_agent_events)
        self.assertEqual(alice_agent_events[-1]["event"]["transcript_text"], "Ack: hello team")
        self.assertEqual(bob_agent_events[-1]["event"]["transcript_source"], "agent_llm")

        await agent.stop()
        self.assertIsNone(manager.get_participant(participant.participant_id))

    async def test_agent_session_ignores_interim_transcripts_by_default(self) -> None:
        manager = RoomManager()
        room_control = RoomControlModule(manager)
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = manager.register_participant(ws_alice, name="Alice")
        bob = manager.register_participant(ws_bob, name="Bob")
        room_control.join_participant(alice, room_name="agent-room", create_if_missing=True)
        room_control.join_participant(bob, room_name="agent-room", create_if_missing=False)
        await asyncio.sleep(0)

        agent = AgentSession(
            manager,
            room_control=room_control,
            name="Room Agent",
            responder=EchoTranscriptResponder(prefix="Ack: "),
        )
        participant = await agent.start("agent-room", create_if_missing=False)
        ws_alice.messages.clear()
        ws_bob.messages.clear()

        manager.emit_participant_transcript(
            alice,
            transcript_text="draft thought",
            transcript_is_final=False,
            transcript_source="browser_web_speech",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(
            _agent_transcript_messages(ws_alice.messages, agent_participant_id=participant.participant_id),
            [],
        )
        self.assertEqual(
            _agent_transcript_messages(ws_bob.messages, agent_participant_id=participant.participant_id),
            [],
        )

        await agent.stop()


if __name__ == "__main__":
    unittest.main()
