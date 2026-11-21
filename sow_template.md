# CS 457 Project Statement of Work (SOW) & Protocol Specification Template

**Student Name:** Jason Arthur 
**Date:** 2026-09-22
**Course:** CS 457 - Computer Networks  
**Target Server Domain:** `server.arthur.edu`  

---

## 1. Game Selection & Scope (Sprint 0)

> Planning is going to be an iterative process through the sprints so you don't have to have all the details now. Focus on big overview concepts. You will be updating the SOW as we plan.
> You have a lot of freedom to choose a game. There are a couple caveats.  

> - It must run in the console. The lab nodes won't be able to handle extensive graphics.
> - It has to be self-contained. You can use a internet-connector to download you code, but because the architecture must run 5 nodes you won't be able to run 
> - You are encouraged to use python, but I'm not going to make it a strict requirement. The instructor and TA's ability to help with C or Rust, etc will be diminished in other languages.

### 1.1 Game Overview
- **Chosen Game:** Terminal Trivia
- **Player Capacity:** 2 Players (Simulated via 2 CML Client nodes)
- **Game Summary:** 2 players will play three rounds of trivia, each round will have 3 multuple choice trivia questions. Each round the questions will become more difficult but will also reward more points for correct responses. The winner is determined by who ends the game with the most points.

### 1.2 Core Game Rules & Win/Draw Conditions
- **Turn Mechanics:** Roles are assigned by connection order, the first player to connect will be player 1 and the second to connect will be player 2. Both players will answer questions simultaneously. Each player will have the question broadcast to them and each given a countdown of 15 seconds to respond, after the countdown the server will reveal the correct answer and update each players score accordingly.
- **Victory Condition:** A player wins the game by gaining more points than their opponent.
- **Draw/Tie Condition:** In the event that both players end the final round with equal points, a final trivia question will be given, and each player will wager some amount of their points, from 0 to their total points after the final round, if the player gets the question right, the points they bet are doubled and added to their score. If the player is wrong, the points bet are taken from their total score. If after this the players are still tied, a draw is declared and the game ends.
---

## 2. Application-Layer Messaging Protocol Blueprint (Sprint 1 Deliverable)

> Full specifications: [`docs/protocol_blueprint.md`](docs/protocol_blueprint.md),
> [`docs/fsm_specification.md`](docs/fsm_specification.md), and
> [`docs/ai_prompts.md`](docs/ai_prompts.md).

### 2.1 Message Transport & Serialization Format
- **Transport Protocol:** TCP over IPv4, server listening on port `5457`
- **Serialization Format:** JSON object, UTF-8 encoded
- **Framing Mechanism:** Newline-delimited (`\n`, `0x0A`) JSON. Each message is one compact JSON object followed by `\n`; max frame size is 4096 bytes. The receiver keeps a per-connection byte buffer across `recv()` calls and extracts one frame per `\n`, handling both coalescing and fragmentation (blueprint §1).

### 2.2 Message Schema Definitions

Every message uses the same envelope: `msg_type`, `player_id` (`"Player_1"` / `"Player_2"`, `null` before `CONNECT`, `"SERVER"` from the server), `payload`, and `timestamp` (blueprint §2).

#### Message Types:
1. `CONNECT` (Client -> Server): Join the game room with a display name.
2. `LOBBY_WAIT` (Server -> Client): Tell Player 1 they are connected and waiting for Player 2.
3. `GAME_START` (Server -> Clients): Game begins; assigns roles `Player_1` / `Player_2` by connection order.
4. `QUESTION` (Server -> Clients): Next question, its point value (100 / 200 / 300 by round), and the 15 s answer window.
5. `MOVE` (Client -> Server): The player's answer (`A` to `D`) for the current question.
6. `WAGER_REQUEST` (Server -> Clients): Scores tied after round 3; ask each player for a tiebreaker wager.
7. `WAGER` (Client -> Server): The player's wager, from 0 to their current score.
8. `STATE_UPDATE` (Server -> Clients): Reveal the correct answer, both players' answers, and updated scores.
9. `ERROR` (Server -> Client): Reject a malformed, invalid, or out-of-turn message without ending the game.
10. `DISCONNECT` (Client -> Server): Player quits on purpose.
11. `GAME_OVER` (Server -> Clients): Final result (`WIN`, `DRAW`, or `FORFEIT`) and final scores.

#### Example JSON Protocol Schema:
```json
{
  "msg_type": "MOVE",
  "player_id": "Player_1",
  "payload": {
    "question_id": "R1Q1",
    "answer": "B"
  },
  "timestamp": 1727000015
}
```

On the wire this is sent as a single line terminated by `\n`:

