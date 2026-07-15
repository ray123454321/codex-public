from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import pty
import select
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SOURCE_SCRIPT = PLUGIN_ROOT / "scripts" / "codex_keep_waiting.py"
EXECUTABLE = Path(
    os.environ.get(
        "USAGE_GUARD_WRAPPER_UNDER_TEST",
        str(SOURCE_SCRIPT),
    )
)
SPEC = importlib.util.spec_from_file_location("codex_keep_waiting", SOURCE_SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


PROMPT = (
    "\x1b[1mAdditional safety checks\x1b[0m\r\n"
    "1. Retry with a faster model\r\n"
    "2. Keep waiting\r\n"
    "3. Learn more\r\n"
    "Press enter to confirm or esc to go back\r\n"
).encode()


class DetectorTests(unittest.TestCase):
    def test_complete_ansi_view_matches(self) -> None:
        detector = MODULE.SafetyPromptDetector()
        midpoint = len(PROMPT) // 2
        self.assertFalse(detector.feed(PROMPT[:midpoint], now=10.0))
        self.assertTrue(detector.feed(PROMPT[midpoint:], now=10.1))

    def test_plain_words_without_full_view_do_not_match(self) -> None:
        detector = MODULE.SafetyPromptDetector()
        self.assertFalse(
            detector.feed(
                b"Additional safety checks might suggest Keep waiting, but this is prose.",
                now=10.0,
            )
        )

    def test_immediate_redraw_does_not_emit_second_selection(self) -> None:
        detector = MODULE.SafetyPromptDetector()
        self.assertTrue(detector.feed(PROMPT, now=10.0))
        self.assertFalse(detector.feed(PROMPT, now=10.2))
        self.assertTrue(detector.feed(PROMPT, now=13.0))

    def test_current_dismiss_wording_matches(self) -> None:
        detector = MODULE.SafetyPromptDetector()
        prompt = PROMPT.replace(b"2. Keep waiting", b"2. Dismiss and keep waiting")
        self.assertTrue(detector.feed(prompt, now=10.0))


class PtyReplayTests(unittest.TestCase):
    def _run_fake(
        self,
        body: str,
        expected: bytes,
        expected_receipts: int,
        timeout: float = 5.0,
    ) -> bytes:
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "fake-codex"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import os, select, sys, termios, tty\n"
                "tty.setraw(sys.stdin.fileno())\n"
                + textwrap.dedent(body),
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

            master, slave = pty.openpty()
            environment = os.environ.copy()
            environment["USAGE_GUARD_REAL_CODEX"] = str(fake)
            environment["CODEX_HOME"] = str(Path(directory) / "codex-home")
            process = subprocess.Popen(
                [sys.executable, str(EXECUTABLE)],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=environment,
                close_fds=True,
            )
            os.close(slave)
            output = bytearray()
            deadline = time.monotonic() + timeout
            try:
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if ready:
                        try:
                            chunk = os.read(master, 65_536)
                        except OSError:
                            chunk = b""
                        output.extend(chunk)
                        if expected in output:
                            break
                    if process.poll() is not None and not ready:
                        break
            finally:
                try:
                    os.close(master)
                except OSError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=2)
            self.assertIn(expected, bytes(output))
            self.assertEqual(process.returncode, 0)
            receipt_path = (
                Path(directory)
                / "codex-home"
                / "usage-guard"
                / "keep_waiting.log.jsonl"
            )
            receipts = receipt_path.read_text().splitlines() if receipt_path.exists() else []
            self.assertEqual(len(receipts), expected_receipts)
            return bytes(output)

    def test_exact_view_selects_two(self) -> None:
        self._run_fake(
            f"""
            os.write(sys.stdout.fileno(), {PROMPT!r})
            ready, _, _ = select.select([sys.stdin.fileno()], [], [], 2.0)
            value = os.read(sys.stdin.fileno(), 1) if ready else b""
            os.write(sys.stdout.fileno(), b"SELECTED_KEEP_WAITING" if value == b"2" else b"WRONG_SELECTION:" + value)
            """,
            b"SELECTED_KEEP_WAITING",
            1,
        )

    def test_incomplete_view_does_not_inject(self) -> None:
        self._run_fake(
            """
            os.write(sys.stdout.fileno(), b"Additional safety checks and Keep waiting in ordinary prose\\r\\n")
            ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0.6)
            value = os.read(sys.stdin.fileno(), 1) if ready else b""
            os.write(sys.stdout.fileno(), b"NO_INJECTION" if value == b"" else b"UNEXPECTED:" + value)
            """,
            b"NO_INJECTION",
            0,
        )

    def test_immediate_redraw_injects_once(self) -> None:
        self._run_fake(
            f"""
            os.write(sys.stdout.fileno(), {PROMPT!r})
            ready, _, _ = select.select([sys.stdin.fileno()], [], [], 2.0)
            first = os.read(sys.stdin.fileno(), 1) if ready else b""
            os.write(sys.stdout.fileno(), {PROMPT!r})
            ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0.6)
            second = os.read(sys.stdin.fileno(), 1) if ready else b""
            result = b"ONE_SELECTION_ONLY" if first == b"2" and second == b"" else b"BAD_KEYS:" + first + second
            os.write(sys.stdout.fileno(), result)
            """,
            b"ONE_SELECTION_ONLY",
            1,
        )


if __name__ == "__main__":
    unittest.main()
