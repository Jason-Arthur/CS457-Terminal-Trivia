# Terminal Trivia: Server Concurrency Architecture

**Course:** CS 457 Computer Networks, Sprint 2
**Author:** Jason Arthur

This document defines how the Terminal Trivia server serves two players at once without blocking,
how it keeps the shared game state consistent, and how it stores and broadcasts scores. It builds
on [`protocol_blueprint.md`](protocol_blueprint.md) (framing, the 11 message types, error codes,
disconnect rules) and [`fsm_specification.md`](fsm_specification.md) (server states and
transitions). Every handler below is triggered by one of those messages, a socket event, or the
server's 15-second answer deadline.

---

## 1. Concurrency Model: Non-Blocking I/O Multiplexing with `selectors`

The server is a **single-threaded event loop** built on Python's `selectors.DefaultSelector`
(`epoll` on the Linux CML nodes). The listening socket and every client socket are set to
non-blocking mode with `sock.setblocking(False)` and registered with one selector. The loop asks
the OS which sockets are ready, services exactly those, and goes back to waiting. No call in the
server ever blocks on one client, so a silent or slow player can never stall the other player.

### 1.1 Why `selectors` instead of a thread per client

| Concern | Thread per client | `selectors` event loop (chosen) |
|---|---|---|
| 15 s answer timer | Needs a separate `threading.Timer` that races with a `MOVE` arriving at the same instant | The deadline is the `select()` timeout; expiry and `MOVE` handling run one after the other in the same thread |
| Shared game state | Every read and write needs `threading.Lock`; a missed lock corrupts scores | Only one handler runs at a time, so each handler is atomic by construction |
| Broadcast to both players | Two worker threads writing while a third (timer) mutates state | One handler builds the message and queues it to both players in the same step |
| Room size | 2 players + rejected extras: threads are fine but unnecessary | 2 players + rejected extras fits easily in one loop |
| Dependencies | stdlib | stdlib (`selectors`, `socket`, `json`, `time`) |

Trivia does very little CPU work per message (compare one letter, add points), so a single thread
is never the bottleneck. The work is waiting on the network, which is exactly what an event loop is
for.

### 1.2 Server objects

| Object | Contents | Lifetime |
|---|---|---|
| `selector` | `selectors.DefaultSelector()`; maps each registered socket to its `Connection` (via `data=`) | Whole process |
| `listen_sock` | TCP socket bound to `0.0.0.0:5457`, `listen(5)`, non-blocking, registered for `EVENT_READ` | Whole process |
| `Connection` | `sock`, `addr`, `inbuf` (bytearray, max 4096 bytes without `\n`), `outbuf` (bytearray of queued frames), `role` (`None`, `"Player_1"`, `"Player_2"`), `name`, `close_after_flush` flag | One TCP connection |
| `Room` | FSM `state`, `players` (role to `Connection`), question list and `q_index`, `open_windows` (set of roles whose window is open), `answers`, `scores`, `wagers`, `history`, `deadline` | One game; reset in `CLEANUP` |

All of these live in the single server thread. Nothing is shared with any other thread.

---

## 2. Connection Lifecycle

### 2.1 Overview

```text
 INIT                bind 0.0.0.0:5457, listen(5), setblocking(False), register EVENT_READ
   |
 ACCEPT              listen_sock readable -> accept() -> new Connection, setblocking(False),
   |                 keepalive options, register EVENT_READ (role = None)
 REGISTRATION        first frame must be CONNECT
   |                   valid, room empty      -> role Player_1, queue LOBBY_WAIT
   |                   valid, Player_1 waiting -> role Player_2, start game
   |                   room already full       -> queue ERROR LOBBY_FULL, close_after_flush
 GAME SERVICING      readable -> recv, frame, dispatch MOVE / WAGER / DISCONNECT
   |                 writable -> flush outbuf
   |                 deadline -> close answer or wager window
 TEARDOWN            GAME_OVER queued to both, close_after_flush on both,
                     unregister + shutdown + close after flush, Room reset -> back to ACCEPT
```

### 2.2 Startup (`INIT`)

1. Load `questions.json` (3 rounds x 3 questions + 1 tiebreaker). If it is missing or invalid, log
   and exit before opening any socket.
2. Create the listening socket, set `SO_REUSEADDR` (so the server can restart while the old port is
   in `TIME_WAIT`), `bind(("0.0.0.0", 5457))`, `listen(5)`, `setblocking(False)`.
