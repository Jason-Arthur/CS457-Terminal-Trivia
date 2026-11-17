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
| [`docs/concurrency_design.md`](docs/concurrency_design.md) | Server concurrency architecture: the single-threaded `selectors` event loop, connection lifecycle, state isolation, and score storage and broadcast. |
| [`docs/sequence_diagram.md`](docs/sequence_diagram.md) | Mermaid sequence diagram and narrative for a full game: joining, move validation, `ERROR` branches, `STATE_UPDATE` broadcasts, tiebreaker, and game over. |
| [`docs/ai_prompts.md`](docs/ai_prompts.md) | AI usage policy, constraint strategy, base system prompt, and a per-sprint prompt log. |
| [`server.py`](server.py) | The game server: one `selectors` event loop that runs the game state machine for two players. |
| [`client.py`](client.py) | The player program: connects to the server by hostname and shows questions, turn status and scores. |
| [`protocol.py`](protocol.py) | Shared protocol code: message framing, the envelope, message types and error codes. |
| [`questions.json`](questions.json) | The question bank: 3 rounds of 3 questions plus 1 tiebreaker. |
| [`tests/`](tests/) | Automated tests built from the protocol and FSM specs, plus command-line checks (22 tests). |
| [`docs/sprint3_execution_log.md`](docs/sprint3_execution_log.md) | Console log of a full two-player game on localhost (Sprint 3). |

---

## How to Play (Step by Step)

You do not need to know how to program to play. You will type a few commands into a
**terminal** (a window where you type instructions instead of clicking). Copy each command exactly
as shown and press **Enter** after it.

A game needs **three terminal windows**:

| Window | What it runs | Who uses it |
|---|---|---|
| Window 1 | The **server**, the referee that asks the questions and keeps score | Nobody types here after starting it |
| Window 2 | **Player 1** | The first player |
| Window 3 | **Player 2** | The second player |

