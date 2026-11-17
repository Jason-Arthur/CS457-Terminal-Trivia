"""Acceptance tests for protocol.py, taken from protocol_blueprint.md.

Run from the repository root:  python3 -m unittest discover -s tests
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import protocol as p  # noqa: E402

# The two frames from the coalescing example in protocol_blueprint.md §1.4.
CONNECT_FRAME = (b'{"msg_type":"CONNECT","player_id":null,"payload":'
                 b'{"player_name":"Alice"},"timestamp":1727000000}\n')
MOVE_FRAME = (b'{"msg_type":"MOVE","player_id":"Player_1","payload":'
              b'{"question_id":"R1Q1","answer":"B"},"timestamp":1727000005}\n')


class FramingTests(unittest.TestCase):
    """protocol_blueprint.md §1.2 to §1.4."""

    def test_encode_is_compact_single_line(self):
        """§1.2: compact JSON, a single b"\\n", even for multi-line text."""
        msg = p.make_msg(p.QUESTION, p.SERVER_ID, {"text": "line1\nline2"})
        frame = p.encode_frame(msg)
        self.assertTrue(frame.endswith(b"\n"))
        self.assertEqual(frame.count(b"\n"), 1)
        self.assertNotIn(b": ", frame)

    def test_envelope_has_exactly_four_keys(self):
        """§2: msg_type, player_id, payload, timestamp."""
        msg = p.make_msg(p.LOBBY_WAIT, p.SERVER_ID)
        self.assertEqual(set(msg),
                         {"msg_type", "player_id", "payload", "timestamp"})
        self.assertIsInstance(msg["timestamp"], int)

    def test_coalescing_example(self):
        """§1.4: a 209-byte chunk yields 96-byte CONNECT + 111-byte MOVE."""
        chunk = CONNECT_FRAME + MOVE_FRAME
        self.assertEqual(len(chunk), 209)
        reader = p.FrameReader()
        frames = reader.feed(chunk)
        self.assertEqual([len(f) for f in frames], [96, 111])
        self.assertEqual(reader.buf, bytearray())

    def test_fragmentation_example(self):
        """§1.4: MOVE split after 47 bytes is only parsed once complete."""
        reader = p.FrameReader()
        self.assertEqual(reader.feed(MOVE_FRAME[:47]), [])
        self.assertEqual(len(reader.buf), 47)
        self.assertEqual(reader.feed(MOVE_FRAME[47:]), [MOVE_FRAME[:-1]])

    def test_both_at_once(self):
        """§1.4: a chunk ending inside the second message keeps the tail."""
        reader = p.FrameReader()
        frames = reader.feed(CONNECT_FRAME + MOVE_FRAME[:17])
        self.assertEqual(frames, [CONNECT_FRAME[:-1]])
        self.assertEqual(reader.feed(MOVE_FRAME[17:]), [MOVE_FRAME[:-1]])

    def test_frame_too_large(self):
        """§1.3 step 6: over 4096 bytes without b"\\n" raises FrameTooLarge."""
        reader = p.FrameReader()
        reader.feed(b"x" * p.MAX_FRAME)
        with self.assertRaises(p.FrameTooLarge):
            reader.feed(b"x")


class ValidationTests(unittest.TestCase):
    """protocol_blueprint.md §2, §3 and the §3.10 error table."""

    def code_for(self, raw):
        """Return the ERROR code a raw frame would get, or None if valid."""
        try:
            p.validate_client_msg(p.decode_frame(raw))
        except p.ProtocolError as e:
            return e.code
        return None

    def test_valid_frames(self):
        self.assertIsNone(self.code_for(CONNECT_FRAME[:-1]))
        self.assertIsNone(self.code_for(MOVE_FRAME[:-1]))

    def test_malformed(self):
        self.assertEqual(self.code_for(b"not json"), p.MALFORMED_MESSAGE)
        self.assertEqual(self.code_for(b"\xff\xfe"), p.MALFORMED_MESSAGE)
        self.assertEqual(self.code_for(b"[1,2]"), p.MALFORMED_MESSAGE)
        no_ts = json.dumps({"msg_type": "MOVE", "player_id": "Player_1",
                            "payload": {}}).encode()
        self.assertEqual(self.code_for(no_ts), p.MALFORMED_MESSAGE)
        no_answer = json.dumps(p.make_msg(p.MOVE, "Player_1",
                                          {"question_id": "R1Q1"})).encode()
        self.assertEqual(self.code_for(no_answer), p.MALFORMED_MESSAGE)

    def test_unknown_msg_type(self):
        for msg_type in ("HELLO", p.STATE_UPDATE):
            raw = json.dumps(p.make_msg(msg_type, None)).encode()
            self.assertEqual(self.code_for(raw), p.UNKNOWN_MSG_TYPE)

    def test_unknown_keys_ignored(self):
        """§2: extra keys do not make a message invalid."""
        msg = p.make_msg(p.DISCONNECT, "Player_1", {"reason": "user_quit",
                                                    "extra": 1})
        msg["future_field"] = True
        self.assertIsNone(self.code_for(json.dumps(msg).encode()))

    def test_player_names(self):
        """§3.2: 1 to 16 letters, digits, _ or -."""
        for good in ("Alice", "a", "Player_2-x", "x" * 16):
            self.assertTrue(p.valid_player_name(good), good)
        for bad in ("", "x" * 17, "Bob the Builder", "bob!"):
            self.assertFalse(p.valid_player_name(bad), bad)

    def test_port_range(self):
        """Sprint 3 rubric: ports 1024-65535 only."""
        self.assertEqual(p.port_number("5457"), 5457)
        for bad in ("80", "65536", "abc"):
            with self.assertRaises(Exception):
                p.port_number(bad)


if __name__ == "__main__":
    unittest.main()
