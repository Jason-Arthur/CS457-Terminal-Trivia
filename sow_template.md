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

### 3.1 Server Concurrency Strategy
- **Architecture Choice:** [Multi-Threading (`threading.Thread`) OR Non-blocking I/O multiplexing (`select.select` / `selectors`)]
- **Synchronization Logic:** Explain how shared game state and client list are thread-safe (e.g. `threading.Lock`) to prevent race conditions during turn processing.

### 3.2 State & Score Synchronization Across Clients
- **Turn Enforcement:** Detail how the server validates active player ID before processing moves and broadcasts updated turn notifications to all clients.
- **Score & Board Synchronization:** Describe how state broadcasts keep client screens synchronized in real time.

---

## 4. Coding & AI Implementation Plan (Sprint 3)

- **Permitted AI Tools:** [e.g., GitHub Copilot, ChatGPT, Claude]
- **AI Prompting & Constraint Strategy:** Explain how you will constrain AI models to generate code (in Python or your chosen language) that adheres strictly to the protocol blueprint and FSM designed in Sprints 1 & 2.
- **Implementation Risk Management:** Detail your plan to leverage past programming experience and manage time to ensure code completion on schedule.

---

## 5. CML Multi-Subnet Topology & Wireshark Deployment Plan (Sprint 4 & 5 Deliverable)

> For now you can use the topology below. We may update this when we get to defining subnets.

### 5.1 Subnet & Router Design
- **Subnet A (Client 1):** `192.168.10.0/24` (Interface `Gi0/1` on Router R1)
- **Subnet B (Client 2):** `192.168.11.0/24` (Interface `Gi0/2` on Router R1)
- **Subnet C (Game Server):** `192.168.20.0/24` (Interface `Gi0/1` on Router R2)
- **Router Backbone:** `10.0.0.0/30` (Interface `Gi0/0` on R1 <-> `Gi0/0` on R2)

### 5.2 DHCP Pools & DNS Configuration Plan
- **Router R1 DHCP Pool 1 (`CLIENT1_POOL`):** Leases `192.168.10.10` - `192.168.10.50`, gateway `192.168.10.1`, DNS `10.0.0.2`.
- **Router R1 DHCP Pool 2 (`CLIENT2_POOL`):** Leases `192.168.11.10` - `192.168.11.50`, gateway `192.168.11.1`, DNS `10.0.0.2`.
- **Router R2 Authoritative DNS:** Configured with `ip dns server` and static host mapping `server.[yourlastname].edu` -> `192.168.20.100`.

### 5.3 Deployment Strategy & Wireshark Trace Capture
- **CML Deployment Strategy:** Deploy `server.py` onto Subnet C node (`192.168.20.100`) behind Router R2, and `client.py` onto Subnet A and Subnet B nodes behind Router R1.
- **Cisco Infrastructure Configuration:** Router R1 DHCP pools (`CLIENT1_POOL`, `CLIENT2_POOL`) and Router R2 authoritative DNS (`ip host server.[lastname].edu 192.168.20.100`).
- **Wireshark Trace Capture Plan:** Capture DHCP DORA exchange (`dhcp_negotiation.pcap`) and DNS query/response resolution (`dns_lookup.pcap`).