3. `selector.register(listen_sock, EVENT_READ, data=None)`. `data=None` is how the loop tells the
   listening socket apart from client sockets.
4. FSM moves to `WAITING_FOR_PLAYERS`.

### 2.3 Accept

When `listen_sock` is readable, the loop calls `accept()` once per readiness event. On a
non-blocking listening socket `accept()` raises `BlockingIOError` if another event already took the
pending connection, and the loop simply ignores that.

For each accepted socket the server:

1. calls `setblocking(False)`;
2. sets the TCP keepalive options from blueprint Section 4.4 (`TCP_KEEPIDLE` 10, `TCP_KEEPINTVL` 5,
   `TCP_KEEPCNT` 3), so a cut CML link is reported within about 25 s;
3. creates a `Connection` with `role = None` and registers it for `EVENT_READ`.

Accepting never depends on the FSM state. A third client is always accepted at the TCP level so the
server can tell it why it is being turned away (`ERROR LOBBY_FULL`) instead of leaving it in the
kernel backlog.

### 2.4 Registration (`CONNECT`)

A connection with `role = None` is unregistered at the game level. The only message it may send is
`CONNECT`; anything else gets `ERROR NOT_JOINED`.

| Room state when `CONNECT` arrives | Server action |
|---|---|
| `WAITING_FOR_PLAYERS`, no players | Validate `player_name`. Set `role = "Player_1"`, store the name, queue `LOBBY_WAIT` (`players_connected: 1`). |
| `WAITING_FOR_PLAYERS`, `Player_1` waiting | Validate name. Set `role = "Player_2"`. In the same handler: queue `GAME_START` to each player (each with its own `your_id`), queue `QUESTION` R1Q1 to both, open both windows, set `deadline = now + 15`. FSM moves to `PLAYER_TURN`. |
| Any `IN_GAME` state, `GAME_OVER` or `CLEANUP` | Queue `ERROR LOBBY_FULL`, set `close_after_flush`. This connection never touches the `Room`. |
| Any, name invalid | Queue `ERROR INVALID_NAME`; the client may retry. |

**Capacity planning.** The room holds exactly two players. Connections beyond that are short-lived:
they are accepted, sent `LOBBY_FULL`, and closed as soon as the error is flushed. `listen(5)` lets
up to five handshakes queue while the loop is busy, which is more than enough for one classroom
topology. Because each client costs only one selector entry and two small buffers (under 8 KB), the
same loop could serve many more connections; the two-player limit is a game rule, not a resource
limit.

### 2.5 Active game servicing

Each loop iteration does three things, always in this order:

```python
while running:
    timeout = room.time_left()             # None when no window is open
    events = selector.select(timeout)
    room.check_deadline()                  # 1. close an expired window first
    for key, mask in events:
        if key.data is None:
            accept_client()                # 2a. new TCP connection
            continue
        conn = key.data
        if mask & selectors.EVENT_READ:
            service_read(conn)             # 2b. recv, frame, dispatch
        if mask & selectors.EVENT_WRITE:
            service_write(conn)            # 3. flush queued frames
    reap_closed_connections()
```

**Reading.** `service_read` calls `recv(4096)` once. `b""` means EOF; `BlockingIOError` means
nothing to read after all (ignored); any of `ConnectionResetError`, `ConnectionAbortedError`,
`TimeoutError` (keepalive) means the peer is gone. All three exits call
`handle_client_disconnect(conn)` from blueprint Section 4.5. Otherwise the bytes are appended to
`conn.inbuf`, complete `\n`-terminated frames are extracted with the Sprint 1 framing rules, and each
frame is parsed, validated and dispatched **one at a time, in arrival order**. If `inbuf` reaches
4096 bytes with no `\n`, the server queues `ERROR FRAME_TOO_LARGE` and treats it as a disconnect.

**Writing.** A non-blocking socket cannot use `sendall()` safely, because `sendall()` raises
`BlockingIOError` part-way through when the kernel send buffer is full and does not report how much
was written. Instead every outgoing message goes through one function:

```python
def queue(conn, msg):
    conn.outbuf += encode_frame(msg)       # json.dumps + b"\n", from the Sprint 1 framing rules
    selector.modify(conn.sock, EVENT_READ | EVENT_WRITE, data=conn)
```

