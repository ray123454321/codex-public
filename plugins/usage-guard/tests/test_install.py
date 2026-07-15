from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = PLUGIN_ROOT / "scripts" / "install.py"
WRAPPER_MARKER = b"USAGE_GUARD_KEEP_WAITING_WRAPPER_MARKER_v1"


class InstallerMigrationTests(unittest.TestCase):
    def test_migrates_legacy_receipt_and_activates_all_three_policies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            codex_home = home / ".codex"
            state_dir = codex_home / "usage-guard"
            target = home / ".local" / "bin" / "codex"
            real_codex = root / "real-codex"
            real_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            real_codex.chmod(real_codex.stat().st_mode | stat.S_IXUSR)

            state_dir.mkdir(parents=True)
            (state_dir / "config.json").write_text(
                json.dumps(
                    {
                        "auto_redeem": False,
                        "notify": False,
                    }
                ),
                encoding="utf-8",
            )
            legacy_receipt = (
                home
                / ".local"
                / "share"
                / "codex-keep-waiting"
                / "install.json"
            )
            legacy_receipt.parent.mkdir(parents=True)
            legacy_receipt.write_text(
                json.dumps({"plugin_root": "/legacy/codex-keep-waiting"}),
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment.update(
                {
                    "CODEX_HOME": str(codex_home),
                    "HOME": str(home),
                    "USAGE_GUARD_REAL_CODEX": str(real_codex),
                    "USAGE_GUARD_STATE_DIR": str(state_dir),
                }
            )
            result = subprocess.run(
                [sys.executable, str(INSTALLER), "--target", str(target)],
                check=True,
                capture_output=True,
                env=environment,
                text=True,
            )

            emitted_receipt = json.loads(result.stdout)
            config = json.loads((state_dir / "config.json").read_text())
            receipt = json.loads(
                (state_dir / "keep_waiting_install.json").read_text()
            )

            self.assertTrue(config["enabled"])
            self.assertEqual(config["threshold_percent"], 97.0)
            self.assertTrue(config["auto_redeem"])
            self.assertTrue(config["handoff_on_redeem_failure"])
            self.assertEqual(config["handoff_threshold_percent"], 98.0)
            self.assertFalse(config["notify"])
            self.assertEqual(receipt, emitted_receipt)
            self.assertEqual(
                receipt["legacy_receipt"]["plugin_root"],
                "/legacy/codex-keep-waiting",
            )
            self.assertEqual(receipt["prior_policy"]["auto_redeem"], False)
            self.assertFalse(legacy_receipt.exists())
            self.assertIn(WRAPPER_MARKER, target.read_bytes()[:65_536])


if __name__ == "__main__":
    unittest.main()