All three can be on the same computer (the steps below do this), or on three different computers
(see [Playing on separate computers](#playing-on-separate-computers)).

### What you need

- A **Mac** or a **Linux** computer. (Windows is not supported.)
- **Python 3.8 or newer**. Macs and most Linux systems already have it. Step 2 checks.
- This project folder on the computer (for example, downloaded from GitHub with
  **Code > Download ZIP** and unzipped).

### Step 1: Open a terminal

- **Mac:** press **Command + Space**, type `Terminal`, and press **Enter**.
- **Linux:** press **Ctrl + Alt + T**, or open **Terminal** from the applications menu.

### Step 2: Check that Python is installed

Type this and press **Enter**:

```text
python3 --version
```

You should see something like `Python 3.12.4`. Any version from 3.8 up is fine.
If you see `command not found` instead, install Python from <https://www.python.org/downloads/>
(Mac) or with `sudo apt install python3` (Ubuntu/Debian) or `apk add python3` (Alpine), and then
try again.

### Step 3: Go into the project folder

Type `cd` followed by a **space**. Do **not** press Enter yet. Then drag the project folder
(`CS457-Terminal-Trivia`) from Finder or your file manager into the terminal window. Its location
is filled in for you. Now press **Enter**.

It will look something like this:

```text
cd /Users/yourname/Downloads/CS457-Terminal-Trivia
```

To check you are in the right place, type `ls` and press **Enter**. You should see `server.py`,
`client.py` and `questions.json` in the list. If you don't, repeat this step.

### Step 4: Start the server (Window 1)

In this window, type:

```text
python3 server.py -p 5457
```

`-p 5457` is the **port**, a number that tells the players' programs where to find the
server. You should see a line like this (the time at the start will differ):

```text
11:15:02 [INFO] Listening on 0.0.0.0:5457 (state WAITING_FOR_PLAYERS)
```

**Leave this window open.** The server keeps running and prints a log of the game. If you close
this window, the game stops.

### Step 5: Start Player 1 (Window 2)

Open a **second** terminal window. On a Mac, press **Command + N** while Terminal is active. Go
into the project folder again (Step 3), then type:

```text
python3 client.py -i localhost -p 5457
```

`-i localhost` means "the server is on this same computer" and `-p 5457` is the same port as in
Step 4. The program asks for a name:

```text
Connected. Type 'quit' at any time to leave.
Enter your player name: Alice
Joined as Player 1 (1 of 2 players).
Waiting for Player 2...
```

Type a name and press **Enter**. Names can be 1 to 16 letters or numbers, with no spaces
(`_` and `-` are allowed). If the name is not accepted, you will be asked again.

### Step 6: Start Player 2 (Window 3)

Open a **third** terminal window, go into the project folder (Step 3), and type the **same**
command:

```text
python3 client.py -i localhost -p 5457
```

Enter a second name. The game starts right away on both player windows.

### Step 7: Play

Both players get the same question at the same time:

```text
Round 1, Question 1  (100 points)
Which layer of the OSI model does TCP operate at?
   A) Network
   B) Transport
   C) Session
   D) Data Link
>> Your Turn: answer A-D within 15 s: B
Answer B locked in. Waiting for Bob...
```

- Type the **letter** of your answer (`A`, `B`, `C` or `D`) and press **Enter**. Capital or
  lowercase letters both work.
- You have **15 seconds**. If time runs out, you get no points for that question.
- Your first answer is final.
- After both players answer (or time runs out), both windows show the correct answer, who got it
  right, and the scores. The next question follows automatically.

There are **3 rounds of 3 questions**, worth **100**, **200** and **300** points. The player with
the most points at the end wins:

```text
------------------------------------------------------------
  GAME OVER
------------------------------------------------------------
  You win! (most points after 3 rounds)
```

**If the scores are tied** after round 3, each player types a **wager**, a whole number from `0` up
to their score. Then there is one final question. A right answer adds **double** the wager, and a
wrong answer **subtracts** it. If it is still tied after that, the game is a draw.

### Leaving and playing again

- To leave in the middle of a game, type `quit` and press **Enter** (or press **Ctrl + C**).
  The other player wins by forfeit.
- After a game ends, the player windows close their connection. The server keeps running and
  is ready for the next game: just repeat Steps 5 and 6.
- To stop the server, click on Window 1 and press **Ctrl + C**.

### If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `can't open file '.../server.py'` or `No such file or directory` | The terminal is not in the project folder | Do Step 3 again in that window |
| `error: the following arguments are required: -p/--port` | The `-p 5457` part was left out | Type the whole command from the step |
| `Cannot listen on port 5457: Address already in use` | A server is already running (perhaps in another window) | Use that server, or stop it with **Ctrl + C** first |
| `[ERROR] Cannot connect to localhost (127.0.0.1) port 5457: Connection refused.` | The server is not running, or the port number is different | Start the server (Step 4) and use the same port |
| `[ERROR] Unable to resolve DNS hostname ...` | The name after `-i` could not be found | Check the spelling. On one computer use `localhost` |
| `[LOBBY_FULL] A game is already in progress.` | Two players are already playing | Wait for that game to finish, then connect again |
| `[INVALID_NAME] ...` | The name has spaces or symbols, or is too long | Type a shorter name using only letters and numbers |

### Playing on separate computers

The game is meant to run with the server and each player on different computers. In this project
these are the Linux nodes in the Cisco Modeling Labs (CML) network shown below. The only change is
the name after `-i`:

1. Copy `server.py`, `client.py`, `protocol.py` and `questions.json` (keep them together in one
   folder) onto the server computer and each player computer. Each computer needs Python 3
   (Step 2). On CML Alpine nodes, check with `python3 --version`. The nodes are offline, so if Python
   is missing it must be added with `apk add python3` while the node has an internet connection.
2. On the server computer: `python3 server.py -p 5457`
3. On each player computer, use the server's name instead of `localhost`:

   ```text
   python3 client.py -i server.arthur.edu -p 5457
   ```

   The player computer looks up `server.arthur.edu` through DNS (served by the CML router), the
   same way a web browser looks up a website. If you see the DNS error from the table above, the
   router's DNS settings (`ip dns server` / `ip host`) need checking.

### Running the automated tests (optional)

From the project folder:

```text
python3 -m unittest discover -s tests
```

The tests take about two seconds and should end with `Ran 22 tests ... OK`. They check the
message framing examples from the protocol blueprint, every error code, and full game behavior
(joining, answering, scoring, forfeits) against a real server on your computer.

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
| 2 | Server concurrency and state synchronization design | Done |
| 3 | Implementation (`protocol.py`, `server.py`, `client.py`) | Done |
| 4–5 | CML topology deployment and Wireshark captures | Not started |

The server and client are written in Python 3 using only the standard library, so they run on the
offline CML nodes with nothing extra to install. See [How to Play](#how-to-play-step-by-step) to
run them.
