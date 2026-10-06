# Terminal Trivia: Application Protocol Blueprint

**Course:** CS 457 Computer Networks, Sprint 1
**Author:** Jason Arthur
**Game:** Terminal Trivia (2 players, 3 rounds x 3 multiple-choice questions, tiebreaker wager)

This document is the contract between the Terminal Trivia server and its clients. Every byte the
client and server exchange must match the framing rule in Section 1 and one of the message schemas
in Section 3. The server-side state machine that drives these messages lives in
[`fsm_specification.md`](fsm_specification.md).

---

## 1. Transport & Framing

| Property | Value |
|---|---|
| Transport | TCP (IPv4), server listens on port `5457` |
| Serialization | JSON object, UTF-8 encoded |
| Framing | Newline-delimited JSON: each message is exactly one JSON object followed by `\n` (`0x0A`) |
| Max frame size | 4096 bytes including the `\n` |

### 1.1 Why the framing is needed

TCP is a byte stream. It does not preserve the boundaries of individual `send()` calls, so:

- **Coalescing:** two messages sent back to back can arrive in a single `recv()` chunk.
- **Fragmentation:** one message can be split across two or more `recv()` chunks.

A receiver that treats each `recv()` result as one message will break under either case. The `\n`
terminator gives the receiver a deterministic boundary to search for.

### 1.2 Sender rules

1. Build the message as a Python `dict` matching a schema in Section 3.
2. Serialize with `json.dumps(msg, separators=(",", ":"))`. This produces compact, single-line JSON.
   It never contains a raw `0x0A` byte because `json.dumps` escapes any newline inside a string
   (for example, a question text containing a line break) as the two characters `\n`, so
   a delimiter collision is impossible.
3. Append `b"\n"`, encode as UTF-8, and send with `sock.sendall()` (never plain `send()`, which may
   write only part of the buffer).

```python
def send_msg(sock, msg: dict) -> None:
    data = json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n"
    sock.sendall(data)
```

### 1.3 Receiver extraction logic

Each connection owns one byte buffer that persists across `recv()` calls.

1. `chunk = sock.recv(4096)`.
2. If `chunk == b""`, the peer closed the connection (EOF). Stop and run disconnect handling
   (Section 4).
3. Append `chunk` to the buffer.
4. While the buffer contains `b"\n"`: split off everything before the first `\n` as one frame,
   remove the frame and its `\n` from the buffer, then decode and parse the frame.
5. Whatever remains after the last `\n` is a partial message. Keep it in the buffer and go back to
   step 1.
6. If the buffer grows past 4096 bytes without a `\n`, the peer is misbehaving: send
   `ERROR` with code `FRAME_TOO_LARGE`, then close the connection.

```python
MAX_FRAME = 4096

class FrameReader:
    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()

    def read_messages(self):
        """Return a list of complete frames (bytes), or None on EOF."""
        chunk = self.sock.recv(4096)
        if not chunk:
            return None                       # TCP FIN received: peer closed
        self.buf.extend(chunk)
        frames = []
        while True:
            idx = self.buf.find(b"\n")
            if idx == -1:
                break
            frames.append(bytes(self.buf[:idx]))
            del self.buf[:idx + 1]
        if len(self.buf) > MAX_FRAME:
            raise ValueError("FRAME_TOO_LARGE")
        return frames
```

Each frame is then parsed with `json.loads(frame.decode("utf-8"))` and validated against Section 3.
A frame that fails to decode, fails to parse, or is missing a required field gets an `ERROR`
reply (`MALFORMED_MESSAGE`) and is discarded. The connection stays open and the receive loop keeps
running.

### 1.4 Wire stream examples

**Coalescing.** Alice's client sends `CONNECT`, then a moment later a `MOVE`. Both arrive in one
209-byte `recv()` chunk:

```text
{"msg_type":"CONNECT","player_id":null,"payload":{"player_name":"Alice"},"timestamp":1727000000}\n{"msg_type":"MOVE","player_id":"Player_1","payload":{"question_id":"R1Q1","answer":"B"},"timestamp":1727000005}\n
```

