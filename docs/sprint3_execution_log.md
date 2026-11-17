# Sprint 3: Intermediate Gameplay Execution Log

**Course:** CS 457 Computer Networks, Sprint 3
**Author:** Jason Arthur

This log shows one complete game of Terminal Trivia between two clients and the server, all running
on one machine over `localhost` (127.0.0.1). The two clients connect at the same time, the server
assigns `Player_1` and `Player_2`, and they play all 9 questions (3 rounds) to `GAME_OVER`. That is
well past the required 3 turns.

## How it was run

Three terminals, from the repository root:

```text
python3 server.py -p 5457                    # terminal 1
python3 client.py -i localhost -p 5457       # terminal 2 (Alice)
python3 client.py -i localhost -p 5457       # terminal 3 (Bob)
```

`-i localhost` goes through `socket.gethostbyname()`, the same DNS path the client will use for
`server.arthur.edu` in Sprint 4. The players' typed input was fed from a script so the run is
repeatable. Typed lines appear in the transcripts right after each prompt.

## What to look for

| Behavior | Where | Spec |
|---|---|---|
| Two simultaneous clients, `LOBBY_WAIT` then `GAME_START` | First 10 lines of the server log; "Waiting for Player 2..." in Alice's transcript | FSM T2 to T4 |
| Server-side name check and retry | Bob types `Bob the Builder`, gets `[INVALID_NAME]`, retries as `Bob` | Blueprint §3.2, §3.10 |
| "Your Turn" prompt and "Waiting for ..." status | Every question in both transcripts | Assignment 2.4 |
| Client-side input check | Bob types `E` on R1Q2 and is asked again | Blueprint §3.6 |
| 15 s timeout closes the window | R2Q2: Bob does not answer; server logs `15 s window expired`, Bob sees "Time's up!", result `no answer` | FSM T6, concurrency §3.4 |
| Identical `STATE_UPDATE` and score summary on both screens | After every question | Concurrency §5.2 |
| `GAME_OVER`, `WIN`, `most_points`, then `CLEANUP` | End of all three logs | FSM T9, T15, T16 |

The input checks on the client only cover typing mistakes. The server enforces every rule on its
own, as shown by the automated tests in `tests/` (`python3 -m unittest discover -s tests`).

## Server log

