from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "usage_guard_once.py"
SPEC = importlib.util.spec_from_file_location("usage_guard_once", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class UsageGuardDefaultPolicyTest(unittest.TestCase):
    def test_default_policy_matches_three_continuity_responsibilities(self) -> None:
        self.assertTrue(MODULE.DEFAULT_CONFIG["enabled"])
        self.assertEqual(MODULE.DEFAULT_CONFIG["threshold_percent"], 97.0)
        self.assertTrue(MODULE.DEFAULT_CONFIG["auto_redeem"])
        self.assertTrue(MODULE.DEFAULT_CONFIG["handoff_on_redeem_failure"])
        self.assertEqual(MODULE.DEFAULT_CONFIG["handoff_threshold_percent"], 98.0)


class UsageGuardHandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.session = self.root / "session.jsonl"
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self._append(
            {
                "type": "session_meta",
                "timestamp": "2026-07-13T00:00:00Z",
                "payload": {"cwd": str(self.workspace), "id": "test-session"},
            }
        )
        self._append(
            {
                "type": "response_item",
                "timestamp": "2026-07-13T00:00:01Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Implement the current task and preserve evidence."}],
                },
            }
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _append(self, event: dict) -> None:
        with self.session.open("a") as fh:
            fh.write(json.dumps(event) + "\n")

    def _usage(self, used_percent: float, resets_at: int = 42) -> None:
        self._append(
            {
                "type": "event_msg",
                "timestamp": f"2026-07-13T00:00:{int(used_percent):02d}Z",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "limit_id": "test-limit",
                        "limit_name": "Test",
                        "primary": {"used_percent": used_percent, "resets_at": resets_at},
                        "secondary": {"used_percent": 1.0, "resets_at": 99},
                        "credits": {"has_credits": True, "balance": "1"},
                    },
                },
            }
        )

    def _config(self, command: str) -> None:
        config = {
            "enabled": True,
            "threshold_percent": 97.0,
            "handoff_on_redeem_failure": True,
            "handoff_threshold_percent": 98.0,
            "handoff_filename": "handoff.org",
            "auto_redeem": True,
            "redeem_strategy": "command",
            "redeem_command": [command],
            "notify": False,
            "settle_timeout_ms": 0,
            "settle_interval_ms": 50,
        }
        (self.state_dir / "config.json").write_text(json.dumps(config))

    def _run(self) -> dict:
        env = os.environ.copy()
        env["USAGE_GUARD_STATE_DIR"] = str(self.state_dir)
        result = subprocess.run(
            [
                "/usr/bin/env",
                "python3",
                str(SCRIPT),
                "--event-file",
                str(self.session),
                "--settle-timeout-ms",
                "0",
                "--print",
            ],
            input="{}",
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )
        return json.loads(result.stdout)

    def test_failed_redeem_starts_handoff_only_above_98_and_preserves_notes(self) -> None:
        self._config("/usr/bin/false")
        self._usage(97.0)
        first = self._run()
        self.assertEqual(first["action"], "redeem_attempted")
        self.assertFalse(first["redeem_ok"])
        handoff = self.workspace / "handoff.org"
        self.assertFalse(handoff.exists())

        self._usage(98.0)
        exact = self._run()
        self.assertEqual(exact["action"], "deduped")
        self.assertFalse(handoff.exists())

        handoff.write_text("* Existing task notes\n- keep this evidence\n")
        self._usage(98.1)
        started = self._run()
        self.assertEqual(started["action"], "handoff_written")
        text = handoff.read_text()
        self.assertIn("* Usage Guard Emergency Handoff", text)
        self.assertIn("Implement the current task and preserve evidence.", text)
        self.assertIn("* Existing task notes", text)

        self._usage(99.0)
        active = self._run()
        self.assertEqual(active["action"], "handoff_active")
        log_entries = [json.loads(line) for line in (self.state_dir / "usage_guard.log.jsonl").read_text().splitlines()]
        self.assertEqual(sum(entry.get("action") == "redeem_attempted" for entry in log_entries), 1)

    def test_successful_redeem_does_not_start_handoff(self) -> None:
        self._config("/usr/bin/true")
        self._usage(99.0)
        decision = self._run()
        self.assertEqual(decision["action"], "redeem_attempted")
        self.assertTrue(decision["redeem_ok"])
        self.assertFalse((self.workspace / "handoff.org").exists())

    def test_old_failed_redeem_log_is_migrated(self) -> None:
        self._config("/usr/bin/false")
        key = "test-limit|primary|42"
        (self.state_dir / "state.json").write_text(
            json.dumps({"triggered": {key: {"ts": 1, "used_percent": 97.0}}})
        )
        (self.state_dir / "usage_guard.log.jsonl").write_text(
            json.dumps(
                {
                    "action": "redeem_attempted",
                    "trigger_key": key,
                    "redeem_ok": False,
                    "redeem_strategy": "command",
                    "redeem_message": "legacy failure",
                }
            )
            + "\n"
        )
        self._usage(98.1)
        decision = self._run()
        self.assertEqual(decision["action"], "handoff_written")
        self.assertIn("legacy failure", (self.workspace / "handoff.org").read_text())

    def test_malformed_managed_markers_fail_closed(self) -> None:
        self._config("/usr/bin/false")
        key = "test-limit|primary|42"
        (self.state_dir / "state.json").write_text(
            json.dumps(
                {
                    "triggered": {
                        key: {
                            "ts": 1,
                            "used_percent": 97.0,
                            "redeem_attempted": True,
                            "redeem_ok": False,
                            "redeem_message": "failed",
                        }
                    }
                }
            )
        )
        handoff = self.workspace / "handoff.org"
        original = "# usage-guard:begin\n* user data without an end marker\n"
        handoff.write_text(original)
        self._usage(98.1)
        decision = self._run()
        self.assertEqual(decision["action"], "handoff_failed")
        self.assertEqual(handoff.read_text(), original)


if __name__ == "__main__":
    unittest.main()