Raw bytes as Wireshark's hex pane would show them (`0a` at offsets `0x60` and `0xd0` are the
frame terminators):

```text
0000  7b 22 6d 73 67 5f 74 79 70 65 22 3a 22 43 4f 4e   {"msg_type":"CON
0010  4e 45 43 54 22 2c 22 70 6c 61 79 65 72 5f 69 64   NECT","player_id
0020  22 3a 6e 75 6c 6c 2c 22 70 61 79 6c 6f 61 64 22   ":null,"payload"
0030  3a 7b 22 70 6c 61 79 65 72 5f 6e 61 6d 65 22 3a   :{"player_name":
0040  22 41 6c 69 63 65 22 7d 2c 22 74 69 6d 65 73 74   "Alice"},"timest
0050  61 6d 70 22 3a 31 37 32 37 30 30 30 30 30 30 7d   amp":1727000000}
0060  0a 7b 22 6d 73 67 5f 74 79 70 65 22 3a 22 4d 4f   .{"msg_type":"MO
0070  56 45 22 2c 22 70 6c 61 79 65 72 5f 69 64 22 3a   VE","player_id":
0080  22 50 6c 61 79 65 72 5f 31 22 2c 22 70 61 79 6c   "Player_1","payl
0090  6f 61 64 22 3a 7b 22 71 75 65 73 74 69 6f 6e 5f   oad":{"question_
00a0  69 64 22 3a 22 52 31 51 31 22 2c 22 61 6e 73 77   id":"R1Q1","answ
00b0  65 72 22 3a 22 42 22 7d 2c 22 74 69 6d 65 73 74   er":"B"},"timest
00c0  61 6d 70 22 3a 31 37 32 37 30 30 30 30 30 35 7d   amp":1727000005}
00d0  0a                                                .
```

The receiver finds `\n` at index 96, extracts the 96-byte `CONNECT` frame, finds the next `\n` at
index 208, extracts the 111-byte `MOVE` frame, and is left with an empty buffer.

**Fragmentation.** The same `MOVE` message is split across two `recv()` calls:

```text
recv #1 -> {"msg_type":"MOVE","player_id":"Player_1","payl
recv #2 -> oad":{"question_id":"R1Q1","answer":"B"},"timestamp":1727000005}\n
```

After recv #1 the buffer holds 47 bytes and no `\n`, so nothing is parsed. After recv #2 the
buffer holds the full frame plus `\n`, and exactly one message is extracted.

**Both at once.** A chunk can end in the middle of the second message:

```text
recv #1 -> {"msg_type":"STATE_UPDATE",...}\n{"msg_type":"QUES
recv #2 -> TION",...}\n
```

recv #1 yields one complete `STATE_UPDATE`; `{"msg_type":"QUES` stays buffered until recv #2
completes the `QUESTION` frame.

---

## 2. Common Message Envelope

Every message, in both directions, is a JSON object with exactly these four top-level keys:

| Key | Type | Required | Description |
|---|---|---|---|
| `msg_type` | string | yes | One of the message types in Section 3 (uppercase). |
| `player_id` | string or null | yes | Client to server: the role the server assigned (`"Player_1"` or `"Player_2"`); `null` before assignment (only in `CONNECT`). Server to client: always `"SERVER"`. |
| `payload` | object | yes | Message-specific fields defined in Section 3. `{}` when there are none. |
| `timestamp` | integer | yes | Sender's Unix epoch time in seconds. Informational only; the server uses its own clock for the answer timer. |

Unknown top-level or payload keys are ignored, so later sprints can add fields without breaking
older clients.

---

## 3. Message Types

### 3.1 Summary