```text
18:31:01 [INFO] Listening on 0.0.0.0:5457 (state WAITING_FOR_PLAYERS)
18:31:02 [INFO] Accepted connection from 127.0.0.1:37652
18:31:03 [INFO] 127.0.0.1:37652 <- CONNECT {'player_name': 'Alice'}
18:31:03 [INFO] Alice joined as Player_1, waiting for Player_2
18:31:03 [INFO] Accepted connection from 127.0.0.1:37668
18:31:04 [INFO] 127.0.0.1:37668 <- CONNECT {'player_name': 'Bob the Builder'}
18:31:04 [INFO] 127.0.0.1:37668 -> ERROR INVALID_NAME
18:31:05 [INFO] 127.0.0.1:37668 <- CONNECT {'player_name': 'Bob'}
18:31:05 [INFO] Bob joined as Player_2
18:31:05 [INFO] State -> GAME_START
18:31:05 [INFO] State -> PLAYER_TURN, QUESTION R1Q1
18:31:05 [INFO] Player_1 <- MOVE {'question_id': 'R1Q1', 'answer': 'B'}
18:31:06 [INFO] Player_2 <- MOVE {'question_id': 'R1Q1', 'answer': 'B'}
18:31:06 [INFO] Scored R1Q1: {'Player_1': {'answer': 'B', 'correct': True, 'points_awarded': 100}, 'Player_2': {'answer': 'B', 'correct': True, 'points_awarded': 100}} scores {'Player_1': 100, 'Player_2': 100}
18:31:06 [INFO] State -> PLAYER_TURN, QUESTION R1Q2
18:31:07 [INFO] Player_1 <- MOVE {'question_id': 'R1Q2', 'answer': 'C'}
18:31:07 [INFO] Player_2 <- MOVE {'question_id': 'R1Q2', 'answer': 'A'}
18:31:07 [INFO] Scored R1Q2: {'Player_1': {'answer': 'C', 'correct': True, 'points_awarded': 100}, 'Player_2': {'answer': 'A', 'correct': False, 'points_awarded': 0}} scores {'Player_1': 200, 'Player_2': 100}
18:31:07 [INFO] State -> PLAYER_TURN, QUESTION R1Q3
18:31:08 [INFO] Player_1 <- MOVE {'question_id': 'R1Q3', 'answer': 'A'}
18:31:09 [INFO] Player_2 <- MOVE {'question_id': 'R1Q3', 'answer': 'C'}
18:31:09 [INFO] Scored R1Q3: {'Player_1': {'answer': 'A', 'correct': True, 'points_awarded': 100}, 'Player_2': {'answer': 'C', 'correct': False, 'points_awarded': 0}} scores {'Player_1': 300, 'Player_2': 100}
18:31:09 [INFO] State -> PLAYER_TURN, QUESTION R2Q1
18:31:09 [INFO] Player_1 <- MOVE {'question_id': 'R2Q1', 'answer': 'C'}
18:31:09 [INFO] Player_2 <- MOVE {'question_id': 'R2Q1', 'answer': 'C'}
18:31:09 [INFO] Scored R2Q1: {'Player_1': {'answer': 'C', 'correct': True, 'points_awarded': 200}, 'Player_2': {'answer': 'C', 'correct': True, 'points_awarded': 200}} scores {'Player_1': 500, 'Player_2': 300}
18:31:09 [INFO] State -> PLAYER_TURN, QUESTION R2Q2
18:31:10 [INFO] Player_1 <- MOVE {'question_id': 'R2Q2', 'answer': 'B'}
18:31:24 [INFO] 15 s window expired in PLAYER_TURN
18:31:24 [INFO] Scored R2Q2: {'Player_1': {'answer': 'B', 'correct': True, 'points_awarded': 200}, 'Player_2': {'answer': None, 'correct': False, 'points_awarded': 0}} scores {'Player_1': 700, 'Player_2': 300}
18:31:24 [INFO] State -> PLAYER_TURN, QUESTION R2Q3
18:31:25 [INFO] Player_2 <- MOVE {'question_id': 'R2Q3', 'answer': 'C'}
18:31:25 [INFO] Player_1 <- MOVE {'question_id': 'R2Q3', 'answer': 'D'}
18:31:25 [INFO] Scored R2Q3: {'Player_1': {'answer': 'D', 'correct': False, 'points_awarded': 0}, 'Player_2': {'answer': 'C', 'correct': True, 'points_awarded': 200}} scores {'Player_1': 700, 'Player_2': 500}
18:31:25 [INFO] State -> PLAYER_TURN, QUESTION R3Q1
18:31:25 [INFO] Player_2 <- MOVE {'question_id': 'R3Q1', 'answer': 'A'}
18:31:25 [INFO] Player_1 <- MOVE {'question_id': 'R3Q1', 'answer': 'A'}
18:31:25 [INFO] Scored R3Q1: {'Player_1': {'answer': 'A', 'correct': True, 'points_awarded': 300}, 'Player_2': {'answer': 'A', 'correct': True, 'points_awarded': 300}} scores {'Player_1': 1000, 'Player_2': 800}
18:31:25 [INFO] State -> PLAYER_TURN, QUESTION R3Q2
18:31:26 [INFO] Player_2 <- MOVE {'question_id': 'R3Q2', 'answer': 'B'}
18:31:26 [INFO] Player_1 <- MOVE {'question_id': 'R3Q2', 'answer': 'D'}
18:31:26 [INFO] Scored R3Q2: {'Player_1': {'answer': 'D', 'correct': True, 'points_awarded': 300}, 'Player_2': {'answer': 'B', 'correct': False, 'points_awarded': 0}} scores {'Player_1': 1300, 'Player_2': 800}
18:31:26 [INFO] State -> PLAYER_TURN, QUESTION R3Q3
18:31:27 [INFO] Player_2 <- MOVE {'question_id': 'R3Q3', 'answer': 'C'}
18:31:27 [INFO] Player_1 <- MOVE {'question_id': 'R3Q3', 'answer': 'C'}
18:31:27 [INFO] Scored R3Q3: {'Player_1': {'answer': 'C', 'correct': True, 'points_awarded': 300}, 'Player_2': {'answer': 'C', 'correct': True, 'points_awarded': 300}} scores {'Player_1': 1600, 'Player_2': 1100}
18:31:27 [INFO] State -> GAME_OVER: WIN winner=Player_1 reason=most_points scores={'Player_1': 1600, 'Player_2': 1100}
18:31:27 [INFO] Closed connection to Player_2
18:31:27 [INFO] Closed connection to Player_1
18:31:27 [INFO] State -> CLEANUP
18:31:27 [INFO] State -> WAITING_FOR_PLAYERS
```