`service_write` calls `sent = conn.sock.send(conn.outbuf)`, deletes the first `sent` bytes, and
switches the socket back to `EVENT_READ` only once `outbuf` is empty. A partial write simply
continues on the next writable event. `BrokenPipeError` or `ConnectionResetError` here also calls
`handle_client_disconnect`. (This replaces the `sendall()` wording in blueprint Section 1.2 for the
server side; the client, which talks to one socket, can keep `sendall()`.)

**Deadline.** `room.deadline` is a `time.monotonic()` value, so wall-clock changes on a CML node
cannot shorten or extend a window. `room.time_left()` returns `max(0, deadline - now)`, which makes
`select()` wake up exactly when the window should close even if no packets arrive.
`check_deadline()` runs before any frame from that iteration is dispatched (see Section 3.4).

### 2.6 Teardown

Teardown has two triggers: the game ending normally (`T9`, `T13`, `T14` in the FSM) and a disconnect
(`handle_client_disconnect`).

1. **Queue the last message.** The FSM enters `GAME_OVER`: `room.deadline = None` (no window can
   fire later), `GAME_OVER` is queued to every player still connected, and `close_after_flush` is set
   on each.
2. **Flush.** `service_write` keeps sending until `outbuf` is empty. A player who is already gone
   fails here and is closed immediately. A flush that is still stuck after 5 s (peer not reading) is
   abandoned so one bad client cannot keep the room locked.
3. **Close.** For each connection with an empty `outbuf` and `close_after_flush`:
   `selector.unregister(sock)`, `sock.shutdown(SHUT_RDWR)`, `sock.close()`, each wrapped in
   `try/except OSError`. Unregistering first means the selector never reports events for a closed
   file descriptor.
4. **Reset.** When both player connections are closed the FSM runs `CLEANUP`: a new `Room` object
   replaces the old one (scores, `q_index`, `answers`, `wagers`, `history` all cleared) and the state
   returns to `WAITING_FOR_PLAYERS`.

A lobby disconnect (only `Player_1` present) skips steps 1 and 2: the socket is closed and the room
goes straight back to waiting with no forfeit. Server shutdown (Ctrl+C) is caught around the loop:
the server queues `GAME_OVER` if a game is running, makes one last flush attempt, closes every
registered socket and the listening socket, and then `selector.close()`.

---

## 3. Thread Safety and State Isolation

### 3.1 The isolation guarantee

There is exactly one thread in the server. The selector only reports readiness; it never runs code
on its own. So at any moment exactly one handler (`accept_client`, `service_read` for one frame,
`service_write`, or `check_deadline`) is running, and it runs to completion before the loop looks
at the next event. **Each handler is an atomic critical section**, with no lock needed.

That guarantee only holds if handlers never give up control half-way. The server follows four rules:

1. **No blocking calls in a handler.** No `sleep()`, no blocking `recv()` or `sendall()`, no
   `input()`. All waiting happens in `selector.select()`.
2. **No threads and no `threading.Timer`.** The answer timer is a deadline value read by the loop.
3. **State changes and the messages that announce them happen in the same handler.** A handler
   that changes the `Room` queues the matching broadcast before it returns, so no other handler can
   observe the new state before both players have it queued.
4. **Sends never call back into game logic.** `queue()` only appends bytes. A send failure is
   discovered later in `service_write`, as its own event, so a broken pipe can never interrupt a
   handler that is in the middle of scoring.

### 3.2 Shared resources

These are the objects that more than one event can touch, the race each would have in a
multi-threaded server, and why it cannot happen here.