| # | Message | Direction | Purpose |
|---|---|---|---|
| 1 | `CONNECT` | Client to Server | Join the game room with a display name. |
| 2 | `LOBBY_WAIT` | Server to Client | Tell Player 1 they are connected and waiting for Player 2. |
| 3 | `GAME_START` | Server to Both | Game begins; assigns each client its role (`Player_1` / `Player_2`). |
| 4 | `QUESTION` | Server to Both | Broadcast the next question, its point value, and the 15 s answer window. |
| 5 | `MOVE` | Client to Server | Player submits an answer choice for the current question. |
| 6 | `WAGER_REQUEST` | Server to Both | Scores are tied after round 3; ask each player for a wager. |
| 7 | `WAGER` | Client to Server | Player submits their tiebreaker wager. |
| 8 | `STATE_UPDATE` | Server to Both | Reveal the correct answer, each player's result, and updated scores. |
| 9 | `ERROR` | Server to Client | Reject a malformed, invalid, or out-of-turn message. |
| 10 | `DISCONNECT` | Client to Server | Player is quitting on purpose. |
| 11 | `GAME_OVER` | Server to Both | Final result (win, draw, or forfeit) and final scores. |

**Turn model.** Trivia is played in *answer windows* rather than alternating turns: both players
answer the same question at the same time. A player's "turn" is open from the moment the server
sends `QUESTION` until that player's first valid `MOVE` or the 15 s timer expires, whichever
comes first. A `MOVE` sent outside an open window is an out-of-turn move (see `ERROR`).

**Scoring.** Round 1 questions are worth 100 points, round 2 worth 200, round 3 worth 300. A
correct answer earns the question's points; a wrong answer or no answer earns 0.

### 3.2 `CONNECT` (Client to Server)

Sent once, immediately after the TCP connection is established.

| Payload field | Type | Constraints |
|---|---|---|
| `player_name` | string | 1 to 16 characters, letters, digits, `_` or `-` |

```json
{
  "msg_type": "CONNECT",
  "player_id": null,
  "payload": { "player_name": "Alice" },
  "timestamp": 1727000000
}
```

The server replies with `LOBBY_WAIT` (first player) or triggers `GAME_START` (second player). A
third connection while a game is running receives `ERROR` (`LOBBY_FULL`) and is closed.

### 3.3 `LOBBY_WAIT` (Server to Client)

| Payload field | Type | Description |
|---|---|---|
| `assigned_id` | string | Role assigned by connection order. Always `"Player_1"` here. |
| `players_connected` | integer | Number of players currently in the room (1). |
| `players_needed` | integer | Players required to start (2). |

```json
{
  "msg_type": "LOBBY_WAIT",
  "player_id": "SERVER",
  "payload": { "assigned_id": "Player_1", "players_connected": 1, "players_needed": 2 },
  "timestamp": 1727000000
}
```

### 3.4 `GAME_START` (Server to Both)

Sent to each client separately so `your_id` is correct for each recipient.

| Payload field | Type | Description |
|---|---|---|
| `your_id` | string | `"Player_1"` (first to connect) or `"Player_2"` (second). |
| `players` | object | Map of role to display name for both players. |
| `total_rounds` | integer | 3 |
| `questions_per_round` | integer | 3 |
| `answer_time_limit` | integer | Seconds per question (15). |

```json
{
  "msg_type": "GAME_START",
  "player_id": "SERVER",
  "payload": {
    "your_id": "Player_2",
    "players": { "Player_1": "Alice", "Player_2": "Bob" },
    "total_rounds": 3,
    "questions_per_round": 3,
    "answer_time_limit": 15
  },
  "timestamp": 1727000010
}
```

### 3.5 `QUESTION` (Server to Both)

Opens an answer window for both players.

| Payload field | Type | Description |
|---|---|---|
| `question_id` | string | Unique per game, format `R<round>Q<number>` (e.g. `"R2Q3"`). Tiebreaker is `"TB"`. |
| `round` | integer | 1 to 3, or 4 for the tiebreaker. |
| `question_number` | integer | 1 to 3 within the round. |
| `text` | string | The question. |
| `choices` | object | Exactly four keys `"A"`, `"B"`, `"C"`, `"D"`, each a string. |
| `points` | integer | Points for a correct answer (100, 200, 300; 0 for the tiebreaker, which uses the wager). |
| `time_limit` | integer | Seconds the window stays open (15). |

