"""Terminal Trivia client.

Connects to the server named on the command line, then shows questions,
turn status and scores in the terminal. One thread runs a selectors loop
over the server socket and stdin, so a STATE_UPDATE that arrives while the
player is still typing (15 s timeout) is shown right away. Linux and macOS
only: Windows cannot select() on stdin.

Usage:
    python3 client.py -h
    python3 client.py -i server.arthur.edu -p 5457
"""

import argparse
import os
import selectors
import socket
import sys

import protocol as p

# Errors that mean the server is gone (protocol_blueprint.md §4.4).
SERVER_GONE = (ConnectionResetError, BrokenPipeError,
               ConnectionAbortedError, TimeoutError)
CONNECT_TIMEOUT = 10          # seconds to wait for the TCP handshake
RULE = "-" * 60

# What the client is waiting for from the keyboard.
ASK_NAME = "ask_name"         # player name for CONNECT
ASK_ANSWER = "ask_answer"     # A-D for the open question (Your Turn)
ASK_WAGER = "ask_wager"       # tiebreaker wager amount
NO_INPUT = "no_input"         # waiting on the server


# ---------------------------------------------------------------------------
# Command line and DNS (Sprint 3 assignment 2.1 and 2.2)
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    """Parse -h, -i and -p for the client (Sprint 3 assignment 2.1)."""
    parser = argparse.ArgumentParser(
        description="Terminal Trivia client. Connects to a Terminal Trivia "
                    "server and plays one 2-player game.",
        epilog="example: python3 client.py -i server.arthur.edu -p 5457")
    parser.add_argument("-i", "--host", required=True,
                        help="server DNS hostname (or IPv4 address)")
    parser.add_argument("-p", "--port", type=p.port_number, required=True,
                        help="server TCP port (1024-65535; the project "
                             "uses 5457)")
    return parser.parse_args(argv)


def resolve_server(hostname):
    """Resolve the server name via DNS (Sprint 3 assignment 2.2).

    Exits with status 1 and a readable message instead of a traceback when
    the name cannot be resolved or the DNS server is unreachable.
    """
    try:
        return socket.gethostbyname(hostname)
    except (socket.gaierror, UnicodeError) as e:
        print(f"[ERROR] Unable to resolve DNS hostname '{hostname}' ({e}).")
        print("Please check your Cisco router DNS configuration "
              "('ip dns server' / 'ip host') or verify domain spelling.")
        sys.exit(1)


