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
| Claude (Claude Code) | Drafting the Sprint 1 design documents with me, and generating code under the base system prompt below. |

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

## 4. Rejection Checklist

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
