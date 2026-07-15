#!/usr/bin/env python3
"""Verify all three Usage Guard continuity responsibilities."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PLUGIN_ROOT / "scripts" / "codex_keep_waiting.py"
WRAPPER = Path("~/.local/bin/codex").expanduser()
SOURCE_MARKETPLACE = (
    PLUGIN_ROOT.parents[1] / ".agents" / "plugins" / "marketplace.json"
)
EXPECTED_MARKETPLACE = "codex-public"
EXPECTED_MARKETPLACE_SOURCE = "git@github.com:ray123454321/codex-public.git"
MARKER = b"USAGE_GUARD_KEEP_WAITING_WRAPPER_MARKER_v1"
ACTIVE_POLICY = {
    "enabled": True,
    "threshold_percent": 97.0,
    "auto_redeem": True,
    "handoff_on_redeem_failure": True,
    "handoff_threshold_percent": 98.0,
}


def state_dir() -> Path:
    override = os.environ.get("USAGE_GUARD_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "usage-guard"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="also run unit and PTY replay tests")
    return parser.parse_args()


def login_shell_codex() -> str | None:
    shell = os.environ.get("SHELL", "/bin/zsh")
    marker = "__USAGE_GUARD_CODEX__"
    result = subprocess.run(
        [
            shell,
            "-lic",
            (
                "unalias codex 2>/dev/null || true; "
                'resolved=$(command -v codex); '
                f'printf "{marker}%s\\n" "$resolved"'
            ),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(marker):
            return line.removeprefix(marker)
    return None


def main() -> int:
    args = parse_args()
    checks: dict[str, object] = {}

    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())
    checks["manifest_name"] = manifest.get("name")
    checks["manifest_version"] = manifest.get("version")

    checks["source_marketplace_file"] = (
        str(SOURCE_MARKETPLACE) if SOURCE_MARKETPLACE.is_file() else None
    )
    marketplace_registered = False
    if SOURCE_MARKETPLACE.is_file():
        marketplace = json.loads(SOURCE_MARKETPLACE.read_text())
        marketplace_registered = any(
            item.get("name") == "usage-guard"
            for item in marketplace.get("plugins", [])
        )

    config_path = state_dir() / "config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    active_policy_checks = {
        key: config.get(key) == expected for key, expected in ACTIVE_POLICY.items()
    }
    checks["active_policy"] = active_policy_checks
    checks["active_policy_ok"] = all(active_policy_checks.values())

    checks["wrapper_exists"] = WRAPPER.is_file()
    checks["wrapper_marker"] = (
        checks["wrapper_exists"] and MARKER in WRAPPER.read_bytes()[:65_536]
    )
    checks["wrapper_current"] = (
        checks["wrapper_exists"] and sha256(WRAPPER) == sha256(SOURCE)
    )
    resolved_login_codex = login_shell_codex()
    checks["login_shell_codex"] = resolved_login_codex
    checks["login_shell_uses_wrapper"] = bool(
        resolved_login_codex
        and Path(resolved_login_codex).resolve() == WRAPPER.resolve()
    )

    diagnosis: dict[str, object] = {}
    if checks["wrapper_exists"]:
        result = subprocess.run(
            [str(WRAPPER), "--usage-guard-diagnose"],
            capture_output=True,
            text=True,
        )
        checks["diagnose_exit"] = result.returncode
        if result.returncode == 0:
            diagnosis = json.loads(result.stdout)
    checks["real_codex"] = diagnosis.get("real_codex")

    if checks["real_codex"]:
        plugin_list = subprocess.run(
            [str(checks["real_codex"]), "plugin", "list", "--json"],
            capture_output=True,
            text=True,
        )
        checks["plugin_list_exit"] = plugin_list.returncode
        if plugin_list.returncode == 0:
            plugin_inventory = json.loads(plugin_list.stdout)
            installed_plugin = next(
                (
                    item
                    for item in plugin_inventory.get("installed", [])
                    if item.get("pluginId") == "usage-guard@codex-public"
                ),
                None,
            )
            if installed_plugin:
                checks["installed_plugin_id"] = installed_plugin.get("pluginId")
                checks["installed_plugin_version"] = installed_plugin.get("version")
                checks["installed_plugin_enabled"] = installed_plugin.get("enabled")
                marketplace_source = installed_plugin.get("marketplaceSource") or {}
                checks["installed_marketplace_source"] = marketplace_source.get(
                    "source"
                )
                checks["installed_marketplace_current"] = bool(
                    installed_plugin.get("marketplaceName") == EXPECTED_MARKETPLACE
                    and marketplace_source.get("source")
                    == EXPECTED_MARKETPLACE_SOURCE
                )
                marketplace_registered = bool(
                    marketplace_registered
                    or checks["installed_marketplace_current"]
                )
                checks["installed_plugin_current"] = bool(
                    installed_plugin.get("installed")
                    and installed_plugin.get("enabled")
                    and installed_plugin.get("version") == manifest.get("version")
                )

        direct = subprocess.run(
            [str(checks["real_codex"]), "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        wrapped = subprocess.run(
            [str(WRAPPER), "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        checks["direct_version"] = direct
        checks["wrapped_version"] = wrapped
        checks["version_passthrough"] = direct == wrapped

    checks["marketplace_registered"] = marketplace_registered

    if args.full:
        test_environment = os.environ.copy()
        test_environment["USAGE_GUARD_WRAPPER_UNDER_TEST"] = str(WRAPPER)
        test_environment["PYTHONDONTWRITEBYTECODE"] = "1"
        tests = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(PLUGIN_ROOT / "tests"), "-v"],
            cwd=PLUGIN_ROOT,
            env=test_environment,
        )
        checks["tests_exit"] = tests.returncode

    required = [
        checks.get("manifest_name") == "usage-guard",
        checks.get("marketplace_registered") is True,
        checks.get("active_policy_ok") is True,
        checks.get("wrapper_marker") is True,
        checks.get("wrapper_current") is True,
        checks.get("login_shell_uses_wrapper") is True,
        checks.get("diagnose_exit") == 0,
        checks.get("installed_marketplace_current") is True,
        checks.get("installed_plugin_current") is True,
        checks.get("version_passthrough") is True,
    ]
    if args.full:
        required.append(checks.get("tests_exit") == 0)

    checks["ok"] = all(required)
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if checks["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
