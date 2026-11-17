"""Terminal Trivia wire protocol shared by server.py and client.py.

Implements docs/protocol_blueprint.md:
  Section 1  framing (newline-delimited compact JSON, 4096-byte max frame)
  Section 2  the four-key message envelope
  Section 3  the closed lists of message types and error codes, and the
             payload checks that make a frame MALFORMED_MESSAGE

Standard library only, because the CML nodes have no internet access.
"""

import argparse
import json
import re
import time

# ---------------------------------------------------------------------------
# Constants (protocol_blueprint.md Sections 1 and 3)
# ---------------------------------------------------------------------------

MAX_FRAME = 4096          # bytes, including the trailing b"\n" (Section 1)
RECV_SIZE = 4096          # bytes requested per recv() call (Section 1.3)
SERVER_ID = "SERVER"      # player_id on server-to-client messages (Sec. 2)
PLAYER_1 = "Player_1"
PLAYER_2 = "Player_2"
ROLES = (PLAYER_1, PLAYER_2)
ANSWER_CHOICES = ("A", "B", "C", "D")
ANSWER_TIME_LIMIT = 15    # seconds per answer or wager window (Section 3.5)

# The 11 message types (Section 3.1). No others are allowed.
CONNECT = "CONNECT"
LOBBY_WAIT = "LOBBY_WAIT"
GAME_START = "GAME_START"
QUESTION = "QUESTION"
MOVE = "MOVE"
WAGER_REQUEST = "WAGER_REQUEST"
WAGER = "WAGER"
STATE_UPDATE = "STATE_UPDATE"
ERROR = "ERROR"
DISCONNECT = "DISCONNECT"
GAME_OVER = "GAME_OVER"

CLIENT_TO_SERVER = frozenset({CONNECT, MOVE, WAGER, DISCONNECT})
SERVER_TO_CLIENT = frozenset({LOBBY_WAIT, GAME_START, QUESTION, WAGER_REQUEST,
                              STATE_UPDATE, ERROR, GAME_OVER})

# The 10 error codes (Section 3.10). No others are allowed.
MALFORMED_MESSAGE = "MALFORMED_MESSAGE"
UNKNOWN_MSG_TYPE = "UNKNOWN_MSG_TYPE"
INVALID_NAME = "INVALID_NAME"
LOBBY_FULL = "LOBBY_FULL"
NOT_JOINED = "NOT_JOINED"
INVALID_ANSWER = "INVALID_ANSWER"
OUT_OF_TURN = "OUT_OF_TURN"
INVALID_WAGER = "INVALID_WAGER"
ID_MISMATCH = "ID_MISMATCH"
FRAME_TOO_LARGE = "FRAME_TOO_LARGE"

# CONNECT.player_name: 1 to 16 letters, digits, "_" or "-" (Section 3.2).
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")

# Required payload fields and their types for each client-to-server message
# (Sections 3.2, 3.6, 3.8, 3.11). A missing field or wrong type is
# MALFORMED_MESSAGE. Range checks that have their own error code
# (INVALID_NAME, INVALID_ANSWER, INVALID_WAGER) are left to the server FSM,
# because those codes depend on game state.
_REQUIRED_PAYLOAD = {
    CONNECT: {"player_name": str},
    MOVE: {"question_id": str, "answer": str},
    WAGER: {"amount": object},          # "not an integer" is INVALID_WAGER
    DISCONNECT: {"reason": str},
}
MAX_REASON_LEN = 64       # DISCONNECT.reason limit (Section 3.11)