## Client 1 (Alice, Player 1)

```text
Resolved localhost to 127.0.0.1. Connecting to port 5457...
Connected. Type 'quit' at any time to leave.
Enter your player name: Alice
Joined as Player 1 (1 of 2 players).
Waiting for Player 2...

------------------------------------------------------------
  TERMINAL TRIVIA
------------------------------------------------------------
  Player 1: Alice  (you)
  Player 2: Bob
  3 rounds x 3 questions, 15 s per question
------------------------------------------------------------

Round 1, Question 1  (100 points)
Which layer of the OSI model does TCP operate at?
   A) Network
   B) Transport
   C) Session
   D) Data Link
>> Your Turn: answer A-D within 15 s: B
Answer B locked in. Waiting for Bob...

Correct answer: B
   Alice            B          correct  +100
   Bob              B          correct  +100
Scores:
   Alice (you)               100
   Bob                       100
Next question coming up...

Round 1, Question 2  (100 points)
Which port does HTTPS use by default?
   A) 80
   B) 22
   C) 443
   D) 8080
>> Your Turn: answer A-D within 15 s: C
Answer C locked in. Waiting for Bob...

Correct answer: C
   Alice            C          correct  +100
   Bob              A          wrong    +0
Scores:
   Alice (you)               200
   Bob                       100
Next question coming up...

Round 1, Question 3  (100 points)
How many bits are in an IPv4 address?
   A) 32
   B) 48
   C) 64
   D) 128
>> Your Turn: answer A-D within 15 s: A
Answer A locked in. Waiting for Bob...

Correct answer: A
   Alice            A          correct  +100
   Bob              C          wrong    +0
Scores:
   Alice (you)               300
   Bob                       100
Next question coming up...

Round 2, Question 1  (200 points)
Which protocol translates hostnames into IP addresses?
   A) DHCP
   B) ARP
   C) DNS
   D) ICMP
>> Your Turn: answer A-D within 15 s: C
Answer C locked in. Waiting for Bob...

Correct answer: C
   Alice            C          correct  +200
   Bob              C          correct  +200
Scores:
   Alice (you)               500
   Bob                       300
Next question coming up...

Round 2, Question 2  (200 points)
What does the TCP three-way handshake start with?
   A) ACK
   B) SYN
   C) FIN
   D) RST
>> Your Turn: answer A-D within 15 s: B
Answer B locked in. Waiting for Bob...

Correct answer: B
   Alice            B          correct  +200
   Bob              no answer  wrong    +0
Scores:
   Alice (you)               700
   Bob                       300
Next question coming up...

Round 2, Question 3  (200 points)
How many usable host addresses does a /24 subnet have?
   A) 256
   B) 255
   C) 254
   D) 252
>> Your Turn: answer A-D within 15 s: D
Answer D locked in. Waiting for Bob...

Correct answer: C
   Alice            D          wrong    +0
   Bob              C          correct  +200
Scores:
   Alice (you)               700
   Bob                       500
Next question coming up...

Round 3, Question 1  (300 points)
Which protocol maps an IPv4 address to a MAC address on a LAN?
   A) ARP
   B) NAT
   C) RIP
   D) DNS
>> Your Turn: answer A-D within 15 s: A
Answer A locked in. Waiting for Bob...

Correct answer: A
   Alice            A          correct  +300
   Bob              A          correct  +300
Scores:
   Alice (you)              1000
   Bob                       800
Next question coming up...

Round 3, Question 2  (300 points)
Which TCP flag closes a connection gracefully?
   A) URG
   B) PSH
   C) RST
   D) FIN
>> Your Turn: answer A-D within 15 s: D
Answer D locked in. Waiting for Bob...

Correct answer: D
   Alice            D          correct  +300
   Bob              B          wrong    +0
Scores:
   Alice (you)              1300
   Bob                       800
Next question coming up...

Round 3, Question 3  (300 points)
Which DHCP message does a client broadcast first?
   A) OFFER
   B) REQUEST
   C) DISCOVER
   D) ACK
>> Your Turn: answer A-D within 15 s: C
Answer C locked in. Waiting for Bob...

Correct answer: C
   Alice            C          correct  +300
   Bob              C          correct  +300
Scores:
   Alice (you)              1600
   Bob                      1100

------------------------------------------------------------
  GAME OVER
------------------------------------------------------------
  You win! (most points after 3 rounds)
Final scores:
   Alice (you)              1600
   Bob                      1100

[exit status 0]
```

## Client 2 (Bob, Player 2)

