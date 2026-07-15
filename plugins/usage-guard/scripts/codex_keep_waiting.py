#!/usr/bin/env python3
"""Usage Guard PTY component for Codex's safety-buffering selection view."""

from __future__ import annotations

import codecs
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import sys
import termios
import time
import tty
from typing import Iterable


VERSION = "0.2.0"
WRAPPER_MARKER = "USAGE_GUARD_KEEP_WAITING_WRAPPER_MARKER_v1"
LEGACY_WRAPPER_MARKER = "CODEX_KEEP_WAITING_WRAPPER_MARKER_v1"
MAX_DETECTOR_TEXT = 32_768
RETRIGGER_GUARD_SECONDS = 2.5


class AnsiTextExtractor:
    """Incrementally keep printable terminal text while discarding ANSI controls."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._state = "TEXT"

    def feed(self, data: bytes) -> str:
        output: list[str] = []
        for char in self._decoder.decode(data):
            code = ord(char)
            if self._state == "TEXT":
                if char == "\x1b":
                    self._state = "ESC"
                elif char == "\b":
                    if output:
                        output.pop()
                elif char in "\r\n\t":
                    output.append(" ")
                elif code >= 0x20 and code != 0x7F:
                    output.append(char)
            elif self._state == "ESC":
                if char == "[":
                    self._state = "CSI"
                elif char == "]":
                    self._state = "OSC"
                else:
                    self._state = "TEXT"
            elif self._state == "CSI":
                if 0x40 <= code <= 0x7E:
                    self._state = "TEXT"
            elif self._state == "OSC":
                if char == "\x07":
                    self._state = "TEXT"
                elif char == "\x1b":
                    self._state = "OSC_ESC"
            elif self._state == "OSC_ESC":
                if char == "\\":
                    self._state = "TEXT"
                elif char != "\x1b":
                    self._state = "OSC"
        return "".join(output)


class SafetyPromptDetector:
    """Recognize only the complete numbered safety-buffering selection view."""

    _title = re.compile(r"\bAdditional\s+safety\s+checks\b", re.IGNORECASE)
    _retry = re.compile(
        r"(?:^|\s)1\.\s*Retry\s+with\s+a\s+faster\s+model\b",
        re.IGNORECASE,
    )
    _wait = re.compile(
        r"(?:^|\s)2\.\s*(?:Dismiss\s+and\s+)?Keep\s+waiting\b",
        re.IGNORECASE,
    )
    _footer = re.compile(
        r"Press\s+enter\s+to\s+confirm\s+or\s+esc\s+to\s+go\s+back",
        re.IGNORECASE,
    )

    def __init__(self, retrigger_guard_s: float = RETRIGGER_GUARD_SECONDS) -> None:
        self._extractor = AnsiTextExtractor()
        self._text = ""
        self._last_triggered_at = float("-inf")
        self._retrigger_guard_s = retrigger_guard_s

    def feed(self, data: bytes, now: float | None = None) -> bool:
        visible = self._extractor.feed(data)
        if visible:
            self._text = (self._text + visible)[-MAX_DETECTOR_TEXT:]

        normalized = re.sub(r"\s+", " ", self._text)
        title = self._title.search(normalized)
        retry = self._retry.search(normalized)
        wait = self._wait.search(normalized)
        footer = self._footer.search(normalized)
        matched = bool(
            title
            and retry
            and wait
            and footer
            and retry.start() < wait.start()
        )
        if not matched:
            return False

        # Consume every complete rendering, including immediate redraws. This
        # prevents stale menu bytes from causing a delayed second keypress.
        self._text = ""
        timestamp = time.monotonic() if now is None else now
        if timestamp - self._last_triggered_at < self._retrigger_guard_s:
            return False

        self._last_triggered_at = timestamp
        return True


def _contains_wrapper_marker(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            prefix = handle.read(65_536)
            return any(
                marker.encode("ascii") in prefix
                for marker in (WRAPPER_MARKER, LEGACY_WRAPPER_MARKER)
            )
    except OSError:
        return False


def _candidate_paths() -> Iterable[Path]:
    override = os.environ.get("USAGE_GUARD_REAL_CODEX") or os.environ.get(
        "CODEX_KEEP_WAITING_REAL_BIN"
    )
    if override:
        yield Path(override).expanduser()

    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            yield Path(directory).expanduser() / "codex"

    yield Path("/usr/local/bin/codex")
    yield Path("/opt/homebrew/bin/codex")


def resolve_real_codex(self_path: Path | None = None) -> Path:
    """Resolve the underlying Codex executable without recursing into wrappers."""

    own = (self_path or Path(__file__)).expanduser().resolve()
    seen: set[str] = set()
    for candidate in _candidate_paths():
        try:
            expanded = candidate.expanduser()
            key = str(expanded.absolute())
            if key in seen:
                continue
            seen.add(key)
            if not expanded.exists() or not os.access(expanded, os.X_OK):
                continue
            resolved = expanded.resolve()
            if resolved == own or _contains_wrapper_marker(expanded):
                continue
            return expanded.absolute()
        except OSError:
            continue
    raise RuntimeError("could not locate a real Codex executable outside the wrapper")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _copy_window_size(source_fd: int, target_fd: int) -> None:
    try:
        size = fcntl.ioctl(source_fd, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(target_fd, termios.TIOCSWINSZ, size)
    except OSError:
        pass


def _log_selection(child_pid: int) -> None:
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    log_path = codex_home / "usage-guard" / "keep_waiting.log.jsonl"
    event = {
        "component": "compute_continuity",
        "event": "safety_buffering_keep_waiting_selected",
        "selection": 2,
        "wrapper_version": VERSION,
        "child_pid": child_pid,
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError:
        # Logging is evidence only; it must not break the user's Codex session.
        pass


def run_proxy(real_codex: Path, arguments: list[str]) -> int:
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    detector = SafetyPromptDetector()
    child_pid, master_fd = pty.fork()

    if child_pid == 0:
        environment = os.environ.copy()
        environment["USAGE_GUARD_KEEP_WAITING_ACTIVE"] = "1"
        environment["USAGE_GUARD_VERSION"] = VERSION
        # Keep the legacy markers for nested invocations during migration.
        environment["CODEX_KEEP_WAITING_ACTIVE"] = "1"
        environment["CODEX_KEEP_WAITING_VERSION"] = VERSION
        os.execve(str(real_codex), [str(real_codex), *arguments], environment)

    original_terminal = termios.tcgetattr(stdin_fd)
    previous_handlers: dict[int, object] = {}

    def resize_handler(_signum: int, _frame: object) -> None:
        _copy_window_size(stdin_fd, master_fd)

    def forward_signal(signum: int, _frame: object) -> None:
        try:
            os.kill(child_pid, signum)
        except ProcessLookupError:
            pass

    try:
        _copy_window_size(stdin_fd, master_fd)
        tty.setraw(stdin_fd, when=termios.TCSANOW)
        for signum, handler in (
            (signal.SIGWINCH, resize_handler),
            (signal.SIGHUP, forward_signal),
            (signal.SIGTERM, forward_signal),
        ):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, handler)

        input_open = True
        master_open = True
        while master_open:
            readers = [master_fd]
            if input_open:
                readers.append(stdin_fd)
            try:
                ready, _, _ = select.select(readers, [], [], 0.25)
            except InterruptedError:
                continue

            if stdin_fd in ready:
                user_data = os.read(stdin_fd, 65_536)
                if user_data:
                    _write_all(master_fd, user_data)
                else:
                    input_open = False

            if master_fd in ready:
                try:
                    child_data = os.read(master_fd, 65_536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        master_open = False
                        continue
                    raise
                if not child_data:
                    master_open = False
                    continue
                _write_all(stdout_fd, child_data)
                if detector.feed(child_data):
                    _write_all(master_fd, b"2")
                    _log_selection(child_pid)
    finally:
        try:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_terminal)
        except (OSError, termios.error):
            pass
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        try:
            os.close(master_fd)
        except OSError:
            pass

    _, status = os.waitpid(child_pid, 0)
    return os.waitstatus_to_exitcode(status)


def _diagnose(real_codex: Path) -> int:
    print(
        json.dumps(
            {
                "active": True,
                "component": "compute_continuity",
                "plugin": "usage-guard",
                "real_codex": str(real_codex),
                "version": VERSION,
                "wrapper": str(Path(__file__).resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    try:
        real_codex = resolve_real_codex()
    except RuntimeError as exc:
        print(f"usage-guard: {exc}", file=sys.stderr)
        return 127

    arguments = sys.argv[1:]
    if arguments in (
        ["--usage-guard-diagnose"],
        ["--codex-keep-waiting-diagnose"],
    ):
        return _diagnose(real_codex)

    # Preserve ordinary CLI/pipe behavior. Only an interactive TUI needs a PTY
    # proxy. The active marker also prevents nested Codex invocations recursing.
    if (
        os.environ.get("USAGE_GUARD_KEEP_WAITING_ACTIVE") == "1"
        or os.environ.get("CODEX_KEEP_WAITING_ACTIVE") == "1"
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
    ):
        os.execve(str(real_codex), [str(real_codex), *arguments], os.environ.copy())

    return run_proxy(real_codex, arguments)


if __name__ == "__main__":
    raise SystemExit(main())
