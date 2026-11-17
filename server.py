"""Terminal Trivia game server.

Single-threaded selectors event loop (docs/concurrency_design.md) that runs
the server FSM (docs/fsm_specification.md) over the wire protocol in
protocol.py (docs/protocol_blueprint.md).

Usage:
    python3 server.py -h
    python3 server.py -p 5457
"""

import argparse
import json
import logging
import os
import selectors
import socket
import sys
import time

import protocol as p

log = logging.getLogger("trivia.server")

QUESTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "questions.json")
ROUNDS = 3
QUESTIONS_PER_ROUND = 3
ROUND_POINTS = {1: 100, 2: 200, 3: 300}       # protocol_blueprint.md §3.1
TIEBREAKER_ID = "TB"
TIEBREAKER_ROUND = 4
FLUSH_TIMEOUT = 5.0                           # concurrency §2.6 step 2

# Errors that mean the peer is gone (protocol_blueprint.md §4.4).
PEER_GONE = (ConnectionResetError, BrokenPipeError,
             ConnectionAbortedError, TimeoutError)

# FSM states (fsm_specification.md §2). No others exist.
INIT = "INIT"
WAITING_FOR_PLAYERS = "WAITING_FOR_PLAYERS"
GAME_START = "GAME_START"
PLAYER_TURN = "PLAYER_TURN"
EVALUATE_MOVE = "EVALUATE_MOVE"
CHECK_WIN_DRAW = "CHECK_WIN_DRAW"
WAGER_TURN = "WAGER_TURN"
GAME_OVER = "GAME_OVER"
CLEANUP = "CLEANUP"
IN_GAME_STATES = frozenset({GAME_START, PLAYER_TURN, EVALUATE_MOVE,
                            CHECK_WIN_DRAW, WAGER_TURN})


# ---------------------------------------------------------------------------
# Command line (Sprint 3 assignment 2.1)
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    """Parse -h and -p for the server (Sprint 3 assignment 2.1)."""
    parser = argparse.ArgumentParser(
        description="Terminal Trivia server. Listens on 0.0.0.0 and runs "
                    "one 2-player game at a time.",
        epilog="example: python3 server.py -p 5457")
    parser.add_argument("-p", "--port", type=p.port_number, required=True,
                        help="TCP port to listen on (1024-65535; the "
                             "project uses 5457)")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Question bank (fsm_specification.md §2, INIT entry action)
# ---------------------------------------------------------------------------

def _check_question(q, where):
    """Validate one questions.json entry (blueprint §3.5 fields)."""
    if not isinstance(q, dict):
        raise ValueError(f"{where}: must be an object")
    if not isinstance(q.get("text"), str) or not q["text"]:
        raise ValueError(f"{where}: 'text' must be a non-empty string")
    choices = q.get("choices")
    if (not isinstance(choices, dict)
            or sorted(choices) != list(p.ANSWER_CHOICES)
            or not all(isinstance(v, str) for v in choices.values())):
        raise ValueError(f"{where}: 'choices' needs string keys A, B, C, D")
    if q.get("answer") not in p.ANSWER_CHOICES:
        raise ValueError(f"{where}: 'answer' must be A, B, C or D")


def load_questions(path):
    """Load 3 rounds x 3 questions + 1 tiebreaker (fsm_specification §2).

    Returns a list of 10 question dicts in play order, each with the
    QUESTION payload fields of protocol_blueprint.md §3.5 plus "answer".
    """
    with open(path, encoding="utf-8") as f:
        bank = json.load(f)
    rounds = bank.get("rounds") if isinstance(bank, dict) else None
    if not isinstance(rounds, list) or len(rounds) != ROUNDS:
        raise ValueError(f"'rounds' must be a list of {ROUNDS} rounds")
    ordered = []
    for r, round_qs in enumerate(rounds, start=1):
        if (not isinstance(round_qs, list)
                or len(round_qs) != QUESTIONS_PER_ROUND):
            raise ValueError(f"round {r} must have {QUESTIONS_PER_ROUND} "
                             "questions")
        for n, q in enumerate(round_qs, start=1):
            _check_question(q, f"round {r} question {n}")
            ordered.append({
                "question_id": f"R{r}Q{n}", "round": r, "question_number": n,
                "text": q["text"], "choices": q["choices"],
                "points": ROUND_POINTS[r], "answer": q["answer"],
            })
    tb = bank.get("tiebreaker")
    _check_question(tb, "tiebreaker")
    ordered.append({
        "question_id": TIEBREAKER_ID, "round": TIEBREAKER_ROUND,
        "question_number": 1, "text": tb["text"], "choices": tb["choices"],
        "points": 0, "answer": tb["answer"],
    })
    return ordered