class ProtocolError(Exception):
    """A received frame that must be answered with ERROR.

    Implements protocol_blueprint.md Section 3.10: carries the error code,
    the human-readable message, and the offending msg_type if it was parsed.
    """

    def __init__(self, code, message, ref_msg_type=None):
        """Store the ERROR payload fields (protocol_blueprint.md §3.10)."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.ref_msg_type = ref_msg_type


class FrameTooLarge(Exception):
    """Over MAX_FRAME bytes buffered without b"\\n" (blueprint §1.3 step 6)."""


# ---------------------------------------------------------------------------
# Envelope and encoding (Sections 1.2 and 2)
# ---------------------------------------------------------------------------

def make_msg(msg_type, player_id, payload=None):
    """Build a message dict with the four envelope keys (blueprint §2)."""
    return {
        "msg_type": msg_type,
        "player_id": player_id,
        "payload": payload if payload is not None else {},
        "timestamp": int(time.time()),
    }


def encode_frame(msg):
    """Serialize one message to compact JSON + b"\\n" (blueprint §1.2).

    json.dumps escapes any newline inside a string, so the only 0x0A byte in
    the result is the terminator.
    """
    return json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n"


def send_msg(sock, msg):
    """Send one framed message with sendall() (protocol_blueprint.md §1.2).

    Used by the client only. The server never calls this: it queues frames
    in Connection.outbuf and flushes with send() (concurrency_design.md §2.5).
    """
    sock.sendall(encode_frame(msg))


# ---------------------------------------------------------------------------
# Receiver framing (Section 1.3)
# ---------------------------------------------------------------------------

class FrameReader:
    """Per-socket receive buffer that extracts newline-terminated frames.

    Implements protocol_blueprint.md §1.3. One FrameReader lives as long as its
    socket, so a partial frame left at the end of one recv() is completed by
    the next one (coalescing and fragmentation, §1.1 and §1.4).
    """

    def __init__(self, sock=None):
        """Create an empty buffer for `sock` (protocol_blueprint.md §1.3)."""
        self.sock = sock
        self.buf = bytearray()

    def read_messages(self):
        """recv() once and return complete frames, or None on EOF.

        Implements protocol_blueprint.md §1.3 steps 1 to 6. A b"" from recv()
        means the peer sent FIN (§4.3), so None is returned and the caller
        must stop reading this socket. Socket exceptions (BlockingIOError,
        ConnectionResetError, ...) propagate to the caller, which decides
        between "nothing to read" and handle_client_disconnect (§4.4).
        """
        chunk = self.sock.recv(RECV_SIZE)
        if not chunk:
            return None
        return self.feed(chunk)

    def feed(self, chunk):
        """Append bytes and return every complete frame (blueprint §1.3).

        Raises FrameTooLarge if more than MAX_FRAME bytes remain buffered
        after the last b"\\n" (§1.3 step 6).
        """
        self.buf.extend(chunk)
        frames = []
        while True:
            idx = self.buf.find(b"\n")
            if idx == -1:
                break
            frames.append(bytes(self.buf[:idx]))
            del self.buf[:idx + 1]
        if len(self.buf) > MAX_FRAME:
            raise FrameTooLarge()
        return frames


# ---------------------------------------------------------------------------
# Decoding and validation (Sections 1.3, 2 and 3)
# ---------------------------------------------------------------------------

def _is_int(value):
    """True for a JSON integer, excluding bool (protocol_blueprint.md §2)."""
    return isinstance(value, int) and not isinstance(value, bool)


def decode_frame(frame):
    """Parse one frame into a message dict and check the envelope.

    Implements protocol_blueprint.md §1.3 (decode, parse) and §2 (exactly the
    four envelope keys with the right types). Raises ProtocolError with
    MALFORMED_MESSAGE on any failure. Unknown extra keys are ignored (§2).
    """
    try:
        msg = json.loads(frame.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProtocolError(MALFORMED_MESSAGE,
                            "Frame is not valid UTF-8 JSON.")
    if not isinstance(msg, dict):
        raise ProtocolError(MALFORMED_MESSAGE,
                            "Message must be a JSON object.")

    msg_type = msg.get("msg_type")
    ref = msg_type if isinstance(msg_type, str) else None
    for key in ("msg_type", "player_id", "payload", "timestamp"):
        if key not in msg:
            raise ProtocolError(MALFORMED_MESSAGE,
                                f"Missing envelope key '{key}'.", ref)
    if not isinstance(msg_type, str):
        raise ProtocolError(MALFORMED_MESSAGE, "msg_type must be a string.")
    if msg["player_id"] is not None and not isinstance(msg["player_id"], str):
        raise ProtocolError(MALFORMED_MESSAGE,
                            "player_id must be a string or null.", ref)
    if not isinstance(msg["payload"], dict):
        raise ProtocolError(MALFORMED_MESSAGE,
                            "payload must be an object.", ref)
    if not _is_int(msg["timestamp"]):
        raise ProtocolError(MALFORMED_MESSAGE,
                            "timestamp must be an integer.", ref)
    return msg


def validate_client_msg(msg):
    """Check a decoded client-to-server message (protocol_blueprint.md §3).

    Raises ProtocolError with UNKNOWN_MSG_TYPE if msg_type is not one of the
    four client-to-server types, or MALFORMED_MESSAGE if a required payload
    field is missing or has the wrong type (§3.2, §3.6, §3.8, §3.11).
    """
    msg_type = msg["msg_type"]
    if msg_type not in CLIENT_TO_SERVER:
        raise ProtocolError(UNKNOWN_MSG_TYPE,
                            f"'{msg_type}' is not a client-to-server message.",
                            msg_type)
    payload = msg["payload"]
    for field, ftype in _REQUIRED_PAYLOAD[msg_type].items():
        if field not in payload or not isinstance(payload[field], ftype):
            raise ProtocolError(MALFORMED_MESSAGE,
                                f"{msg_type} payload needs '{field}'.",
                                msg_type)
    if msg_type == DISCONNECT and len(payload["reason"]) > MAX_REASON_LEN:
        raise ProtocolError(MALFORMED_MESSAGE,
                            "DISCONNECT reason is over 64 characters.",
                            msg_type)


def valid_player_name(name):
    """True if name meets the CONNECT rules (protocol_blueprint.md §3.2)."""
    return isinstance(name, str) and bool(_NAME_RE.match(name))


def error_msg(code, message, ref_msg_type=None):
    """Build a server ERROR message (protocol_blueprint.md §3.10)."""
    return make_msg(ERROR, SERVER_ID, {
        "code": code,
        "message": message,
        "ref_msg_type": ref_msg_type,
    })


def port_number(text):
    """argparse type for -p: a TCP port in 1024-65535.

    Shared by server.py and client.py (Sprint 3 assignment 2.1; rubric
    criterion 1 asks for port validation in this range).
    """
    try:
        port = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a number")
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError(
            f"{port} is out of range (use 1024-65535)")
    return port