```text
{"msg_type":"MOVE","player_id":"Player_1","payload":{"question_id":"R1Q1","answer":"B"},"timestamp":1727000015}\n
```

---

### 2.3 Game State Machine (FSM) Design (Sprint 1 Deliverable)
- **State Transitions:** `INIT` -> `WAITING_FOR_PLAYERS` -> `GAME_START` -> (`PLAYER_TURN` -> `EVALUATE_MOVE` -> `CHECK_WIN_DRAW`) x 9 questions -> `GAME_OVER` -> `CLEANUP` -> back to `WAITING_FOR_PLAYERS`. If scores are tied after round 3, `CHECK_WIN_DRAW` -> `WAGER_TURN` -> `PLAYER_TURN` (tiebreaker question) before `GAME_OVER`.
- **Turn Model:** Both players answer the same question at once. A player's "turn" is the 15 s answer window, timed by the server. A `MOVE` outside that window, or a second answer to the same question, gets `ERROR OUT_OF_TURN`.
- **Invalid Input:** Malformed, invalid, or out-of-turn messages get an `ERROR` and leave the state unchanged; the server loop never crashes.
- **Disconnects:** A disconnect (`DISCONNECT`, TCP EOF, RST, or keepalive timeout) during a game ends it as a `FORFEIT` win for the opponent, then `CLEANUP`. A disconnect in the lobby just returns to `WAITING_FOR_PLAYERS`.
- **Diagram:** Mermaid `stateDiagram-v2` in [`docs/fsm_specification.md`](docs/fsm_specification.md).

---

## 3. Game Behavior & Server Concurrency Architecture (Sprint 2 Deliverable)

> Full specifications: [`docs/concurrency_design.md`](docs/concurrency_design.md) and
> [`docs/sequence_diagram.md`](docs/sequence_diagram.md).

### 3.1 Server Concurrency Strategy
- **Architecture Choice:** Non-blocking I/O multiplexing with `selectors` (`DefaultSelector`, `epoll` on the Linux CML nodes). One thread runs an event loop over the listening socket and every client socket, all set to `setblocking(False)`. A silent or slow player never blocks the other, and the 15 s answer window is a `time.monotonic()` deadline passed as the `select()` timeout, so no timer thread is needed.
- **Connection Lifecycle:** bind and `listen()` on port `5457` -> `accept()` and register each client -> `CONNECT` assigns `Player_1` / `Player_2` (a third client gets `ERROR LOBBY_FULL` and is closed) -> the loop services reads, writes and the deadline -> `GAME_OVER` is flushed, sockets are unregistered and closed, and the room resets for the next pair.
- **Synchronization Logic:** No locks are needed, because only one handler runs at a time and each one runs to completion before the loop takes the next event. That makes every handler an atomic critical section over the shared state (FSM state, client slot table, open answer windows, answers, deadline, question index, scores, wagers, per-connection buffers). The rules that keep this true: no blocking calls or threads in the server, every state change queues its broadcast in the same handler, and sends only append to a per-connection `outbuf` (flushed with `send()` when writable) so a send failure can never interrupt game logic.

### 3.2 State & Score Synchronization Across Clients
- **Turn Enforcement:** Both players answer the same question at once, so a player's "turn" is their own answer window. A `MOVE` is accepted only if the game is in `PLAYER_TURN`, its `question_id` is the current one, that player's window is still open, and the answer is `A` to `D`. Otherwise the sender alone gets `ERROR OUT_OF_TURN` or `INVALID_ANSWER` and the game state is unchanged. The deadline is checked before any frame is processed, so a late `MOVE` is rejected and never counted. A valid `MOVE` gets no reply, so the opponent cannot learn the result early.
- **Score & Board Synchronization:** Scores live only on the server (`scores` totals plus a per-question `history`). When a question closes (both answered, or the deadline passed), one `close_round()` scores it once, builds one `STATE_UPDATE` with the full score table, and queues the identical bytes to both clients in the same handler, followed by the next `QUESTION`, `WAGER_REQUEST` or `GAME_OVER`. Both clients therefore always show the same scores, in the same order. Mermaid sequence diagram: [`docs/sequence_diagram.md`](docs/sequence_diagram.md).

---

## 4. Coding & AI Implementation Plan (Sprint 3)