# ---------------------------------------------------------------------------
# Per-connection and per-game state (concurrency_design.md §1.2)
# ---------------------------------------------------------------------------

class Connection:
    """One TCP connection and its buffers (concurrency_design.md §1.2)."""

    def __init__(self, sock, addr):
        """Wrap an accepted socket; role stays None until CONNECT (§2.4)."""
        self.sock = sock
        self.addr = addr
        self.reader = p.FrameReader(sock)     # inbuf (blueprint §1.3)
        self.outbuf = bytearray()
        self.role = None
        self.name = None
        self.close_after_flush = False
        self.flush_deadline = None
        self.closed = False

    def label(self):
        """Short name for log lines."""
        return self.role or f"{self.addr[0]}:{self.addr[1]}"


class Room:
    """The one game room and its FSM data (concurrency §1.2, §5.1)."""

    def __init__(self, questions):
        """Fresh room in WAITING_FOR_PLAYERS (fsm_specification.md T1, T16)."""
        self.state = WAITING_FOR_PLAYERS
        self.questions = questions
        self.players = {}                  # role -> Connection
        self.q_index = -1                  # index of the open/last question
        self.open_windows = set()          # roles whose window is open
        self.answers = {}
        self.scores = {p.PLAYER_1: 0, p.PLAYER_2: 0}
        self.wagers = {p.PLAYER_1: None, p.PLAYER_2: None}
        self.history = []
        self.deadline = None               # time.monotonic() value or None

    def current(self):
        """The question that is open or was just closed."""
        return self.questions[self.q_index]

    def time_left(self):
        """Seconds until the open window closes, or None (concurrency §2.5)."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class TriviaServer:
    """selectors event loop plus FSM handlers (concurrency §2, §3)."""

    def __init__(self, port, questions):
        """INIT entry actions (fsm_specification.md §2, concurrency §2.2)."""
        self.port = port
        self.questions = questions
        self.selector = selectors.DefaultSelector()
        self.conns = set()
        self.room = Room(questions)
        self.listen_sock = None

    # ----- startup and main loop -------------------------------------------

    def start(self):
        """Bind 0.0.0.0:port, listen(5), register (concurrency §2.2)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.port))
        sock.listen(5)
        sock.setblocking(False)
        self.selector.register(sock, selectors.EVENT_READ, data=None)
        self.listen_sock = sock
        log.info("Listening on 0.0.0.0:%d (state %s)", self.port,
                 self.room.state)

    def run(self):
        """Main loop in the order of concurrency_design.md §2.5."""
        while True:
            events = self.selector.select(self._select_timeout())
            self.check_deadline()                       # 1. expired window
            for key, mask in events:
                if key.data is None:
                    self.accept_client()                # 2a. new connection
                    continue
                conn = key.data
                if conn.closed:
                    continue
                if mask & selectors.EVENT_READ:
                    self.service_read(conn)             # 2b. recv + dispatch
                if mask & selectors.EVENT_WRITE and not conn.closed:
                    self.service_write(conn)            # 3. flush outbuf
            self.reap_closed_connections()

    def _select_timeout(self):
        """Wake for the deadline or a stuck flush (concurrency §2.5, §2.6)."""
        waits = [self.room.time_left()]
        now = time.monotonic()
        waits += [max(0.0, c.flush_deadline - now) for c in self.conns
                  if c.flush_deadline is not None]
        waits = [w for w in waits if w is not None]
        return min(waits) if waits else None

    # ----- socket handlers --------------------------------------------------

    def accept_client(self):
        """Accept one connection (concurrency_design.md §2.3)."""
        try:
            sock, addr = self.listen_sock.accept()
        except BlockingIOError:
            return
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Keepalive timers from protocol_blueprint.md §4.4 (Linux names).
        for opt, value in (("TCP_KEEPIDLE", 10), ("TCP_KEEPINTVL", 5),
                           ("TCP_KEEPCNT", 3)):
            if hasattr(socket, opt):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt),
                                value)
        conn = Connection(sock, addr)
        self.conns.add(conn)
        self.selector.register(sock, selectors.EVENT_READ, data=conn)
        log.info("Accepted connection from %s:%d", addr[0], addr[1])

    def service_read(self, conn):
        """recv once, frame, dispatch in arrival order (concurrency §2.5)."""
        try:
            frames = conn.reader.read_messages()
        except BlockingIOError:
            return
        except p.FrameTooLarge:
            log.warning("%s sent over %d bytes without a newline",
                        conn.label(), p.MAX_FRAME)
            self.queue(conn, p.error_msg(
                p.FRAME_TOO_LARGE, "Frame exceeds 4096 bytes.", None))
            self.handle_client_disconnect(conn, "opponent_disconnected",
                                          flush_first=True)
            return
        except PEER_GONE as e:
            log.warning("Connection to %s lost abruptly: %s", conn.label(), e)
            self.handle_client_disconnect(conn, "opponent_disconnected")
            return
        if frames is None:
            log.info("%s closed the connection (EOF)", conn.label())
            self.handle_client_disconnect(conn, "opponent_disconnected")
            return
        for frame in frames:
            # A frame that ended the game or this connection drops the rest
            # of the buffer (concurrency_design.md §3.5).
            if conn.closed or conn.close_after_flush:
                break
            self.dispatch(conn, frame)

    def service_write(self, conn):
        """Flush outbuf with send(), keep unsent bytes (concurrency §2.5)."""
        try:
            sent = conn.sock.send(conn.outbuf)
        except BlockingIOError:
            return
        except PEER_GONE as e:
            log.warning("Send to %s failed: %s", conn.label(), e)
            self.handle_client_disconnect(conn, "opponent_disconnected")
            return
        del conn.outbuf[:sent]
        if not conn.outbuf:
            self.selector.modify(conn.sock, selectors.EVENT_READ, data=conn)

    def queue(self, conn, msg):
        """Append a frame to outbuf, watch for writable (concurrency §2.5)."""
        if conn.closed:
            return
        conn.outbuf += p.encode_frame(msg)
        self.selector.modify(conn.sock,
                             selectors.EVENT_READ | selectors.EVENT_WRITE,
                             data=conn)

    def broadcast(self, msg):
        """Queue the same bytes to both players at once (concurrency §5.2)."""
        for role in p.ROLES:
            conn = self.room.players.get(role)
            if conn is not None:
                self.queue(conn, msg)

    def close_connection(self, conn):
        """unregister, shutdown, close (concurrency_design.md §2.6 step 3)."""
        if conn.closed:
            return
        conn.closed = True
        self.conns.discard(conn)
        for step in (lambda: self.selector.unregister(conn.sock),
                     lambda: conn.sock.shutdown(socket.SHUT_RDWR),
                     conn.sock.close):
            try:
                step()
            except (OSError, KeyError, ValueError):
                pass
        log.info("Closed connection to %s", conn.label())

    def reap_closed_connections(self):
        """Close flushed or stuck connections, then CLEANUP (§2.6)."""
        now = time.monotonic()
        for conn in list(self.conns):
            if not conn.close_after_flush:
                continue
            if conn.flush_deadline is None:
                conn.flush_deadline = now + FLUSH_TIMEOUT
            if not conn.outbuf or now >= conn.flush_deadline:
                self.close_connection(conn)
        if self.room.state == GAME_OVER and all(
                c.closed for c in self.room.players.values()):
            self.cleanup()

    # ----- dispatch (fsm_specification.md §3.2) -----------------------------

    def dispatch(self, conn, frame):
        """Decode, validate and route one frame (fsm_specification.md §3.2)."""
        try:
            msg = p.decode_frame(frame)
            p.validate_client_msg(msg)
        except p.ProtocolError as e:
            log.info("%s -> ERROR %s", conn.label(), e.code)
            self.queue(conn, p.error_msg(e.code, e.message, e.ref_msg_type))
            return
        msg_type = msg["msg_type"]
        log.info("%s <- %s %s", conn.label(), msg_type, msg["payload"])

        if msg_type == p.CONNECT:
            self.on_connect(conn, msg)
            return
        if conn.role is None:
            if msg_type == p.DISCONNECT:
                self.close_connection(conn)
            else:
                self.send_error(conn, p.NOT_JOINED,
                                "Send CONNECT before any other message.",
                                msg_type)
            return
        if msg["player_id"] != conn.role:
            self.send_error(conn, p.ID_MISMATCH,
                            f"You are {conn.role}, not {msg['player_id']}.",
                            msg_type)
            return
        if msg_type == p.MOVE:
            self.on_move(conn, msg)
        elif msg_type == p.WAGER:
            self.on_wager(conn, msg)
        elif msg_type == p.DISCONNECT:
            self.handle_client_disconnect(conn, "opponent_quit")

    def send_error(self, conn, code, message, ref_msg_type):
        """Queue an ERROR to the offending client only (blueprint §3.10)."""
        log.info("%s -> ERROR %s", conn.label(), code)
        self.queue(conn, p.error_msg(code, message, ref_msg_type))

    # ----- CONNECT (fsm T2, T3, T4; concurrency §2.4) -----------------------

    def on_connect(self, conn, msg):
        """Registration rules of concurrency_design.md §2.4."""
        room = self.room
        if conn.role is not None:
            self.send_error(conn, p.OUT_OF_TURN,
                            "You have already joined.", p.CONNECT)
            return
        if room.state != WAITING_FOR_PLAYERS:
            self.send_error(conn, p.LOBBY_FULL,
                            "A game is already in progress.", p.CONNECT)
            conn.close_after_flush = True
            return
        name = msg["payload"]["player_name"]
        if not p.valid_player_name(name):
            self.send_error(conn, p.INVALID_NAME,
                            "Name must be 1-16 letters, digits, _ or -.",
                            p.CONNECT)
            return
        conn.name = name
        if p.PLAYER_1 not in room.players:                       # T2
            conn.role = p.PLAYER_1
            room.players[p.PLAYER_1] = conn
            log.info("%s joined as Player_1, waiting for Player_2", name)
            self.queue(conn, p.make_msg(p.LOBBY_WAIT, p.SERVER_ID, {
                "assigned_id": p.PLAYER_1,
                "players_connected": 1,
                "players_needed": 2,
            }))
            return
        conn.role = p.PLAYER_2                                    # T3
        room.players[p.PLAYER_2] = conn
        log.info("%s joined as Player_2", name)
        self.start_game()

    def start_game(self):
        """GAME_START entry actions and T4 (fsm_specification.md §2, §3.1)."""
        room = self.room
        room.state = GAME_START
        log.info("State -> %s", room.state)
        names = {role: c.name for role, c in room.players.items()}
        for role, conn in room.players.items():
            self.queue(conn, p.make_msg(p.GAME_START, p.SERVER_ID, {
                "your_id": role,
                "players": names,
                "total_rounds": ROUNDS,
                "questions_per_round": QUESTIONS_PER_ROUND,
                "answer_time_limit": p.ANSWER_TIME_LIMIT,
            }))
        self.open_question(0)

    # ----- answer windows (fsm T4, T5, T6, T8, T12) -------------------------

    def open_question(self, index):
        """PLAYER_TURN entry: broadcast QUESTION, open windows, start timer."""
        room = self.room
        room.q_index = index
        room.state = PLAYER_TURN
        q = room.current()
        room.answers = {p.PLAYER_1: None, p.PLAYER_2: None}
        room.open_windows = set(p.ROLES)
        room.deadline = time.monotonic() + p.ANSWER_TIME_LIMIT
        log.info("State -> PLAYER_TURN, QUESTION %s", q["question_id"])
        payload = {k: q[k] for k in ("question_id", "round", "question_number",
                                     "text", "choices", "points")}
        payload["time_limit"] = p.ANSWER_TIME_LIMIT
        self.broadcast(p.make_msg(p.QUESTION, p.SERVER_ID, payload))

    def on_move(self, conn, msg):
        """Four ordered MOVE checks of concurrency_design.md §3.3 (fsm T5)."""
        room = self.room
        payload = msg["payload"]
        if room.state != PLAYER_TURN:
            self.send_error(conn, p.OUT_OF_TURN,
                            "No question is open right now.", p.MOVE)
            return
        qid = room.current()["question_id"]
        if payload["question_id"] != qid:
            self.send_error(conn, p.OUT_OF_TURN,
                            f"{payload['question_id']} is not the open "
                            f"question ({qid}).", p.MOVE)
            return
        if conn.role not in room.open_windows:
            self.send_error(conn, p.OUT_OF_TURN,
                            f"You already answered {qid}.", p.MOVE)
            return
        answer = payload["answer"].upper()
        if answer not in p.ANSWER_CHOICES:
            self.send_error(conn, p.INVALID_ANSWER,
                            "Answer must be A, B, C or D.", p.MOVE)
            return
        room.answers[conn.role] = answer
        room.open_windows.discard(conn.role)
        if not room.open_windows:                                 # T6
            self.close_round()

    def on_wager(self, conn, msg):
        """WAGER checks of fsm_specification.md T11 and §3.2."""
        room = self.room
        if room.state != WAGER_TURN or conn.role not in room.open_windows:
            self.send_error(conn, p.OUT_OF_TURN,
                            "No wager is being requested from you.", p.WAGER)
            return
        amount = msg["payload"]["amount"]
        max_wager = room.scores[conn.role]
        if (not isinstance(amount, int) or isinstance(amount, bool)
                or not 0 <= amount <= max_wager):
            self.send_error(conn, p.INVALID_WAGER,
                            f"Wager must be a whole number from 0 to "
                            f"{max_wager}.", p.WAGER)
            return
        room.wagers[conn.role] = amount
        room.open_windows.discard(conn.role)
        if not room.open_windows:                                 # T12
            self.close_wager_window()

    def check_deadline(self):
        """Close an expired window before any dispatch (concurrency §3.4)."""
        room = self.room
        if room.deadline is None or time.monotonic() < room.deadline:
            return
        log.info("15 s window expired in %s", room.state)
        if room.state == PLAYER_TURN:
            self.close_round()                                    # T6
        elif room.state == WAGER_TURN:
            self.close_wager_window()                             # T12

    def close_wager_window(self):
        """T12: missing wager = 0, then open the tiebreaker question."""
        room = self.room
        room.deadline = None
        room.open_windows = set()
        for role in p.ROLES:
            if room.wagers[role] is None:
                room.wagers[role] = 0
        log.info("Wagers: %s", room.wagers)
        self.open_question(room.q_index + 1)

    # ----- scoring and next step (fsm T6-T15; concurrency §3.3, §5.2) -------

    def close_round(self):
        """The one code path that scores a question (concurrency §3.3)."""
        room = self.room
        room.deadline = None
        room.open_windows = set()
        room.state = EVALUATE_MOVE                                # T6
        q = room.current()
        is_tb = q["question_id"] == TIEBREAKER_ID
        results = {}
        for role in p.ROLES:
            answer = room.answers[role]
            correct = answer == q["answer"]
            if is_tb:
                wager = room.wagers[role] or 0
                points = 2 * wager if correct else -wager
            else:
                points = q["points"] if correct else 0
            room.scores[role] += points
            results[role] = {"answer": answer, "correct": correct,
                             "points_awarded": points}
        room.history.append({"question_id": q["question_id"],
                             "round": q["round"],
                             "correct_answer": q["answer"],
                             "results": results})

        # CHECK_WIN_DRAW decides "next" before STATE_UPDATE goes out (T7).
        room.state = CHECK_WIN_DRAW
        tied = room.scores[p.PLAYER_1] == room.scores[p.PLAYER_2]
        last_regular = room.q_index == ROUNDS * QUESTIONS_PER_ROUND - 1
        if is_tb:
            nxt = p.GAME_OVER
        elif not last_regular:
            nxt = p.QUESTION
        elif tied:
            nxt = p.WAGER_REQUEST
        else:
            nxt = p.GAME_OVER

        log.info("Scored %s: %s scores %s", q["question_id"], results,
                 room.scores)
        self.broadcast(p.make_msg(p.STATE_UPDATE, p.SERVER_ID, {
            "question_id": q["question_id"],
            "correct_answer": q["answer"],
            "results": results,
            "scores": dict(room.scores),
            "next": nxt,
        }))

        if nxt == p.QUESTION:                                     # T8
            self.open_question(room.q_index + 1)
        elif nxt == p.WAGER_REQUEST:                              # T10
            self.open_wager_window()
        elif is_tb and tied:                                      # T14
            self.end_game("DRAW", None, "tied_after_tiebreaker")
        else:                                                     # T9, T13
            leader = max(p.ROLES, key=lambda r: room.scores[r])
            self.end_game("WIN", leader,
                          "tiebreaker" if is_tb else "most_points")

    def open_wager_window(self):
        """WAGER_TURN entry: WAGER_REQUEST to each, start timer (fsm T10)."""
        room = self.room
        room.state = WAGER_TURN
        room.wagers = {p.PLAYER_1: None, p.PLAYER_2: None}
        room.open_windows = set(p.ROLES)
        room.deadline = time.monotonic() + p.ANSWER_TIME_LIMIT
        log.info("State -> WAGER_TURN")
        for role, conn in room.players.items():
            self.queue(conn, p.make_msg(p.WAGER_REQUEST, p.SERVER_ID, {
                "max_wager": room.scores[role],
                "scores": dict(room.scores),
                "time_limit": p.ANSWER_TIME_LIMIT,
            }))

    # ----- game over, disconnects, cleanup (fsm T15, T16, §3.3) ------------

    def end_game(self, result, winner, reason):
        """GAME_OVER entry and T15 (fsm_specification §2, concurrency §2.6)."""
        room = self.room
        room.state = GAME_OVER
        room.deadline = None
        room.open_windows = set()
        log.info("State -> GAME_OVER: %s winner=%s reason=%s scores=%s",
                 result, winner, reason, room.scores)
        self.broadcast(p.make_msg(p.GAME_OVER, p.SERVER_ID, {
            "result": result,
            "winner": winner,
            "reason": reason,
            "final_scores": dict(room.scores),
        }))
        for conn in room.players.values():
            conn.close_after_flush = True

    def handle_client_disconnect(self, conn, reason, flush_first=False):
        """One disconnect path for every case (blueprint §4.5, fsm §3.3).

        reason is "opponent_quit" for DISCONNECT and "opponent_disconnected"
        for EOF, socket errors and FRAME_TOO_LARGE. flush_first keeps the
        connection open long enough to deliver an ERROR already queued.
        """
        room = self.room
        if flush_first:
            conn.close_after_flush = True
        else:
            self.close_connection(conn)
        role = conn.role
        if role is None or room.players.get(role) is not conn:
            return                                   # never joined the room
        if room.state == WAITING_FOR_PLAYERS:
            # Lobby disconnect: back to waiting, no forfeit (fsm §3.3).
            del room.players[role]
            log.info("%s left the lobby; waiting for players", role)
            return
        if room.state in IN_GAME_STATES:
            winner = p.PLAYER_2 if role == p.PLAYER_1 else p.PLAYER_1
            log.info("%s left mid-game: forfeit to %s", role, winner)
            self.end_game("FORFEIT", winner, reason)
        # GAME_OVER / CLEANUP: ignored, sockets are closing anyway.

    def cleanup(self):
        """CLEANUP and T16: fresh Room, back to WAITING_FOR_PLAYERS."""
        log.info("State -> CLEANUP")
        self.room = Room(self.questions)
        log.info("State -> %s", self.room.state)

    def shutdown(self):
        """Ctrl+C: close every socket and the selector (concurrency §2.6)."""
        log.info("Shutting down")
        for conn in list(self.conns):
            self.close_connection(conn)
        if self.listen_sock is not None:
            try:
                self.selector.unregister(self.listen_sock)
            except (KeyError, ValueError):
                pass
            self.listen_sock.close()
        self.selector.close()


def main(argv=None):
    """Parse args, load questions, run the loop (fsm_specification.md INIT)."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    try:
        questions = load_questions(QUESTIONS_FILE)
    except (OSError, ValueError) as e:
        log.error("Cannot load %s: %s", QUESTIONS_FILE, e)
        return 1
    server = TriviaServer(args.port, questions)
    try:
        server.start()
    except OSError as e:
        log.error("Cannot listen on port %d: %s", args.port, e)
        return 1
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