```json
{
  "msg_type": "QUESTION",
  "player_id": "SERVER",
  "payload": {
    "question_id": "R1Q1",
    "round": 1,
    "question_number": 1,
    "text": "Which layer of the OSI model does TCP operate at?",
    "choices": { "A": "Network", "B": "Transport", "C": "Session", "D": "Data Link" },
    "points": 100,
    "time_limit": 15
  },
  "timestamp": 1727000012
}
```

### 3.6 `MOVE` (Client to Server)

The player's answer for the open question. Each player may submit one `MOVE` per question; the
first valid one is final.

| Payload field | Type | Constraints |
|---|---|---|
| `question_id` | string | Must equal the `question_id` of the currently open question. |
| `answer` | string | One of `"A"`, `"B"`, `"C"`, `"D"` (case-insensitive; server normalizes to uppercase). |

```json
{
  "msg_type": "MOVE",
  "player_id": "Player_1",
  "payload": { "question_id": "R1Q1", "answer": "B" },
  "timestamp": 1727000015
}
```

The server does **not** reply to a valid `MOVE` immediately (to avoid leaking whether it was
right before the opponent answers). Results arrive in the next `STATE_UPDATE`. An invalid `MOVE`
gets an `ERROR`.

### 3.7 `WAGER_REQUEST` (Server to Both)

Sent only when scores are tied after round 3.

| Payload field | Type | Description |
|---|---|---|
| `max_wager` | integer | The recipient's current score (they may wager 0 up to this). |
| `scores` | object | Map of role to current score. |
| `time_limit` | integer | Seconds to submit a wager (15). |

```json
{
  "msg_type": "WAGER_REQUEST",
  "player_id": "SERVER",
  "payload": { "max_wager": 900, "scores": { "Player_1": 900, "Player_2": 900 }, "time_limit": 15 },
  "timestamp": 1727000200
}
```

### 3.8 `WAGER` (Client to Server)

| Payload field | Type | Constraints |
|---|---|---|
| `amount` | integer | `0 <= amount <= max_wager` from `WAGER_REQUEST`. |

```json
{
  "msg_type": "WAGER",
  "player_id": "Player_2",
  "payload": { "amount": 500 },
  "timestamp": 1727000204
}
```

A player who does not wager before the timer expires wagers 0. Once both wagers are in (or the
timer expires) the server sends the tiebreaker `QUESTION` with `question_id` `"TB"`. A correct
tiebreaker answer adds `2 x amount`; a wrong or missing answer subtracts `amount`.

### 3.9 `STATE_UPDATE` (Server to Both)

Sent when an answer window closes (both players answered, or the timer expired). Closes the window
and synchronizes both screens.

| Payload field | Type | Description |
|---|---|---|
| `question_id` | string | The question that just closed. |
| `correct_answer` | string | `"A"` to `"D"`. |
| `results` | object | Map of role to `{ "answer": string or null, "correct": boolean, "points_awarded": integer }`. `answer` is `null` if the player timed out. |
| `scores` | object | Map of role to total score after this question. |
| `next` | string | `"QUESTION"`, `"WAGER_REQUEST"`, or `"GAME_OVER"`: what the client should expect next. |

```json
{
  "msg_type": "STATE_UPDATE",
  "player_id": "SERVER",
  "payload": {
    "question_id": "R1Q1",
    "correct_answer": "B",
    "results": {
      "Player_1": { "answer": "B", "correct": true,  "points_awarded": 100 },
      "Player_2": { "answer": null, "correct": false, "points_awarded": 0 }
    },
    "scores": { "Player_1": 100, "Player_2": 0 },
    "next": "QUESTION"
  },
  "timestamp": 1727000027
}
```