def connect_to_server(ip, port, hostname):
    """Open the TCP connection (protocol_blueprint.md §4.1 step 1)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    try:
        sock.connect((ip, port))
    except OSError as e:
        sock.close()
        print(f"[ERROR] Cannot connect to {hostname} ({ip}) port {port}: "
              f"{e.strerror or e}.")
        print("Check that the server is running and the port is correct.")
        sys.exit(1)
    sock.settimeout(None)
    return sock


# ---------------------------------------------------------------------------
# Line-based stdin that works with selectors
# ---------------------------------------------------------------------------

class StdinLines:
    """Read whole lines from stdin without blocking the selectors loop.

    Uses os.read() on the raw file descriptor, because sys.stdin's own
    buffer can hold lines that select() would never report.
    """

    def __init__(self):
        """Empty line buffer over file descriptor 0."""
        self.fd = sys.stdin.fileno()
        self.buf = b""
        self.eof = False

    def read_lines(self):
        """Read what is available and return complete lines as str."""
        data = os.read(self.fd, 1024)
        if not data:
            self.eof = True
            return []
        self.buf += data
        *lines, self.buf = self.buf.split(b"\n")
        return [line.decode("utf-8", "replace").strip() for line in lines]


# ---------------------------------------------------------------------------
# Game client
# ---------------------------------------------------------------------------

class TriviaClient:
    """Client side of protocol_blueprint.md §3 (one handler per message)."""

    def __init__(self, sock):
        """Set up the selector over the server socket and stdin."""
        self.sock = sock
        self.reader = p.FrameReader(sock)
        self.stdin = StdinLines()
        self.selector = selectors.DefaultSelector()
        self.selector.register(sock, selectors.EVENT_READ, "server")
        self.selector.register(self.stdin.fd, selectors.EVENT_READ, "stdin")
        self.my_id = None          # "Player_1" / "Player_2" once assigned
        self.names = {}
        self.question = None       # payload of the open QUESTION
        self.max_wager = 0
        self.mode = NO_INPUT
        self.done = False
        self.exit_code = 0

    # ----- sending ----------------------------------------------------------

    def send(self, msg_type, payload):
        """Send one message with sendall() (protocol_blueprint.md §1.2)."""
        try:
            p.send_msg(self.sock, p.make_msg(msg_type, self.my_id, payload))
        except SERVER_GONE:
            self.lost_connection()

    def opponent(self):
        """Display name of the other player."""
        for role, name in self.names.items():
            if role != self.my_id:
                return name
        return "the other player"

    def prompt(self, mode, text):
        """Switch keyboard mode and show the prompt."""
        self.mode = mode
        print(text, end="", flush=True)

    # ----- main loop --------------------------------------------------------

    def run(self):
        """selectors loop over socket + stdin until GAME_OVER or disconnect."""
        print("Connected. Type 'quit' at any time to leave.")
        self.prompt(ASK_NAME, "Enter your player name: ")
        try:
            while not self.done:
                for key, _ in self.selector.select():
                    if key.data == "server":
                        self.on_server_readable()
                    else:
                        self.on_stdin_readable()
                    if self.done:
                        break
        except KeyboardInterrupt:
            print()
            self.quit()
        finally:
            self.selector.close()
            self.sock.close()
        return self.exit_code

    def on_server_readable(self):
        """recv once and handle each complete frame (blueprint §1.3, §4.3)."""
        try:
            frames = self.reader.read_messages()
        except SERVER_GONE:
            self.lost_connection()
            return
        except p.FrameTooLarge:
            print("\n[ERROR] Server sent an oversized frame.")
            self.lost_connection()
            return
        if frames is None:                       # EOF (blueprint §4.3)
            if not self.done:
                self.lost_connection()
            return
        for frame in frames:
            try:
                msg = p.decode_frame(frame)
            except p.ProtocolError as e:
                print(f"\n[WARN] Ignoring bad message from server: {e}")
                continue
            self.handle(msg)
            if self.done:
                return

    def on_stdin_readable(self):
        """Handle each typed line according to the current mode."""
        lines = self.stdin.read_lines()
        if self.stdin.eof:
            # Input closed (e.g. piped input ran out): keep playing as a
            # spectator until the server ends the game.
            self.selector.unregister(self.stdin.fd)
            return
        for line in lines:
            self.on_line(line)
            if self.done:
                return

    def on_line(self, line):
        """Turn one typed line into CONNECT, MOVE, WAGER or DISCONNECT."""
        if line.lower() == "quit":
            self.quit()
            return
        if self.mode == ASK_NAME:
            # The server checks the name and answers INVALID_NAME if it
            # breaks the rules; on_error then asks again (blueprint §3.2).
            self.mode = NO_INPUT
            self.send(p.CONNECT, {"player_name": line})
        elif self.mode == ASK_ANSWER:
            answer = line.upper()
            if answer not in p.ANSWER_CHOICES:
                self.prompt(ASK_ANSWER, "Please type A, B, C or D: ")
                return
            self.mode = NO_INPUT
            self.send(p.MOVE, {"question_id": self.question["question_id"],
                               "answer": answer})
            print(f"Answer {answer} locked in. "
                  f"Waiting for {self.opponent()}...")
        elif self.mode == ASK_WAGER:
            if not line.isdigit() or int(line) > self.max_wager:
                self.prompt(ASK_WAGER, f"Please enter a whole number from 0 "
                                       f"to {self.max_wager}: ")
                return
            self.mode = NO_INPUT
            self.send(p.WAGER, {"amount": int(line)})
            print(f"Wager of {int(line)} locked in. "
                  f"Waiting for {self.opponent()}...")
        elif line:
            print("(Not your turn yet. Waiting for the server...)")

    def quit(self):
        """Graceful quit: DISCONNECT, then close (blueprint §4.2)."""
        if self.my_id is not None:
            self.send(p.DISCONNECT, {"reason": "user_quit"})
        print("You left the game.")
        self.done = True

    def lost_connection(self):
        """Server gone: message and clean exit (protocol_blueprint.md §4.5)."""
        print("\nLost connection to server.")
        self.done = True
        self.exit_code = 1

    # ----- server messages (protocol_blueprint.md §3) -----------------------

    def handle(self, msg):
        """Route one server message to its display handler."""
        handlers = {
            p.LOBBY_WAIT: self.on_lobby_wait,
            p.GAME_START: self.on_game_start,
            p.QUESTION: self.on_question,
            p.STATE_UPDATE: self.on_state_update,
            p.WAGER_REQUEST: self.on_wager_request,
            p.ERROR: self.on_error,
            p.GAME_OVER: self.on_game_over,
        }
        handler = handlers.get(msg["msg_type"])
        if handler is not None:
            handler(msg["payload"])

    def on_lobby_wait(self, payload):
        """LOBBY_WAIT (protocol_blueprint.md §3.3)."""
        self.my_id = payload["assigned_id"]
        print(f"Joined as {self.my_id.replace('_', ' ')} "
              f"({payload['players_connected']} of "
              f"{payload['players_needed']} players).")
        print("Waiting for Player 2...")

    def on_game_start(self, payload):
        """GAME_START (protocol_blueprint.md §3.4)."""
        self.my_id = payload["your_id"]
        self.names = payload["players"]
        print(f"\n{RULE}\n  TERMINAL TRIVIA\n{RULE}")
        for role in p.ROLES:
            you = "  (you)" if role == self.my_id else ""
            print(f"  {role.replace('_', ' ')}: {self.names[role]}{you}")
        print(f"  {payload['total_rounds']} rounds x "
              f"{payload['questions_per_round']} questions, "
              f"{payload['answer_time_limit']} s per question")
        print(RULE)

    def on_question(self, payload):
        """QUESTION opens this player's turn (protocol_blueprint.md §3.5)."""
        self.question = payload
        if payload["question_id"] == "TB":
            header = "TIEBREAKER (your wager is on the line)"
        else:
            header = (f"Round {payload['round']}, Question "
                      f"{payload['question_number']}  "
                      f"({payload['points']} points)")
        print(f"\n{header}\n{payload['text']}")
        for letter in p.ANSWER_CHOICES:
            print(f"   {letter}) {payload['choices'][letter]}")
        self.prompt(ASK_ANSWER, f">> Your Turn: answer A-D within "
                                f"{payload['time_limit']} s: ")

    def on_state_update(self, payload):
        """STATE_UPDATE: reveal answers and scores (blueprint §3.9)."""
        if self.mode == ASK_ANSWER:
            print("\nTime's up!")
        self.mode = NO_INPUT
        print(f"\nCorrect answer: {payload['correct_answer']}")
        for role in p.ROLES:
            r = payload["results"][role]
            name = self.names.get(role, role)
            given = r["answer"] or "no answer"
            mark = "correct" if r["correct"] else "wrong"
            print(f"   {name:<16} {given:<10} {mark:<8} "
                  f"{r['points_awarded']:+d}")
        self.print_scores(payload["scores"])
        if payload["next"] == p.QUESTION:
            print("Next question coming up...")

    def on_wager_request(self, payload):
        """WAGER_REQUEST (protocol_blueprint.md §3.7)."""
        self.max_wager = payload["max_wager"]
        print("\nThe scores are tied! Time for the tiebreaker.")
        print("Correct answer: +2x your wager. Wrong or no answer: "
              "-1x your wager.")
        self.prompt(ASK_WAGER, f">> Your Turn: wager 0 to {self.max_wager} "
                               f"within {payload['time_limit']} s: ")

    def on_error(self, payload):
        """ERROR from the server (protocol_blueprint.md §3.10)."""
        code = payload["code"]
        print(f"\n[{code}] {payload['message']}")
        if code == p.INVALID_NAME:
            self.prompt(ASK_NAME, "Enter your player name: ")
        elif code == p.INVALID_ANSWER and self.question is not None:
            self.prompt(ASK_ANSWER, "Please type A, B, C or D: ")
        elif code == p.INVALID_WAGER:
            self.prompt(ASK_WAGER, f"Wager 0 to {self.max_wager}: ")
        elif code in (p.LOBBY_FULL, p.FRAME_TOO_LARGE):
            print("The server closed the connection.")
            self.done = True
            self.exit_code = 1

    def on_game_over(self, payload):
        """GAME_OVER: final result (protocol_blueprint.md §3.12)."""
        self.mode = NO_INPUT
        print(f"\n{RULE}\n  GAME OVER\n{RULE}")
        winner = payload["winner"]
        reasons = {
            "most_points": "most points after 3 rounds",
            "tiebreaker": "won the tiebreaker",
            "tied_after_tiebreaker": "still tied after the tiebreaker",
            "opponent_quit": "opponent quit",
            "opponent_disconnected": "opponent disconnected",
        }
        why = reasons.get(payload["reason"], payload["reason"])
        if payload["result"] == "DRAW":
            print(f"  It's a draw ({why}).")
        elif winner == self.my_id:
            print(f"  You win! ({why})")
        else:
            print(f"  {self.names.get(winner, winner)} wins ({why}).")
        self.print_scores(payload["final_scores"], title="Final scores")
        self.done = True

    def print_scores(self, scores, title="Scores"):
        """Score summary table, the player first."""
        print(f"{title}:")
        for role in sorted(p.ROLES, key=lambda r: r != self.my_id):
            you = " (you)" if role == self.my_id else ""
            name = self.names.get(role, role) + you
            print(f"   {name:<22} {scores[role]:>6}")


def main(argv=None):
    """Parse args, resolve DNS, connect, play (Sprint 3 assignment 2.1-2.4)."""
    args = parse_args(argv)
    ip = resolve_server(args.host)
    print(f"Resolved {args.host} to {ip}. Connecting to port {args.port}...")
    sock = connect_to_server(ip, args.port, args.host)
    return TriviaClient(sock).run()


if __name__ == "__main__":
    sys.exit(main())