| Shared resource | Touched by | Race it would have with threads | Why the event loop prevents it |
|---|---|---|---|
| `room.state` (FSM state) | every dispatch, `check_deadline`, `handle_client_disconnect` | Timer thread moves to `EVALUATE_MOVE` while a worker thread is recording a `MOVE`; a disconnect forfeits a game that just ended | Only one handler runs at a time and every handler checks `room.state` first, so transitions happen in one strict order |
| `room.players` and each `Connection.role` (client slot table) | `CONNECT`, disconnects, `CLEANUP` | Two clients send `CONNECT` at once and both become `Player_2`, or one becomes `Player_2` after the game started | `CONNECT` frames are dispatched one at a time; the second one sees the slot already filled and gets `LOBBY_FULL` |
| `room.open_windows` and `room.answers` | `MOVE`, `check_deadline` | Both players' `MOVE`s land together and both see "other player not answered yet", so `STATE_UPDATE` is sent twice or never | The second `MOVE` is dispatched after the first has finished, sees both answers in, and is the only one that triggers scoring |
| `room.deadline` | open window, `check_deadline`, `GAME_OVER` | A timer fires after the game ended and moves it back to `EVALUATE_MOVE` | `GAME_OVER` sets `deadline = None`; `check_deadline` does nothing when it is `None` |
| `room.q_index` and current `question_id` | `EVALUATE_MOVE`, `CHECK_WIN_DRAW`, `MOVE` validation | A late `MOVE` is checked against the old `question_id` while another thread advances to the next question, and is scored on the wrong question | Advancing the index and queuing the next `QUESTION` happen inside one handler; a later `MOVE` with the old id gets `OUT_OF_TURN` |
| `room.scores` and `room.history` | `EVALUATE_MOVE`, `WAGER_REQUEST`, `GAME_OVER` | Read-modify-write (`scores[p] += points`) lost when two threads update together | Only `EVALUATE_MOVE` writes scores, once per question, inside one handler |
| `room.wagers` | `WAGER`, `check_deadline` | Wager recorded after the timer already sent `QUESTION TB` | Same pattern as answers: the window is closed before the `TB` question is queued |
| `Connection.inbuf` | `service_read` for that connection only | Two threads reading one socket and interleaving partial frames | Each connection's buffer is read by one handler at a time; frames are dispatched in arrival order |
| `Connection.outbuf` | `queue()` (any handler), `service_write` | Two broadcasts interleave bytes on the wire and the client sees half a frame | `queue()` appends a whole frame at once; `send()` only ever takes bytes from the front |
| selector registration | accept, `queue()`, teardown | Writing to or closing a socket another thread is still using | Unregister happens before close, in the one thread that also does every `send()` |

### 3.3 Atomic step: one `MOVE`

The longest critical section is the `MOVE` that completes a round. Everything below runs inside one
call to `dispatch(conn, frame)`; no other frame, timer or disconnect can run in between.

```text
dispatch(MOVE from Player_1, question_id R2Q1, answer "C")
  state is PLAYER_TURN?                    no  -> queue ERROR OUT_OF_TURN to Player_1, return
  question_id == current id?               no  -> queue ERROR OUT_OF_TURN to Player_1, return
  Player_1 in open_windows?                no  -> queue ERROR OUT_OF_TURN to Player_1, return
  answer in A-D?                           no  -> queue ERROR INVALID_ANSWER, window stays open, return
  answers[Player_1] = "C"; open_windows.remove(Player_1)
  open_windows still has Player_2?        yes -> return (no reply to Player_1; see blueprint 3.6)
  close_round():                                   # both answered
      deadline = None
      score both answers, update scores, append to history
      build STATE_UPDATE once, queue it to Player_1 and Player_2
      CHECK_WIN_DRAW: queue next QUESTION (open windows, new deadline)
                      or WAGER_REQUEST or GAME_OVER
  return
```

`check_deadline()` calls the same `close_round()` with the missing answer recorded as `null`, so
there is exactly one code path that scores a question.

### 3.4 The deadline versus a late `MOVE`

This is the one place where two events really do compete: a `MOVE` that arrives right as the 15 s
window ends. The rule is **the server's monotonic clock at the moment of processing decides**, and
the loop always calls `check_deadline()` before dispatching any frame read in the same iteration.

- If the deadline has passed, `close_round()` runs first, the window is gone, and the late `MOVE`
  then sees a new `question_id` (or no open window) and gets `ERROR OUT_OF_TURN`.
- If the deadline has not passed, the `MOVE` is recorded normally.

Either way the result is deterministic, the answer is never counted twice, and `STATE_UPDATE` is
sent exactly once per question. The client's `timestamp` field is never used for this decision
(blueprint Section 2).

### 3.5 Multiple frames in one `recv()`

Because of TCP coalescing (blueprint Section 1.4), one `recv()` can hold, for example, a `MOVE` and
a `DISCONNECT`. Frames are dispatched in order, and each one sees the state left by the one before
it. If a frame causes a disconnect or `GAME_OVER`, the rest of that buffer is dropped.

---

## 4. Turn Enforcement

Trivia does not alternate turns. A player's turn is their own **answer window**: it opens when the
server queues `QUESTION` and closes on that player's first valid `MOVE` or at the deadline. A `MOVE`
is accepted only if all four checks in Section 3.3 pass, in that order. Every rejection queues an
`ERROR` to the sender only and changes nothing in the `Room`:

