"""End-to-end tests of server.py over real TCP sockets on localhost.

Each test starts server.py on a free port and plays raw protocol frames
against it, checking the FSM rows of fsm_specification.md §3.

Run from the repository root:  python3 -m unittest discover -s tests
"""

import json
import os
import socket
import subprocess
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import protocol as p  # noqa: E402


def free_port():
    """Ask the OS for an unused port in the allowed range."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Player:
    """A raw-socket test client."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port))
        self.sock.settimeout(5)
        self.reader = p.FrameReader(self.sock)
        self.pending = []
        self.role = None

    def send(self, msg_type, payload, player_id="self"):
        pid = self.role if player_id == "self" else player_id
        p.send_msg(self.sock, p.make_msg(msg_type, pid, payload))

    def recv(self):
        """Next message, or None on EOF."""
        while not self.pending:
            frames = self.reader.read_messages()
            if frames is None:
                return None
            self.pending += [json.loads(f) for f in frames]
        return self.pending.pop(0)

    def close(self):
        self.sock.close()


class ServerTests(unittest.TestCase):
    """fsm_specification.md transition, error and disconnect tables."""

    def setUp(self):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server.py"),
             "-p", str(self.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port)).close()
                break
            except ConnectionRefusedError:
                time.sleep(0.05)
        self.players = []

    def tearDown(self):
        for pl in self.players:
            pl.close()
        self.proc.terminate()
        self.proc.wait()

    def join(self, name):
        pl = Player(self.port)
        self.players.append(pl)
        pl.send(p.CONNECT, {"player_name": name}, player_id=None)
        return pl

    def start_game(self):
        """T2, T3, T4: two CONNECTs lead to GAME_START and QUESTION R1Q1."""
        a = self.join("Alice")
        self.assertEqual(a.recv()["msg_type"], p.LOBBY_WAIT)
        b = self.join("Bob")
        for pl, role in ((a, p.PLAYER_1), (b, p.PLAYER_2)):
            start = pl.recv()
            self.assertEqual(start["msg_type"], p.GAME_START)
            self.assertEqual(start["payload"]["your_id"], role)
            pl.role = role
            self.assertEqual(pl.recv()["payload"]["question_id"], "R1Q1")
        return a, b

    def test_errors_before_join(self):
        """§3.2 rows: MALFORMED_MESSAGE, NOT_JOINED, INVALID_NAME."""
        pl = Player(self.port)
        self.players.append(pl)
        pl.sock.sendall(b"garbage\n")
        self.assertEqual(pl.recv()["payload"]["code"], p.MALFORMED_MESSAGE)
        pl.send(p.MOVE, {"question_id": "R1Q1", "answer": "A"}, None)
        self.assertEqual(pl.recv()["payload"]["code"], p.NOT_JOINED)
        pl.send(p.CONNECT, {"player_name": "bad name"}, None)
        self.assertEqual(pl.recv()["payload"]["code"], p.INVALID_NAME)
        pl.send(p.CONNECT, {"player_name": "Alice"}, None)
        self.assertEqual(pl.recv()["msg_type"], p.LOBBY_WAIT)

    def test_lobby_full(self):
        """§3.2: a third CONNECT gets LOBBY_FULL and is closed."""
        self.start_game()
        c = self.join("Carl")
        self.assertEqual(c.recv()["payload"]["code"], p.LOBBY_FULL)
        self.assertIsNone(c.recv())

    def test_move_rules_and_broadcast(self):
        """T5-T8 and the MOVE error rows; identical STATE_UPDATE to both."""
        a, b = self.start_game()
        a.send(p.MOVE, {"question_id": "R1Q1", "answer": "E"})
        self.assertEqual(a.recv()["payload"]["code"], p.INVALID_ANSWER)
        a.send(p.MOVE, {"question_id": "R1Q1", "answer": "b"})
        a.send(p.MOVE, {"question_id": "R1Q1", "answer": "C"})
        self.assertEqual(a.recv()["payload"]["code"], p.OUT_OF_TURN)
        b.send(p.MOVE, {"question_id": "R9Q9", "answer": "A"})
        self.assertEqual(b.recv()["payload"]["code"], p.OUT_OF_TURN)
        b.send(p.MOVE, {"question_id": "R1Q1", "answer": "A"}, p.PLAYER_1)
        self.assertEqual(b.recv()["payload"]["code"], p.ID_MISMATCH)
        b.send(p.WAGER, {"amount": 0})
        self.assertEqual(b.recv()["payload"]["code"], p.OUT_OF_TURN)
        b.send(p.MOVE, {"question_id": "R1Q1", "answer": "A"})
        ua, ub = a.recv(), b.recv()
        self.assertEqual(ua["payload"], ub["payload"])
        self.assertEqual(ua["payload"]["scores"],
                         {p.PLAYER_1: 100, p.PLAYER_2: 0})
        self.assertEqual(ua["payload"]["results"][p.PLAYER_1]["answer"], "B")
        self.assertEqual(ua["payload"]["next"], p.QUESTION)
        self.assertEqual(a.recv()["payload"]["question_id"], "R1Q2")

    def test_forfeit_on_disconnect(self):
        """§3.3: DISCONNECT mid-game is a FORFEIT, opponent_quit."""
        a, b = self.start_game()
        b.send(p.DISCONNECT, {"reason": "user_quit"})
        over = a.recv()
        self.assertEqual(over["msg_type"], p.GAME_OVER)
        self.assertEqual(over["payload"]["result"], "FORFEIT")
        self.assertEqual(over["payload"]["winner"], p.PLAYER_1)
        self.assertEqual(over["payload"]["reason"], "opponent_quit")
        self.assertIsNone(a.recv())

    def test_forfeit_on_eof_then_new_game(self):
        """§3.3: EOF mid-game forfeits; T16 room accepts a new pair."""
        a, b = self.start_game()
        a.close()
        over = b.recv()
        self.assertEqual(over["payload"]["reason"], "opponent_disconnected")
        self.assertEqual(over["payload"]["winner"], p.PLAYER_2)
        time.sleep(0.2)
        self.assertEqual(self.join("Dana").recv()["msg_type"], p.LOBBY_WAIT)

    def test_lobby_disconnect_is_not_forfeit(self):
        """§3.3: Player_1 leaving the lobby sends the room back to waiting."""
        a = self.join("Alice")
        a.recv()
        a.close()
        time.sleep(0.2)
        b = self.join("Bob")
        self.assertEqual(b.recv()["payload"]["assigned_id"], p.PLAYER_1)


if __name__ == "__main__":
    unittest.main()
