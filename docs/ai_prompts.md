# Terminal Trivia: AI Prompting & Constraint Strategy

**Course:** CS 457 Computer Networks, Sprint 1
**Author:** Jason Arthur

This document records how AI coding assistants are used on this project and, more importantly,
how they are constrained. The rule is simple: **the AI implements the spec; it never designs it.**
The two spec documents are the source of truth:

- [`protocol_blueprint.md`](protocol_blueprint.md): framing, envelope, the 11 message schemas,
  error codes, socket lifecycle.
- [`fsm_specification.md`](fsm_specification.md): server states, transitions T1 to T16, invalid
  input handling, disconnect handling.

If the AI produces anything that is not in those documents (a new message type, a different
framing rule, a renamed field, a state that doesn't exist), the output is rejected, not the spec.
A spec change only happens when I decide it, in a separate commit to the spec documents first.

---

## 1. Tools & Disclosure

| Tool | Used for |
|---|---|
| Claude (Claude Code) | Drafting the Sprint 1 design documents with me, and generating Sprint 3 code from the prompts below. |

**Disclosure:** Claude helped draft `protocol_blueprint.md` and `fsm_specification.md`. I reviewed
every section, made the design decisions (port 5457, newline-delimited JSON, simultaneous answer
window as the "turn", 100/200/300 scoring, lobby disconnects returning to waiting), and approved
each document in its PR before merging (PR #8 and PR #9).

---

## 2. Constraint Strategy

Five techniques keep the generated code tied to the blueprint instead of drifting toward generic
socket boilerplate.

1. **Spec in context, every time.** Every prompt includes the relevant spec sections verbatim
   (pasted, or attached as files). The AI is never asked to "write a trivia server" from memory.
2. **Closed vocabulary.** Prompts list the exact allowed values (the 11 `msg_type` strings, the 10
   error codes, the 9 FSM states) and forbid anything else.
3. **Section references in code.** Every function must carry a docstring naming the blueprint or
   FSM section it implements (e.g. `"""Implements protocol_blueprint.md §1.3."""`). This makes
   the code reviewable against the spec line by line.
4. **One module per prompt.** Small, bounded tasks (framing, then validation, then the FSM) instead
   of one giant "build the game" prompt. Each module is tested before the next prompt.
5. **Spec-derived acceptance tests.** Each prompt ends with test cases taken directly from the
   spec (the wire examples in §1.4, the transition table rows, the error table rows). Output that
   fails them is sent back with the failing case, not patched by hand.

---

## 3. Base System Prompt

Used as the system prompt (or first message) for every coding session on this project.

```text
You are implementing the networking code for "Terminal Trivia", a 2-player TCP trivia
game for a CS 457 Computer Networks course. You are an implementer, not a designer.

SOURCE OF TRUTH
- docs/protocol_blueprint.md and docs/fsm_specification.md (provided below) are the
  complete and binding specification. Implement exactly what they say.
- If something you need is not specified, STOP and ask. Do not invent behavior.
- Never add, rename, or remove a message type, payload field, error code, or FSM state.

HARD CONSTRAINTS
- Python 3 standard library only (socket, threading, selectors, json, time, logging,
  struct, random). No third-party packages. The code runs on Cisco Modeling Labs nodes
  with no internet access.
- Transport: TCP over IPv4. Server listens on 0.0.0.0:5457.
- Framing: newline-delimited JSON exactly as in protocol_blueprint.md §1.
  * Send: json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n", via sendall().
  * Receive: one persistent bytearray buffer per socket; recv(4096); split on b"\n";
    keep the trailing partial frame; >4096 bytes buffered without b"\n" = FRAME_TOO_LARGE.
  * NEVER assume one recv() == one message.
- Envelope: every message has exactly msg_type, player_id, payload, timestamp (§2).
- Allowed msg_type values (no others):
  CONNECT, LOBBY_WAIT, GAME_START, QUESTION, MOVE, WAGER_REQUEST, WAGER,
  STATE_UPDATE, ERROR, DISCONNECT, GAME_OVER
- Allowed ERROR codes (no others):
  MALFORMED_MESSAGE, UNKNOWN_MSG_TYPE, INVALID_NAME, LOBBY_FULL, NOT_JOINED,
  INVALID_ANSWER, OUT_OF_TURN, INVALID_WAGER, ID_MISMATCH, FRAME_TOO_LARGE
- recv() returning b"" is EOF: stop reading that socket and run disconnect handling.
  Never loop on recv() after EOF.
- Every recv() and sendall() is wrapped in
  except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError).
  No bare "except:" and no "except Exception: pass".
- The server is authoritative: scoring, the 15 s answer timer, and turn enforcement
  happen only on the server, using the server's clock.

CODE STYLE
- Every function has a docstring that cites the spec section it implements,
  e.g. """Implements protocol_blueprint.md §1.3 (receiver extraction).""".
- Use the exact names from the spec: send_msg, FrameReader, handle_client_disconnect.
- Use the logging module, not print, on the server.

OUTPUT
- Return only the requested module. After the code, list any place where the spec
  was ambiguous and what you assumed, so I can confirm or correct it.
```

---

## 4. Task Prompts

Each task prompt is sent after the base system prompt, together with the spec sections it names.

### 4.1 Framing layer (`protocol.py`, part 1)

```text
Implement the framing layer in protocol.py. Spec: protocol_blueprint.md §1.2 and §1.3.

Write exactly:
1. MAX_FRAME = 4096
2. send_msg(sock, msg: dict) -> None
   - compact json.dumps, UTF-8, append b"\n", sock.sendall().
3. class FrameReader:
   - __init__(self, sock): store sock, self.buf = bytearray()
   - read_messages(self) -> list[bytes] | None
     * chunk = sock.recv(4096); if chunk == b"": return None (EOF)
     * extend buffer; extract every complete frame up to b"\n"; remove frame + b"\n"
     * leave any partial frame in the buffer
     * if len(buffer) > MAX_FRAME after extraction: raise FrameTooLargeError
4. class FrameTooLargeError(Exception)

Do not parse JSON in this layer. Do not catch socket exceptions here; callers do (§4.4).

Acceptance tests (write them as unittest cases with a fake socket whose recv() returns
a scripted list of chunks):
- Coalescing: the 209-byte chunk from §1.4 (CONNECT + MOVE) returns 2 frames, buffer empty.
- Fragmentation: the MOVE frame split after 47 bytes returns [] then 1 frame.
- Split mid second message: chunk 1 = full STATE_UPDATE + '{"msg_type":"QUES',
  chunk 2 = rest of QUESTION + b"\n" returns 1 frame, then 1 frame.
- EOF: recv() returns b"" -> read_messages() returns None.
- 4097 bytes with no b"\n" -> FrameTooLargeError.
- send_msg output contains exactly one b"\n", at the end, even when a string value
  contains a newline character.
```

### 4.2 Message validation and builders (`protocol.py`, part 2)

```text
Add message parsing, validation, and builders to protocol.py.
Spec: protocol_blueprint.md §2 and §3.2 to §3.12.

Write:
1. parse_frame(frame: bytes) -> dict
   - decode UTF-8, json.loads; raise ProtocolError("MALFORMED_MESSAGE") on any failure.
   - require exactly the envelope keys and types from §2, else MALFORMED_MESSAGE.
2. validate_client_message(msg: dict) -> None
   - msg_type must be CONNECT, MOVE, WAGER or DISCONNECT, else UNKNOWN_MSG_TYPE.
   - check each payload field's presence and type exactly per its §3 table.
   - CONNECT.player_name: 1-16 chars of [A-Za-z0-9_-], else INVALID_NAME.
   - MOVE.answer: normalize to uppercase; must be A-D, else INVALID_ANSWER.
   - WAGER.amount: must be an int (bool is NOT an int here), else INVALID_WAGER.
     (Range checking against max_wager belongs to the FSM, not here.)
3. class ProtocolError(Exception) carrying .code (one of the allowed ERROR codes).
4. One builder per server message, returning a full envelope with player_id "SERVER"
   and timestamp int(time.time()):
   make_lobby_wait, make_game_start, make_question, make_wager_request,
   make_state_update, make_error, make_game_over.
   Builder parameters must match the payload fields in §3 by name.

Acceptance tests:
- Every JSON sample in protocol_blueprint.md §3 round-trips: parse_frame(sample) succeeds,
  and each server-side sample can be produced by its builder with the same payload.
- Missing "timestamp" -> MALFORMED_MESSAGE. msg_type "ATTACK" -> UNKNOWN_MSG_TYPE.
- MOVE answer "e" -> INVALID_ANSWER; answer "b" -> accepted and normalized to "B".
- CONNECT name "" and name of 17 chars -> INVALID_NAME.
```

### 4.3 Server state machine (`server.py`)

```text
Implement the game server in server.py using protocol.py.
Spec: fsm_specification.md (all sections) and protocol_blueprint.md §4.

Structure:
- class State(Enum): INIT, WAITING_FOR_PLAYERS, GAME_START, PLAYER_TURN, EVALUATE_MOVE,
  CHECK_WIN_DRAW, WAGER_TURN, GAME_OVER, CLEANUP. No other states.
- class GameRoom holding: state, players (role -> socket, name, score, answered,
  answer, wager), question index, current question, timer.
- One transition method per transition-table row T1 to T16. Name each with its row,
  e.g. def t5_record_move(...). Each docstring quotes its row from §3.1.
- dispatch(role, msg) routes a validated client message based on the CURRENT state
  using the §3.2 table. Any (state, message) pair not listed as valid sends the ERROR
  code from that table and leaves the state unchanged. It must never raise out of
  the receive loop.
- handle_client_disconnect(role, reason) implements §3.3 exactly:
  lobby -> remove Player_1, stay WAITING_FOR_PLAYERS (NOT a forfeit);
  in game -> GAME_OVER FORFEIT, opponent wins, then CLEANUP.
- The 15 s timer is server-side (threading.Timer or a select timeout). Cancel it on
  entering EVALUATE_MOVE and on entering GAME_OVER so a late timer can never
  re-enter EVALUATE_MOVE.
- Enable TCP keepalive on each client socket with the exact values in §4.4.
- Scoring: 100 / 200 / 300 by round; tiebreaker +2x wager if correct, -1x if wrong
  or unanswered.
- Questions come from questions.json (3 rounds x 3 questions + 1 tiebreaker).

Do not add a scoreboard, chat, spectators, reconnection, or any feature not in the spec.

Acceptance tests (scripted fake clients, one per row):
- Happy path T1-T9 with scripted answers: final scores and GAME_OVER payload match.
- Tie after 9 questions goes through WAGER_TURN and TB (T10-T14), including a
  still-tied DRAW.
- Every row of fsm_specification.md §3.2 sends the listed ERROR code and the state is
  unchanged afterwards.
- Every row of §3.3: lobby disconnect returns to WAITING_FOR_PLAYERS; mid-game EOF and
  mid-game ConnectionResetError both produce GAME_OVER FORFEIT to the other player.
- After CLEANUP, two new clients can connect and play a second game.
```

### 4.4 Client (`client.py`)

```text
Implement the terminal client in client.py using protocol.py.
Spec: protocol_blueprint.md §3 and §4.

- Connect to the server host given on the command line (default server.arthur.edu),
  port 5457. Send CONNECT with the player name.
- The client is a display and input terminal only. It does not score, does not judge
  answers, and does not run the authoritative timer (it may show a countdown).
- On QUESTION: print the question and choices, read A-D from stdin, send MOVE.
- On WAGER_REQUEST: read an integer 0..max_wager, send WAGER.
- On STATE_UPDATE / GAME_OVER: print results and scores. Exit after GAME_OVER.
- On ERROR: print the message; for INVALID_ANSWER / INVALID_WAGER let the user retry.
- "quit" or Ctrl+C: send DISCONNECT, close the socket, exit cleanly.
- EOF or any socket exception from §4.4: print "Lost connection to server" and exit
  with no traceback.
```

---

## 5. Rejection Checklist

AI output is rejected and re-prompted (with the failing item quoted) if any of these appear:

| Red flag | Why it violates the spec |
|---|---|
| `data = sock.recv(1024)` followed directly by `json.loads(data)` | Assumes one `recv()` is one message; ignores §1.1 coalescing and fragmentation. |
| `sock.send(...)` instead of `sendall` | Can write a partial frame (§1.2). |
| A `msg_type` or error code not in the closed lists | Breaks the protocol contract (§3, §3.10). |
| `json.dumps(..., indent=...)` on the wire | Multi-line JSON would break newline framing. |
| A receive loop with no `if not data` / `None` check | Spins forever at 100% CPU after EOF (§4.3). |
| Bare `except:` or `except Exception: pass` around socket code | Hides disconnects instead of triggering `handle_client_disconnect` (§4.4). |
| Scoring, answer checking, or the authoritative timer on the client | The server is authoritative (FSM §2). |
| A disconnect in the lobby treated as a forfeit | FSM §3.3 says the server returns to waiting. |
| Third-party imports | The CML nodes are offline; standard library only. |
| A function with no spec-section docstring | Can't be traced back to the design. |

---

## 6. Verification & Traceability

1. **Tests first from the spec.** The acceptance tests in §4 are written from the spec tables
   and wire examples, so passing them shows conformance to *this* protocol, not just "it runs".
2. **Line-by-line review.** For each generated function, I read its docstring's spec reference
   and check the code against that section before committing.
3. **Wireshark check (Sprint 5).** Captures on the CML links must show one JSON object per
   line, terminated by `0a`, with only the 11 defined `msg_type` values.
4. **Prompt log.** Each prompt actually sent during Sprint 3 is appended to the Sprint 3 prompt log below with the date,
   what was accepted, what was rejected, and why.

### Sprint 1 design session (2026-10-06)

Summary of the prompts I gave Claude while designing the Sprint 1 documents, and what I did with
the output. Quoted prompts are my words; the notes describe the outcome.

| Date | Prompt | Result | Notes |
|---|---|---|---|
| 2026-10-06 | "Work on Sprint 1, one step at a time" + the Sprint 1 rubric PDF | Blueprint drafted | I set the order (blueprint, then FSM, then AI prompts) and required my approval before anything was pushed. |
| 2026-10-06 | My own restatement of the framing and message spec, for review | Corrected | Claude flagged 3 misunderstandings in my summary: `recv(4096)` does not enforce the max frame size (the buffer check does), EOF is disconnect handling rather than an error, and `FRAME_TOO_LARGE` closes the connection. |
| 2026-10-06 | Review of the proposed defaults (port 5457, 100/200/300 scoring, answer window as the turn) | Accepted | Approved and merged in PR #8. |
| 2026-10-06 | "Create the FSM with Mermaid" | Accepted | I confirmed the lobby rule: a disconnect before the game starts returns to waiting, not a forfeit. Approved and merged in PR #9. |

### Sprint 3 prompt log


| Date | Prompt (§) | Result | Notes |
|---|---|---|---|
| | | | |