| Rejected `MOVE` | Error | State effect |
|---|---|---|
| No window open (`EVALUATE_MOVE`, `CHECK_WIN_DRAW`, `WAGER_TURN`, `GAME_START`) | `OUT_OF_TURN` | None |
| Stale `question_id` | `OUT_OF_TURN` | None |
| Second `MOVE` for the same question | `OUT_OF_TURN` | First answer stands |
| Answer not `A` to `D` | `INVALID_ANSWER` | Window stays open |
| `player_id` does not match the socket's role | `ID_MISMATCH` | None |

The same pattern guards `WAGER` (`OUT_OF_TURN` outside `WAGER_TURN` or on a second wager,
`INVALID_WAGER` for a bad amount). The opponent never sees an error caused by the other player.

---

## 5. Score and Leaderboard Synchronization

### 5.1 Storage

Scores live only in server memory, in the `Room`:

```python
scores  = {"Player_1": 0, "Player_2": 0}          # running totals, the only source of truth
history = []                                      # one entry per closed question, in order
wagers  = {"Player_1": None, "Player_2": None}    # only used for the tiebreaker
```

Each `history` entry records `question_id`, `round`, `correct_answer`, and for each player the
`answer` (or `null`), `correct`, and `points_awarded`. `scores` is always equal to the sum of
`points_awarded` in `history`, which makes it easy to check in Sprint 3 tests. Clients never send
scores; they only display what the server sends. All of this is discarded in `CLEANUP`.

Points come from the blueprint: 100 / 200 / 300 for rounds 1 / 2 / 3; for the tiebreaker, a correct
answer adds `2 x wager` and a wrong or missing answer subtracts `wager`.

### 5.2 Broadcast logic

The score table is pushed to both clients each time a question closes, in `close_round()`:

1. Score both answers and update `scores` and `history`.
2. Build **one** `STATE_UPDATE` payload (`question_id`, `correct_answer`, `results` for both players,
   `scores`, `next`) and encode it once.
3. Append the same bytes to `Player_1.outbuf` and `Player_2.outbuf` in the same handler.
4. Queue whatever comes next (`QUESTION`, `WAGER_REQUEST` or `GAME_OVER`) behind it, in the same
   handler.

At the end of the game `GAME_OVER` carries `final_scores`, built from the same `scores` dictionary.

### 5.3 What "both clients receive updates synchronously" means here

TCP cannot make two clients on different subnets receive bytes at the same instant. The guarantee
this design gives is stronger and more useful:

- **Same snapshot.** Both clients receive byte-identical `STATE_UPDATE` frames built from one
  scoring pass, so their screens can never disagree on a score.
- **Same moment in game time.** Both copies are queued in the same atomic handler, before the server
  processes any other event. No `MOVE`, deadline or disconnect can change the state between the two
  queue operations.
- **Same order.** Each connection's `outbuf` is FIFO and TCP preserves order, so each client sees
  `STATE_UPDATE` for R2Q1 before `QUESTION` R2Q2, and never a question before the result of the one
  before it.
- **Fair answer windows.** Both players' windows close in the same handler, and the next `QUESTION`
  is queued to both in the same handler, so neither player gets a head start from the server side.
  Any remaining difference is network delay between the CML subnets, which will show up in the
  Sprint 5 Wireshark captures.

If one player's socket fails while the broadcast is being flushed, that is a normal disconnect
(Section 2.6): the remaining player gets `GAME_OVER` with `FORFEIT`.

---

## 6. Traceability

| This document | Sprint 1 source |
|---|---|
| Port 5457, newline framing, 4096-byte limit, `FRAME_TOO_LARGE` | Blueprint Section 1 |
| `CONNECT`, `LOBBY_WAIT`, `GAME_START`, `LOBBY_FULL`, `NOT_JOINED` registration rules | Blueprint 3.2 to 3.4, FSM T2 to T4 |
| Answer windows, `OUT_OF_TURN`, `INVALID_ANSWER`, no reply to a valid `MOVE` | Blueprint 3.1, 3.6, 3.10; FSM 3.2 |
| `STATE_UPDATE` payload and scoring values | Blueprint 3.9, 3.1 |
| Wager rules | Blueprint 3.7, 3.8; FSM T10 to T12 |
| Disconnect handling, keepalive, forfeit | Blueprint Section 4; FSM 3.3 |
| `sendall()` replaced by `outbuf` + `send()` on the server | Change from blueprint 1.2, required by non-blocking sockets |