```text
Resolved localhost to 127.0.0.1. Connecting to port 5457...
Connected. Type 'quit' at any time to leave.
Enter your player name: Bob the Builder

[INVALID_NAME] Name must be 1-16 letters, digits, _ or -.
Enter your player name: Bob

------------------------------------------------------------
  TERMINAL TRIVIA
------------------------------------------------------------
  Player 1: Alice
  Player 2: Bob  (you)
  3 rounds x 3 questions, 15 s per question
------------------------------------------------------------

Round 1, Question 1  (100 points)
Which layer of the OSI model does TCP operate at?
   A) Network
   B) Transport
   C) Session
   D) Data Link
>> Your Turn: answer A-D within 15 s: B
Answer B locked in. Waiting for Alice...

Correct answer: B
   Alice            B          correct  +100
   Bob              B          correct  +100
Scores:
   Bob (you)                 100
   Alice                     100
Next question coming up...

Round 1, Question 2  (100 points)
Which port does HTTPS use by default?
   A) 80
   B) 22
   C) 443
   D) 8080
>> Your Turn: answer A-D within 15 s: E
Please type A, B, C or D: A
Answer A locked in. Waiting for Alice...

Correct answer: C
   Alice            C          correct  +100
   Bob              A          wrong    +0
Scores:
   Bob (you)                 100
   Alice                     200
Next question coming up...

Round 1, Question 3  (100 points)
How many bits are in an IPv4 address?
   A) 32
   B) 48
   C) 64
   D) 128
>> Your Turn: answer A-D within 15 s: C
Answer C locked in. Waiting for Alice...

Correct answer: A
   Alice            A          correct  +100
   Bob              C          wrong    +0
Scores:
   Bob (you)                 100
   Alice                     300
Next question coming up...

Round 2, Question 1  (200 points)
Which protocol translates hostnames into IP addresses?
   A) DHCP
   B) ARP
   C) DNS
   D) ICMP
>> Your Turn: answer A-D within 15 s: C
Answer C locked in. Waiting for Alice...

Correct answer: C
   Alice            C          correct  +200
   Bob              C          correct  +200
Scores:
   Bob (you)                 300
   Alice                     500
Next question coming up...

Round 2, Question 2  (200 points)
What does the TCP three-way handshake start with?
   A) ACK
   B) SYN
   C) FIN
   D) RST
>> Your Turn: answer A-D within 15 s: 
Time's up!

Correct answer: B
   Alice            B          correct  +200
   Bob              no answer  wrong    +0
Scores:
   Bob (you)                 300
   Alice                     700
Next question coming up...

Round 2, Question 3  (200 points)
How many usable host addresses does a /24 subnet have?
   A) 256
   B) 255
   C) 254
   D) 252
>> Your Turn: answer A-D within 15 s: C
Answer C locked in. Waiting for Alice...

Correct answer: C
   Alice            D          wrong    +0
   Bob              C          correct  +200
Scores:
   Bob (you)                 500
   Alice                     700
Next question coming up...

Round 3, Question 1  (300 points)
Which protocol maps an IPv4 address to a MAC address on a LAN?
   A) ARP
   B) NAT
   C) RIP
   D) DNS
>> Your Turn: answer A-D within 15 s: A
Answer A locked in. Waiting for Alice...

Correct answer: A
   Alice            A          correct  +300
   Bob              A          correct  +300
Scores:
   Bob (you)                 800
   Alice                    1000
Next question coming up...

Round 3, Question 2  (300 points)
Which TCP flag closes a connection gracefully?
   A) URG
   B) PSH
   C) RST
   D) FIN
>> Your Turn: answer A-D within 15 s: B
Answer B locked in. Waiting for Alice...

Correct answer: D
   Alice            D          correct  +300
   Bob              B          wrong    +0
Scores:
   Bob (you)                 800
   Alice                    1300
Next question coming up...

Round 3, Question 3  (300 points)
Which DHCP message does a client broadcast first?
   A) OFFER
   B) REQUEST
   C) DISCOVER
   D) ACK
>> Your Turn: answer A-D within 15 s: C
Answer C locked in. Waiting for Alice...

Correct answer: C
   Alice            C          correct  +300
   Bob              C          correct  +300
Scores:
   Bob (you)                1100
   Alice                    1600

------------------------------------------------------------
  GAME OVER
------------------------------------------------------------
  Alice wins (most points after 3 rounds).
Final scores:
   Bob (you)                1100
   Alice                    1600

[exit status 0]
```
