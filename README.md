# CS 457 Terminal Trivia

**Author:** Jason Arthur
**Course:** CS 457 Computer Networks, Fall 2026

A two-player, terminal-based trivia game played over TCP. The game server and the two clients run
on separate subnets of a multi-router network built in Cisco Modeling Labs (CML).

---

## The Game

- **Players:** 2. The first to connect is Player 1 and the second is Player 2.
- **Format:** 3 rounds of 3 multiple-choice questions (A to D). Questions are worth 100, 200 and
  300 points in rounds 1, 2 and 3.
- **Answering:** both players get the same question at the same time and have 15 seconds to
  answer. The server then reveals the correct answer and updates both scores.
- **Winning:** the player with the most points after round 3 wins.
- **Ties:** if the scores are tied, each player wagers 0 up to their total score on one final
  question. A correct answer adds double the wager, and a wrong answer subtracts it. If the scores
  are still tied, the game is a draw.
- **Disconnects:** a player who quits or drops mid-game forfeits, and the opponent wins.

---

## Repository Contents

| File | Description |
|---|---|
| [`sow_template.md`](sow_template.md) | Statement of Work: game scope, protocol summary, and plans for every sprint. |
| [`docs/protocol_blueprint.md`](docs/protocol_blueprint.md) | Application protocol: TCP framing, the message envelope, all 11 message schemas, error codes, and the socket lifecycle. |
| [`docs/fsm_specification.md`](docs/fsm_specification.md) | Server game state machine: a Mermaid diagram plus state, transition, error and disconnect tables. |
| [`docs/ai_prompts.md`](docs/ai_prompts.md) | AI usage policy, constraint strategy, base system prompt, and a per-sprint prompt log. |

---

## Protocol at a Glance

- **Transport:** TCP over IPv4, server on port `5457`
- **Format:** one compact UTF-8 JSON object per message, terminated by `\n` (max 4096 bytes)
- **Envelope:** `msg_type`, `player_id`, `payload`, `timestamp`
- **Messages:** `CONNECT`, `LOBBY_WAIT`, `GAME_START`, `QUESTION`, `MOVE`, `WAGER_REQUEST`,
  `WAGER`, `STATE_UPDATE`, `ERROR`, `DISCONNECT`, `GAME_OVER`

```text
{"msg_type":"MOVE","player_id":"Player_1","payload":{"question_id":"R1Q1","answer":"B"},"timestamp":1727000015}\n
```

---

## Planned Network Topology (CML)

| Segment | Network | Host |
|---|---|---|
| Subnet A | `192.168.10.0/24` behind Router R1 | Client 1 (DHCP) |
| Subnet B | `192.168.11.0/24` behind Router R1 | Client 2 (DHCP) |
| Subnet C | `192.168.20.0/24` behind Router R2 | Game server `192.168.20.100` (`server.arthur.edu`) |
| Backbone | `10.0.0.0/30` | R1 <-> R2 |

See section 5 of the [SOW](sow_template.md) for the DHCP, DNS and Wireshark plans.

---

## Project Status

| Sprint | Deliverable | Status |
|---|---|---|
| 0 | Game selection and SOW | Done |
| 1 | Application protocol and FSM design | Done |
| 2 | Server concurrency and state synchronization design | Not started |
| 3 | Implementation (`protocol.py`, `server.py`, `client.py`) | Not started |
| 4–5 | CML topology deployment and Wireshark captures | Not started |

There is no runnable code yet. The server and client will be written in Python 3 (standard
library only) in Sprint 3, and run instructions will be added here then.