### 3.10 `ERROR` (Server to Client)

Sent only to the client that caused the problem. An `ERROR` never ends the game by itself; the
server keeps its receive loop running unless the code says the connection is closed.

| Payload field | Type | Description |
|---|---|---|
| `code` | string | One of the codes below. |
| `message` | string | Human-readable explanation for the client to print. |
| `ref_msg_type` | string or null | `msg_type` of the offending message, if it could be parsed. |

| Code | Trigger | Connection |
|---|---|---|
| `MALFORMED_MESSAGE` | Frame is not valid UTF-8 / JSON, or a required key is missing or the wrong type. | Kept open |
| `UNKNOWN_MSG_TYPE` | `msg_type` is not a client-to-server type from Section 3. | Kept open |
| `INVALID_NAME` | `player_name` fails the `CONNECT` constraints. | Kept open (client may retry `CONNECT`) |
| `LOBBY_FULL` | A third client connects while two are already in the room. | Closed by server |
| `NOT_JOINED` | Any message other than `CONNECT` before `CONNECT` succeeded. | Kept open |
| `INVALID_ANSWER` | `MOVE.answer` is not `A` to `D`. | Kept open; window stays open for a corrected `MOVE` |
| `OUT_OF_TURN` | `MOVE` with no window open, after this player already answered, or with a stale `question_id`; or `WAGER` when no wager was requested. | Kept open; message ignored |
| `INVALID_WAGER` | `WAGER.amount` is negative, not an integer, or above `max_wager`. | Kept open; client may resend |
| `ID_MISMATCH` | `player_id` does not match the role assigned to this socket. | Kept open; message ignored |
| `FRAME_TOO_LARGE` | More than 4096 bytes buffered without a `\n`. | Closed by server |

```json
{
  "msg_type": "ERROR",
  "player_id": "SERVER",
  "payload": {
    "code": "OUT_OF_TURN",
    "message": "You already answered R1Q1.",
    "ref_msg_type": "MOVE"
  },
  "timestamp": 1727000016
}
```

### 3.11 `DISCONNECT` (Client to Server)

Graceful, intentional quit. The client sends this, then closes its socket.

| Payload field | Type | Description |
|---|---|---|
| `reason` | string | Free text, e.g. `"user_quit"`. Max 64 characters. |

```json
{
  "msg_type": "DISCONNECT",
  "player_id": "Player_2",
  "payload": { "reason": "user_quit" },
  "timestamp": 1727000100
}
```

### 3.12 `GAME_OVER` (Server to Both)

The last message of a game. After sending it the server closes both connections.

| Payload field | Type | Description |
|---|---|---|
| `result` | string | `"WIN"`, `"DRAW"`, or `"FORFEIT"`. |
| `winner` | string or null | Winning role; `null` on a draw. |
| `reason` | string | `"most_points"`, `"tiebreaker"`, `"tied_after_tiebreaker"`, `"opponent_quit"`, or `"opponent_disconnected"`. |
| `final_scores` | object | Map of role to final score. |

```json
{
  "msg_type": "GAME_OVER",
  "player_id": "SERVER",
  "payload": {
    "result": "FORFEIT",
    "winner": "Player_1",
    "reason": "opponent_disconnected",
    "final_scores": { "Player_1": 300, "Player_2": 100 }
  },
  "timestamp": 1727000110
}
```

---

## 4. Connection Termination & Socket Lifecycle

### 4.1 Normal lifecycle

1. Client opens a TCP connection to `server.arthur.edu:5457` (three-way handshake).
2. Client sends `CONNECT`; server replies `LOBBY_WAIT` or starts the game with `GAME_START`.
3. Game messages flow as described in Section 3.
4. Server sends `GAME_OVER`, then calls `shutdown(SHUT_RDWR)` and `close()` on both sockets, which
   starts the TCP FIN teardown. Clients see EOF on their next `recv()` and exit.

### 4.2 Graceful disconnect (`DISCONNECT` + TCP FIN)

