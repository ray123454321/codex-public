#!/usr/bin/env python3
"""Install Usage Guard's compute-continuity wrapper and active policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PLUGIN_ROOT / "scripts" / "codex_keep_waiting.py"
DEFAULT_TARGET = Path("~/.local/bin/codex").expanduser()
NEW_MARKER = b"USAGE_GUARD_KEEP_WAITING_WRAPPER_MARKER_v1"
LEGACY_MARKER = b"CODEX_KEEP_WAITING_WRAPPER_MARKER_v1"
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
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    return codex_home / "usage-guard"


STATE_DIR = state_dir()
CONFIG = STATE_DIR / "config.json"
RECEIPT = STATE_DIR / "keep_waiting_install.json"
LEGACY_RECEIPT = Path("~/.local/share/codex-keep-waiting/install.json").expanduser()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_our_wrapper(path: Path) -> bool:
    try:
        prefix = path.read_bytes()[:65_536]
        return NEW_MARKER in prefix or LEGACY_MARKER in prefix
    except OSError:
        return False


def read_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return value


def write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def install(target: Path, force: bool) -> dict[str, object]:
    target = target.expanduser().absolute()
    backup: Path | None = None
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not is_our_wrapper(target):
        if not force:
            raise RuntimeError(
                f"refusing to replace unrelated executable {target}; inspect it or use --force"
            )
        backup = target.with_name(f"{target.name}.pre-usage-guard-{int(time.time())}")
        shutil.copy2(target, backup)

    source_bytes = SOURCE.read_bytes()
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(source_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(
        stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IXUSR
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH
    )
    os.replace(temporary, target)

    diagnosis = subprocess.run(
        [str(target), "--usage-guard-diagnose"],
        check=True,
        capture_output=True,
        text=True,
    )
    diagnosed = json.loads(diagnosis.stdout)
    config = read_json_object(CONFIG)
    prior_policy = {key: config.get(key) for key in ACTIVE_POLICY}
    config.update(ACTIVE_POLICY)
    write_json_atomic(CONFIG, config)

    legacy_receipt: dict[str, object] | None = None
    if LEGACY_RECEIPT.exists():
        legacy_receipt = read_json_object(LEGACY_RECEIPT)

    receipt = {
        "backup": str(backup) if backup else None,
        "config": str(CONFIG),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "legacy_receipt": legacy_receipt,
        "managed_policy": ACTIVE_POLICY,
        "plugin_root": str(PLUGIN_ROOT),
        "prior_policy": prior_policy,
        "real_codex": diagnosed["real_codex"],
        "sha256": sha256(target),
        "target": str(target),
        "wrapper_version": diagnosed["version"],
    }
    write_json_atomic(RECEIPT, receipt)
    if legacy_receipt is not None:
        LEGACY_RECEIPT.unlink()
    return receipt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        receipt = install(args.target, args.force)
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"install failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
