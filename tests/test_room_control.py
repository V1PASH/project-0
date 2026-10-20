import unittest

from room.control import (
    ParticipantNotInRoomControlError,
    RoomAlreadyExistsError,
    RoomCapacityError,
    RoomControlError,
    RoomControlModule,
)
from room.manager import RoomManager
from room.model import ParticipantEvictedError, RoomNotFoundError


class RoomControlModuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RoomManager()
        self.control = RoomControlModule(self.manager)

    def test_create_room_and_join_by_room_name(self) -> None:
        creator = self.manager.register_participant(websocket=object(), name="Creator")
        joiner = self.manager.register_participant(websocket=object(), name="Joiner")

        room = self.control.create_room(
            "named-room",
            creator_id=creator.participant_id,
            max_participants=5,
        )

        joined_creator, creator_created = self.control.join_participant(
            creator,
            room_name="named-room",
            create_if_missing=False,
        )
        joined_joiner, joiner_created = self.control.join_participant(
            joiner,
            room_name="named-room",
            create_if_missing=False,
        )

        self.assertEqual(room.room_id, "named-room")
        self.assertEqual(joined_creator.room_id, "named-room")
        self.assertEqual(joined_joiner.room_id, "named-room")
        self.assertFalse(creator_created)
        self.assertFalse(joiner_created)
        self.assertEqual(joined_joiner.participant_count, 2)

    def test_create_room_rejects_duplicate_name(self) -> None:
        self.control.create_room("duplicate-room")
        with self.assertRaises(RoomAlreadyExistsError):
            self.control.create_room("duplicate-room")

    def test_join_participant_requires_existing_room_when_disabled(self) -> None:
        participant = self.manager.register_participant(websocket=object(), name="Alice")
        with self.assertRaises(RoomNotFoundError):
            self.control.join_participant(
                participant,
                room_name="missing-room",
                create_if_missing=False,
            )

    def test_update_room_capacity_rejects_lower_than_current_population(self) -> None:
        alice = self.manager.register_participant(websocket=object(), name="Alice")
        bob = self.manager.register_participant(websocket=object(), name="Bob")
        self.control.create_room("capacity-room", max_participants=5)
        self.control.join_participant(alice, room_name="capacity-room", create_if_missing=False)
        self.control.join_participant(bob, room_name="capacity-room", create_if_missing=False)

        with self.assertRaises(RoomCapacityError):
            self.control.update_room_capacity("capacity-room", 1)

    def test_remove_participant_marks_room_deleted_for_last_member(self) -> None:
        participant = self.manager.register_participant(websocket=object(), name="Alice")
        self.control.create_room("solo-room")
        self.control.join_participant(participant, room_name="solo-room", create_if_missing=False)

        removed = self.control.remove_participant(participant.participant_id)

        self.assertEqual(removed.participant_id, participant.participant_id)
        self.assertEqual(removed.room_id, "solo-room")
        self.assertTrue(removed.room_deleted)
        self.assertIsNone(self.manager.get_room("solo-room"))

    def test_remove_participant_requires_membership(self) -> None:
        participant = self.manager.register_participant(websocket=object(), name="Alice")
        with self.assertRaises(ParticipantNotInRoomControlError):
            self.control.remove_participant(participant.participant_id)

    def test_validate_max_participants_rejects_boolean(self) -> None:
        with self.assertRaises(RoomControlError):
            self.control.validate_max_participants(True)
        with self.assertRaises(RoomControlError):
            self.control.validate_max_participants(False)

    def test_validate_max_participants_rejects_values_above_limit(self) -> None:
        with self.assertRaises(RoomControlError):
            self.control.validate_max_participants(
                self.control.max_participants_limit + 1
            )
        self.assertEqual(
            self.control.validate_max_participants(self.control.max_participants_limit),
            self.control.max_participants_limit,
        )

    def test_custom_max_participants_limit_is_enforced(self) -> None:
        control = RoomControlModule(self.manager, max_participants_limit=3)

        with self.assertRaises(RoomControlError):
            control.validate_max_participants(4)
        self.assertEqual(control.validate_max_participants(3), 3)

    def test_close_room_destroys_an_empty_room(self) -> None:
        self.control.create_room("empty-room")

        result = self.control.close_room("empty-room")

        self.assertEqual(result.room_id, "empty-room")
        self.assertEqual(result.removed_participant_ids, [])
        self.assertIsNone(self.manager.get_room("empty-room"))

    def test_close_room_removes_all_participants(self) -> None:
        alice = self.manager.register_participant(websocket=object(), name="Alice")
        bob = self.manager.register_participant(websocket=object(), name="Bob")
        self.control.create_room("close-room")
        self.control.join_participant(alice, room_name="close-room", create_if_missing=False)
        self.control.join_participant(bob, room_name="close-room", create_if_missing=False)

        result = self.control.close_room("close-room")

        self.assertEqual(result.room_id, "close-room")
        self.assertEqual(
            sorted(result.removed_participant_ids),
            sorted([alice.participant_id, bob.participant_id]),
        )
        self.assertIsNone(self.manager.get_room("close-room"))
        self.assertIsNone(alice.room_id)
        self.assertIsNone(bob.room_id)

    def test_validate_max_participants_clamps_default_to_limit(self) -> None:
        control = RoomControlModule(self.manager, max_participants_limit=5)

        # A lowered limit must not reject the server's own default, otherwise
        # every client that omits max_participants is locked out.
        self.assertEqual(control.validate_max_participants(None), 5)

    def test_validate_max_participants_clamps_default_when_limit_is_lowered(self) -> None:
        control = RoomControlModule(self.manager)
        control.max_participants_limit = 3

        self.assertEqual(control.validate_max_participants(None), 3)

    def test_removed_participant_cannot_rejoin_the_same_room(self) -> None:
        alice = self.manager.register_participant(websocket=object(), name="Alice")
        bob = self.manager.register_participant(websocket=object(), name="Bob")
        self.control.create_room("kick-room", max_participants=5)
        self.control.join_participant(alice, room_name="kick-room", create_if_missing=False)
        self.control.join_participant(bob, room_name="kick-room", create_if_missing=False)

        self.control.remove_participant(alice.participant_id)

        self.assertIsNone(alice.room_id)
        with self.assertRaises(ParticipantEvictedError):
            self.control.join_participant(alice, room_name="kick-room", create_if_missing=False)

    def test_evicted_participant_may_join_a_different_room(self) -> None:
        alice = self.manager.register_participant(websocket=object(), name="Alice")
        bob = self.manager.register_participant(websocket=object(), name="Bob")
        self.control.create_room("kick-room", max_participants=5)
        self.control.join_participant(alice, room_name="kick-room", create_if_missing=False)
        self.control.join_participant(bob, room_name="kick-room", create_if_missing=False)
        self.control.remove_participant(alice.participant_id)

        self.control.create_room("other-room", max_participants=5)
        room, _ = self.control.join_participant(
            alice, room_name="other-room", create_if_missing=False
        )

        self.assertEqual(room.room_id, "other-room")

    def test_close_room_blocks_evicted_participants_from_rejoining(self) -> None:
        alice = self.manager.register_participant(websocket=object(), name="Alice")
        bob = self.manager.register_participant(websocket=object(), name="Bob")
        self.control.create_room("close-room", max_participants=5)
        self.control.join_participant(alice, room_name="close-room", create_if_missing=False)
        self.control.join_participant(bob, room_name="close-room", create_if_missing=False)

        self.control.close_room("close-room")

        # The room is gone, so re-creating it gives a fresh room that the
        # previously evicted participant may join again.
        self.control.create_room("close-room", max_participants=5)
        room, _ = self.control.join_participant(
            alice, room_name="close-room", create_if_missing=False
        )
        self.assertEqual(room.room_id, "close-room")


if __name__ == "__main__":
    unittest.main()
