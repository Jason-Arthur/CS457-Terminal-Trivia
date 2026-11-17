"""Command-line tests for server.py and client.py (Sprint 3 assignment 2.1).

Both programs must require -p and accept only ports 1024-65535; a bad or
missing value prints usage and exits with status 2 before any socket opens.

Run from the repository root:  python3 -m unittest discover -s tests
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAMS = {
    "server.py": [],
    "client.py": ["-i", "localhost"],
}


def run(program, args):
    """Run one program with args and return (exit status, combined output)."""
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, program)] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, timeout=10)
    return proc.returncode, proc.stdout


class PortFlagTests(unittest.TestCase):
    """-p is required and validated on both programs (rubric criterion 1)."""

    def test_missing_port(self):
        for program, extra in PROGRAMS.items():
            status, out = run(program, extra)
            self.assertEqual(status, 2, program)
            self.assertIn("required: -p/--port", out)

    def test_out_of_range(self):
        for program, extra in PROGRAMS.items():
            for port in ("80", "1023", "65536"):
                status, out = run(program, extra + ["-p", port])
                self.assertEqual(status, 2, (program, port))
                self.assertIn("out of range", out)

    def test_not_a_number(self):
        for program, extra in PROGRAMS.items():
            for port in ("abc", "5.5"):
                status, out = run(program, extra + ["-p", port])
                self.assertEqual(status, 2, (program, port))
                self.assertIn("is not a number", out)

    def test_help(self):
        for program, _ in PROGRAMS.items():
            status, out = run(program, ["-h"])
            self.assertEqual(status, 0, program)
            self.assertIn("-p", out)


if __name__ == "__main__":
    unittest.main()