- **Permitted AI Tools:** Claude (Claude Code), used under the base system prompt and rejection checklist in [`docs/ai_prompts.md`](docs/ai_prompts.md). Every prompt and decision for Sprint 3 is logged there.
- **AI Prompting & Constraint Strategy:** The three spec documents ([`protocol_blueprint.md`](docs/protocol_blueprint.md), [`fsm_specification.md`](docs/fsm_specification.md), [`concurrency_design.md`](docs/concurrency_design.md)) are the binding source of truth. The AI implements them and does not design. Where the specs were silent (the client's input loop, how the player name is entered, the port flag, the question bank), it stopped and asked, and I made each call before any code depended on it. Every function's docstring names the spec section it implements, the code uses the closed lists of 11 message types, 10 error codes and 9 FSM states, and the code is Python 3 standard library only, because the CML nodes are offline.
- **Implementation Risk Management:** The code is split into small modules, built in order: `protocol.py` (framing and validation), then `server.py` (the `selectors` loop and FSM), then `client.py` (CLI, DNS handling, terminal UI). Each was tested before the next was started. 24 automated tests in `tests/` are taken directly from the spec's wire examples, error and transition tables, and the `-p` port rules. The code was also run on Alpine Linux (the CML node OS) and as a full two-client game on localhost ([`docs/sprint3_execution_log.md`](docs/sprint3_execution_log.md)) before submission.

---

## 5. CML Multi-Subnet Topology & Wireshark Deployment Plan (Sprint 4 & 5 Deliverable)

> For now you can use the topology below. We may update this when we get to defining subnets.

Sprint 4 uses a **single Cisco IOSv router (`JA-R1`)** that connects all three subnets directly, so
no backbone link or static routes are needed. This replaces the earlier two-router plan. The
paste-ready router configuration is [`cml/r1_config.txt`](cml/r1_config.txt), and the evidence is in
[`sprint_4_network_report.md`](sprint_4_network_report.md).

### 5.1 Subnet & Router Design
- **Subnet A (Client 1, `JA-Client1`):** `192.168.10.0/24`, gateway `192.168.10.1` on `JA-R1` `Gi0/1`
- **Subnet B (Client 2, `JA-Client2`):** `192.168.11.0/24`, gateway `192.168.11.1` on `JA-R1` `Gi0/2`
- **Subnet C (Game Server, `JA-Server`):** `192.168.20.0/24`, gateway `192.168.20.1` on `JA-R1` `Gi0/3`. The server has the static address `192.168.20.100/24` with `default via 192.168.20.1`.
- **Routing:** `ip routing` on `JA-R1`. All three subnets are connected (`C`) routes.
- **Internet access:** `JA-R1` `Gi0/0` gets its address by DHCP from a CML External Connector (NAT mode). NAT overload (`access-list 1` for all three subnets, `ip nat inside source list 1 interface GigabitEthernet0/0 overload`) lets the nodes install packages and clone the repository.

### 5.2 DHCP Pools & DNS Configuration Plan
- **`CLIENT1_POOL`:** `network 192.168.10.0 255.255.255.0`, `default-router 192.168.10.1`, `dns-server 192.168.10.1`
- **`CLIENT2_POOL`:** `network 192.168.11.0 255.255.255.0`, `default-router 192.168.11.1`, `dns-server 192.168.11.1`
- **Exclusions:** `ip dhcp excluded-address 192.168.10.1` and `192.168.11.1`, so R1 never leases its own gateway address.
- **Clients:** Alpine nodes request a lease with `udhcpc -i eth0`.
- **Authoritative DNS on `JA-R1`:** `ip domain lookup`, `ip dns server` and `ip host server.arthur.edu 192.168.20.100`. Other names are forwarded to the External Connector's DNS server learned on `Gi0/0` (no `ip name-server 8.8.8.8`, because external UDP/53 is blocked on the CSU network).

### 5.3 Deployment Strategy & Wireshark Trace Capture
- **CML Deployment Strategy:** `server.py` runs on `JA-Server` (`192.168.20.100`) and `client.py` runs on `JA-Client1` and `JA-Client2` with `-i server.arthur.edu -p 5457`. Python 3 and git are installed on the Alpine nodes with `apk add python3 git` through the NAT uplink.
- **Cisco Infrastructure Configuration:** one router, `JA-R1`, provides the three gateways, both DHCP pools, the DNS record and NAT ([`cml/r1_config.txt`](cml/r1_config.txt)).
- **Verification:** `show ip interface brief`, `show ip route` and `show ip dhcp binding` on `JA-R1`; `ping -4 192.168.20.100` and `nslookup -type=A server.arthur.edu` from both clients.
- **Wireshark Trace Capture Plan (Sprint 4):** capture on the `JA-Client1` <-> `JA-R1` link during `nslookup -type=A server.arthur.edu` and save it as `sprint_4_dns_lookup.pcap`. It shows the DNS Standard Query to `192.168.10.1` on UDP 53 and the response returning `192.168.20.100`. The DHCP capture was removed from the Sprint 4 requirements.
- **Deliverables:** `sprint_4_topology.yaml` (CML export), `sprint_4_dns_lookup.pcap` and `sprint_4_network_report.md`, all in the repository root.