A player who quits (types `quit` or presses Ctrl+C, which the client catches) sends `DISCONNECT`
and then closes the socket, so the OS sends FIN. When the server receives `DISCONNECT`:

- **In the lobby:** remove the player and go back to waiting. No one else is affected.
- **Mid-game:** send `GAME_OVER` to the remaining player with `result: "FORFEIT"`,
  `winner: <remaining player>`, `reason: "opponent_quit"`, then clean up both sockets.

The FIN that follows `DISCONNECT` produces a 0-byte `recv()` on the server. By then the player is
already marked as gone, so the server simply closes that socket.

### 4.3 Clean close without `DISCONNECT` (TCP FIN, 0-byte EOF)

If a client process exits normally without sending `DISCONNECT` (for example the terminal window is
closed), the OS still sends FIN. The server's `recv()` then returns `b""`.

**EOF rule:** `recv()` returning `b""` is the only signal of a clean close. It does not raise an
exception, and every later `recv()` on that socket also returns `b""` immediately. A loop that does
not check for it will spin forever at 100% CPU. Every receive loop in this project must check:

```python
frames = reader.read_messages()
if frames is None:                 # recv() returned b"": peer sent FIN
    log.info("%s closed the connection (EOF)", player_id)
    handle_client_disconnect(player_id, reason="opponent_disconnected")
    break                          # leave the loop; do not call recv() again
```

The server treats this the same as an abrupt drop (Section 4.4): forfeit if mid-game, remove from
lobby otherwise.

### 4.4 Abrupt termination (TCP RST, network drops)

If a client is killed (`kill -9`), loses power, or the link between subnets is cut in CML, no
`DISCONNECT` or FIN arrives. The server finds out in one of these ways:

| Condition | How it surfaces in Python |
|---|---|
| Peer host sends RST (process crashed, port closed) | `recv()` or `sendall()` raises `ConnectionResetError` |
| Server writes to a socket whose peer is already gone | `sendall()` raises `BrokenPipeError` |
| Connection aborted by the local OS | `ConnectionAbortedError` |
| Peer silent because the link is down (no RST ever arrives) | `TimeoutError` once TCP keepalive probes fail (below) |

Every `recv()` and every `sendall()` on the server is wrapped so that none of these can crash the
server process:

```python
try:
    frames = reader.read_messages()
    if frames is None:
        handle_client_disconnect(player_id, reason="opponent_disconnected")
        break
    for frame in frames:
        dispatch(player_id, frame)
except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError) as e:
    log.warning("Connection to %s lost abruptly: %s", player_id, e)
    handle_client_disconnect(player_id, reason="opponent_disconnected")
    break
```

**Detecting silent link drops.** A cut CML link produces no packets at all, so neither EOF nor
RST ever arrives, and a player who simply does not answer is also silent. The server therefore does
not time out on application silence. Instead it enables TCP keepalive on every client socket with
short Linux timers, so the kernel probes an idle connection and reports it dead within about
25 seconds:

```python
sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)   # idle seconds before first probe
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)   # seconds between probes
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)     # failed probes before giving up
```

When the probes fail, the blocked `recv()` raises `TimeoutError` (`ETIMEDOUT`), which the handler
above already catches. During a game the server also writes a `QUESTION` or `STATE_UPDATE` at least
every 15 seconds; once the kernel gives up on those unacknowledged writes, `sendall()` fails with
one of the same exceptions.

### 4.5 What `handle_client_disconnect` does

1. Mark the player as disconnected and close their socket (ignoring any error from `close()`).
2. If the game is in progress, send `GAME_OVER` (`FORFEIT`, opponent wins) to the remaining player.
   That send is itself wrapped in the same `try/except`, because both players may have dropped.
3. Move the server state machine to `CLEANUP`, which resets the room so a new pair of players can
   connect.

The client applies the same rules in reverse: on EOF or any of the exceptions above, it prints
"Lost connection to server" and exits cleanly instead of showing a traceback.
