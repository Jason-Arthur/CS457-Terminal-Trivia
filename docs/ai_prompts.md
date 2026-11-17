# Terminal Trivia: AI Prompting & Constraint Strategy

**Course:** CS 457 Computer Networks, Sprint 1 (updated Sprints 2 and 3)
**Author:** Jason Arthur

This document records how AI coding assistants are used on this project and, more importantly,
how they are constrained. The rule is simple: **the AI implements the spec; it never designs it.**
The two spec documents are the source of truth:

- [`protocol_blueprint.md`](protocol_blueprint.md): framing, envelope, the 11 message schemas,
  error codes, socket lifecycle.
- [`fsm_specification.md`](fsm_specification.md): server states, transitions T1 to T16, invalid
  input handling, disconnect handling.
- [`concurrency_design.md`](concurrency_design.md) (Sprint 2): single-threaded `selectors` event
  loop, connection lifecycle, state isolation rules, score storage and broadcast.
- [`sequence_diagram.md`](sequence_diagram.md) (Sprint 2): message flow for a full game, including
  every `ERROR` branch.

If the AI produces anything that is not in those documents (a new message type, a different
framing rule, a renamed field, a state that doesn't exist), the output is rejected, not the spec.
A spec change only happens when I decide it, in a separate commit to the spec documents first.

---

## 1. Tools & Disclosure

| Tool | Used for |
|---|---|
| Claude (Claude Code) | Drafting the Sprint 1 and Sprint 2 design documents with me, and generating the Sprint 3 code under the base system prompt below. |

**Disclosure:** Claude helped draft `protocol_blueprint.md` and `fsm_specification.md`. I reviewed
every section, made the design decisions (port 5457, newline-delimited JSON, simultaneous answer
window as the "turn", 100/200/300 scoring, lobby disconnects returning to waiting), and approved
each document in its PR before merging (PR #8 and PR #9).

In Sprint 2 Claude helped draft `concurrency_design.md` and `sequence_diagram.md`. I chose the
concurrency model (`selectors` I/O multiplexing over a thread per client), reviewed both documents,
and merged them in PR #16.

In Sprint 3 Claude generated `protocol.py`, `server.py`, `client.py`, `questions.json` and the
tests in `tests/` from the three spec documents. Where the specs did not decide something, Claude
asked first, and I made each of those design choices (listed in the Sprint 3 log below). I
reviewed the code and test results before it was pushed.

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
- docs/protocol_blueprint.md, docs/fsm_specification.md and docs/concurrency_design.md
  (provided below) are the complete and binding specification. Implement exactly what they say.
- If something you need is not specified, STOP and ask. Do not invent behavior.
- Never add, rename, or remove a message type, payload field, error code, or FSM state.

HARD CONSTRAINTS
- Python 3 standard library only (socket, threading, selectors, json, time, logging,
  struct, random). No third-party packages. The code runs on Cisco Modeling Labs nodes
  with no internet access.
- Transport: TCP over IPv4. Server listens on 0.0.0.0:5457.
- Framing: newline-delimited JSON exactly as in protocol_blueprint.md §1.
  * Encode: json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n".
  * Client sends with sendall(). The server never calls sendall(): it appends the frame
    to that connection's outbuf and flushes with send() when the socket is writable
    (concurrency_design.md §2.5).
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
- Server concurrency: ONE thread running a selectors.DefaultSelector loop over
  non-blocking sockets (concurrency_design.md §1, §2.5). No threading, no
  threading.Timer, no sleep() or other blocking call inside a handler. The 15 s answer
  window is a time.monotonic() deadline used as the select() timeout, checked before
  any frame is dispatched (§3.4).
- Every recv(), send() and sendall() is wrapped in
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

## 4. Rejection Checklist

AI output is rejected and re-prompted (with the failing item quoted) if any of these appear:

| Red flag | Why it violates the spec |
|---|---|
| `data = sock.recv(1024)` followed directly by `json.loads(data)` | Assumes one `recv()` is one message; ignores §1.1 coalescing and fragmentation. |
| Client: `sock.send(...)` instead of `sendall` | Can write a partial frame (§1.2). |
| Server: `sendall()` on a non-blocking socket, or `send()` without keeping the unsent rest in `outbuf` | Raises `BlockingIOError` part-way or drops bytes (concurrency_design.md §2.5). |
| `threading`, `threading.Timer`, or `socketserver.ThreadingTCPServer` in the server | The design is a single-threaded `selectors` loop; threads reintroduce the races in concurrency_design.md §3.2. |
| `time.sleep()`, blocking `recv()`, or `input()` inside a server handler | Stalls the event loop for both players (concurrency_design.md §3.1). |
| Scoring a question anywhere other than `close_round()` | Risks scoring a question twice (concurrency_design.md §3.3). |
| A `msg_type` or error code not in the closed lists | Breaks the protocol contract (§3, §3.10). |
| `json.dumps(..., indent=...)` on the wire | Multi-line JSON would break newline framing. |
| A receive loop with no `if not data` / `None` check | Spins forever at 100% CPU after EOF (§4.3). |
| Bare `except:` or `except Exception: pass` around socket code | Hides disconnects instead of triggering `handle_client_disconnect` (§4.4). |
| Scoring, answer checking, or the authoritative timer on the client | The server is authoritative (FSM §2). |
| A disconnect in the lobby treated as a forfeit | FSM §3.3 says the server returns to waiting. |
| Third-party imports | The CML nodes are offline; standard library only. |
| A function with no spec-section docstring | Can't be traced back to the design. |

---

## 5. Verification & Traceability

1. **Tests first from the spec.** Acceptance tests are written from the spec tables
   and wire examples, so passing them shows conformance to *this* protocol, not just "it runs".
2. **Line-by-line review.** For each generated function, I read its docstring's spec reference
   and check the code against that section before committing.
3. **Wireshark check (Sprint 5).** Captures on the CML links must show one JSON object per
   line, terminated by `0a`, with only the 11 defined `msg_type` values.
4. **Prompt log.** The prompts sent during each sprint are logged below under that sprint's heading, with the date,
   what was accepted, what was rejected, and why.

### Sprint 1 design session (2026-10-06)

Summary of the prompts I gave Claude while designing the Sprint 1 documents, and what I did with
the output. Quoted prompts are my words; the notes describe the outcome.

| Date | Prompt | Result | Notes |
|---|---|---|---|
| 2026-10-06 | "we are working sprint 1" and "Lets go one step at a time", + the Sprint 1 rubric PDF | Blueprint drafted | I set the order (blueprint, then FSM, then AI prompts) and required my approval before anything was pushed. |
| 2026-10-06 | My own restatement of the framing and message spec, for review | Corrected | Claude flagged 3 misunderstandings in my summary: `recv(4096)` does not enforce the max frame size (the buffer check does), EOF is disconnect handling rather than an error, and `FRAME_TOO_LARGE` closes the connection. |
| 2026-10-06 | Review of the proposed defaults (port 5457, 100/200/300 scoring, answer window as the turn) | Accepted | Approved and merged in PR #8. |
| 2026-10-06 | "can you create the FSM with mermaid?" | Accepted | I confirmed the lobby rule: a disconnect before the game starts returns to waiting, not a forfeit. Approved and merged in PR #9. |

### Sprint 2 design session (2026-10-27)

Summary of the prompts I gave Claude while designing the Sprint 2 documents, and what I did with
the output. Quoted prompts are my words; the notes describe the outcome.

| Date | Prompt | Result | Notes |
|---|---|---|---|
| 2026-10-27 | "time to work on sprint two, just layout what you read here first", + the Sprint 2 assignment and rubric | Breakdown only | Claude mapped the 150 rubric points to two files and flagged where the rubric's tic-tac-toe framing (one active player, a reply to every move) differs from our simultaneous answer windows. |
| 2026-10-27 | "the real due date is 31, ignore the 18. lets go selectors." | Model chosen | I picked `selectors` over a thread per client, because the 15 s window becomes the `select()` timeout and removes the MOVE-vs-timer race. |
| 2026-10-27 | Review comment on the section 1 heading: "add multiplexing here" | Accepted | Heading now names the model as non-blocking I/O multiplexing, matching the rubric's wording. |
| 2026-10-27 | "-> use mermaid syntax for this" (on assignment section 2.2) and "use this reference for the sequence diagram: https://mermaid.js.org/syntax/sequenceDiagram.html" | Accepted | Diagram uses only syntax from that page, covers all 11 message types, and was rendered with Mermaid 11 before pushing to check for syntax errors. |
| 2026-10-27 | "this looks good, lets make the issue(s) and open the pr" | Merged | Issues #14 and #15, both closed by PR #16. |
| 2026-10-27 | "lets update sow and ai prompts" | PR #18 | SOW section 3 filled in. Base prompt and rejection checklist updated for the `selectors` design: the server no longer uses `sendall()` (one change from blueprint §1.2), and threads in the server are now a rejection item. |
| 2026-10-27 | "can you also add the two new files to the readme \"repository contents\" section. add this last prompt to the ai prompts, and send the pr." | PR #18 | README lists `concurrency_design.md` and `sequence_diagram.md`; this row added to the log. |
| 2026-10-27 | "review this to ensure all is completed", + the Sprint 2 rubric file | Checked | Every rubric criterion was met on main. Claude noted that `concurrency_design.md` did not link to the diagram. |
| 2026-10-27 | "add a link to the sequence diagram at the top of the concurrency design file" | PR #20 | Link added under the author line. |

### Sprint 3 implementation session (2026-11-16 to 2026-11-17)

Summary of the prompts I gave Claude while writing the Sprint 3 code, and what I did with the
output. Quoted prompts are my words; the notes describe the outcome.

| Date | Prompt | Result | Notes |
|---|---|---|---|
| 2026-11-16 | "can you read this", + the Sprint 3 assignment page | Breakdown only | Claude listed the deliverables (`client.py`, `server.py`, execution log), the real due date, and how the 100 rubric points split across CLI parsing, DNS handling, framing, concurrency/FSM and code quality. |
| 2026-11-16 | "start implementation. refer to design specs in the repo made in the last two sprints. ask clarification before making design changes or choices." | Questions first | Before writing the client, Claude listed 5 places where the specs were silent or conflicted with the assignment (port flag, client input loop, player name entry, question bank, PR split). It then drafted `protocol.py` and `server.py`, which the specs fully define, and tested a full game with raw sockets. |
| 2026-11-16 | "1. make -p required and check its a valid port. 2. use the same selectors, it will only run on linux and mac 3. prompt the player for the name after connecting, and retry if INVALID_NAME 4. write some example questions as you recommended for now, I may or may not change them later. 5. use recommendation." | Accepted | `-p` is required on both programs and must be 1024 to 65535. The client uses one `selectors` loop over the socket and stdin (no threads, so not Windows). The name is checked by the server, and the client asks again on `INVALID_NAME`. `questions.json` holds 10 networking questions. Two smaller gaps keep placeholders for now: a repeated `CONNECT` from a joined player gets `OUT_OF_TURN`, and a server Ctrl+C closes the sockets without `GAME_OVER` (no defined `reason` fits). |
| 2026-11-16 | Review of the finished code | Checked | 18 spec-derived tests pass, `pycodestyle` reports no PEP 8 issues, and a scripted two-client game on localhost was captured as `docs/sprint3_execution_log.md`. |
| 2026-11-17 | "all the folders are in place, can you run the tests from here" | Passed | All 18 tests pass on my Mac (Python 3.14) in my local copy of the repo. |
| 2026-11-17 | "check this can run while configured on a cml alpine linux setup" | Passed | The tests pass on Alpine Linux with Python 3.8 and 3.12, and a full game ran across three separate Alpine hosts, with the clients finding the server by DNS name (`-i server.arthur.edu`). Open risk: the offline CML Alpine nodes need `python3` installed. |
| 2026-11-17 | "great, update the docs, include an explicit \"how to\" with the commands entered. make it exceptionally clear so even a non-technical person could start the trivia game." | Docs updated | README has a step-by-step "How to Play" with every command, the expected output and a troubleshooting table. SOW section 4 is filled in, and this log is updated. |
| 2026-11-17 | "be sure to add this to the ai prompts and the message before" (replying to my answer "make -p required and check its a valid port.") | Logged | The clarification table below was added. `tests/test_cli.py` was also added: 4 tests that check both programs reject a missing, out-of-range or non-numeric `-p`, bringing the total to 22. |
| 2026-11-17 | "can we settle those now?" (about the two open placeholders) | Specs updated | Re-sent `CONNECT` stays `OUT_OF_TURN` and is now written into blueprint §3.10, FSM §3.2 and concurrency §2.4. Server Ctrl+C mid-game now sends `GAME_OVER` `DRAW` / `server_shutdown`; added to blueprint §3.12, the FSM diagram and §3.3, and concurrency §2.6 before the code changed. 2 new tests (24 total). |

**Sprint 3 clarification questions.** Claude asked these before writing `client.py`, as the base
prompt requires ("If something you need is not specified, STOP and ask"). The questions are
Claude's, paraphrased. Answers 1 to 5 are my words from the reply above; 6 and 7 are the options
I picked on decision cards.

| # | Gap Claude found in the specs (paraphrased) | Claude's recommendation | My answer (verbatim) |
|---|---|---|---|
| 1 | The specs fix port 5457, but the assignment requires a `-p` flag on both programs. | Make `-p` required, check it is 1024 to 65535, use 5457 in examples. | "make -p required and check its a valid port." |
| 2 | The specs do not say how the client reads the keyboard while still listening to the server (for example a `STATE_UPDATE` after a 15 s timeout). | The same `selectors` loop over the socket and stdin; works on Linux and macOS but not Windows. | "use the same selectors, it will only run on linux and mac" |
| 3 | The assignment lists only `-h -i -p`, so where does the player name come from? | Prompt after connecting; ask again on `INVALID_NAME`. | "prompt the player for the name after connecting, and retry if INVALID_NAME" |
| 4 | The specs say "3 rounds x 3 questions + 1 tiebreaker" but give no questions or file format. | Claude writes 10 networking questions in a simple JSON layout for review. | "write some example questions as you recommended for now, I may or may not change them later." |
| 5 | How to split the work into issues and PRs, and who produces the execution log. | One issue/PR for the code and tests, one for the docs and execution log; Claude runs two clients on localhost for the log. | "use recommendation." |
| 6 | No error code covers a joined player sending `CONNECT` again. | Placeholder: `OUT_OF_TURN`, message ignored. | Picked on a decision card (2026-11-17): "Keep OUT_OF_TURN, document it". |
| 7 | The concurrency design says to send `GAME_OVER` on server Ctrl+C, but no `reason` value fits. | Send `GAME_OVER` with a new `reason`, `server_shutdown` (`DRAW`, no winner). | Picked on a decision card (2026-11-17): "GAME_OVER with reason server_shutdown". |
