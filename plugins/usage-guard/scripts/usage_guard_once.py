#!/usr/bin/env python3
"""One-shot Codex usage guard for Stop hooks.

This script intentionally does not run as a daemon and does not poll. Codex
invokes it at Stop hook time; it reads the freshest structured session event and
exits.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def state_dir() -> Path:
    override = os.environ.get("USAGE_GUARD_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return codex_home() / "usage-guard"


STATE_DIR = state_dir()
CONFIG_PATH = STATE_DIR / "config.json"
STATE_PATH = STATE_DIR / "state.json"
LOG_PATH = STATE_DIR / "usage_guard.log.jsonl"

DEFAULT_CONFIG = {
    "threshold_percent": 97.0,
    "recent_seconds": 900,
    "settle_timeout_ms": 3000,
    "settle_interval_ms": 200,
    "log_below_threshold": False,
    "notify": True,
    "auto_redeem": False,
    "redeem_strategy": "chatgpt_backend",
    "redeem_command": None,
    "redeem_timeout_ms": 5000,
    "chatgpt_backend_base": "https://chatgpt.com/backend-api",
    "auth_path": None,
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except Exception as exc:
        return {"_error": f"failed to read {path}: {exc}", **(default if isinstance(default, dict) else {})}


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def append_log(entry: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def read_hook_stdin() -> dict[str, Any]:
    try:
        data = sys.stdin.read()
    except Exception:
        return {}
    if not data.strip():
        return {}
    try:
        return json.loads(data)
    except Exception:
        return {"raw_stdin": data[:4096]}


def find_paths(obj: Any) -> list[Path]:
    paths: list[Path] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_s = str(key).lower()
            if isinstance(value, str) and ("session" in key_s or "transcript" in key_s or "rollout" in key_s):
                p = Path(value).expanduser()
                if p.exists() and p.is_file():
                    paths.append(p)
            else:
                paths.extend(find_paths(value))
    elif isinstance(obj, list):
        for value in obj:
            paths.extend(find_paths(value))
    return paths


def newest_existing_file(paths: list[Path]) -> Path | None:
    existing = []
    for path in paths:
        try:
            if path.exists() and path.is_file():
                existing.append(path)
        except OSError:
            continue
    if not existing:
        return None
    return max(set(existing), key=lambda p: p.stat().st_mtime)


def latest_session_file(recent_seconds: int, hook_payload: dict[str, Any]) -> Path | None:
    payload_candidate = newest_existing_file(find_paths(hook_payload))
    if payload_candidate:
        return payload_candidate

    candidates: list[Path] = []
    root = codex_home() / "sessions"
    now = time.time()
    if root.exists():
        for path in root.rglob("*.jsonl"):
            try:
                if now - path.stat().st_mtime <= recent_seconds:
                    candidates.append(path)
            except OSError:
                continue
    if not candidates:
        return None
    freshest: tuple[str, Path] | None = None
    for path in set(candidates):
        event = latest_token_count(path)
        if not event:
            continue
        timestamp = str(event.get("timestamp") or "")
        if freshest is None or timestamp > freshest[0]:
            freshest = (timestamp, path)
    if freshest:
        return freshest[1]
    return max(set(candidates), key=lambda p: p.stat().st_mtime)


def iter_lines_reverse(path: Path, max_bytes: int = 2_000_000) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()
        data = fh.read()
    return data.decode("utf-8", errors="replace").splitlines()[::-1]


def latest_token_count(path: Path) -> dict[str, Any] | None:
    for line in iter_lines_reverse(path):
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("type") != "event_msg":
            continue
        payload = event.get("payload") or {}
        if payload.get("type") == "token_count" and payload.get("rate_limits"):
            return {"timestamp": event.get("timestamp"), "payload": payload}
    return None


def settled_token_count(path: Path, timeout_ms: int, interval_ms: int) -> dict[str, Any] | None:
    """Return the latest token_count after a short Stop-hook settle window."""
    deadline = time.time() + max(timeout_ms, 0) / 1000.0
    best: dict[str, Any] | None = None
    last_seen: tuple[str | None, int] | None = None
    stable_reads = 0

    while True:
        event = latest_token_count(path)
        if event:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            marker = (event.get("timestamp"), size)
            best = event
            if marker == last_seen:
                stable_reads += 1
            else:
                stable_reads = 0
                last_seen = marker
            if stable_reads >= 1:
                return best

        if time.time() >= deadline:
            return best
        time.sleep(max(interval_ms, 50) / 1000.0)


def window_percent(rate_limits: dict[str, Any], name: str) -> float:
    window = rate_limits.get(name)
    if not isinstance(window, dict):
        return 0.0
    try:
        return float(window.get("used_percent") or 0.0)
    except Exception:
        return 0.0


def trigger_key(rate_limits: dict[str, Any], window_name: str) -> str:
    window = rate_limits.get(window_name) or {}
    return "|".join(
        [
            str(rate_limits.get("limit_id") or "unknown-limit"),
            window_name,
            str(window.get("resets_at") or "unknown-reset"),
        ]
    )


def credits_available(rate_limits: dict[str, Any]) -> bool:
    credits = rate_limits.get("credits")
    if not isinstance(credits, dict):
        return False
    if credits.get("unlimited") is True:
        return False
    if credits.get("has_credits") is True:
        return True
    balance = credits.get("balance")
    try:
        return balance is not None and float(balance) > 0
    except Exception:
        return False


def notify(title: str, message: str) -> None:
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def command_argv(command: Any) -> list[str]:
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        return command
    if isinstance(command, str) and command.strip():
        return shlex.split(command)
    return []


def auth_path(config: dict[str, Any]) -> Path:
    configured = config.get("auth_path")
    if configured:
        return Path(str(configured)).expanduser()
    return codex_home() / "auth.json"


def load_auth(config: dict[str, Any]) -> tuple[str, str]:
    path = auth_path(config)
    data = load_json(path, {})
    token = data.get("access_token") or (data.get("tokens") or {}).get("access_token")
    account_id = data.get("account_id") or (data.get("tokens") or {}).get("account_id")
    if not token or not account_id:
        raise RuntimeError(f"auth file is missing access_token/account_id: {path}")
    return str(token), str(account_id)


def backend_request(
    config: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    token, account_id = load_auth(config)
    base = str(config.get("chatgpt_backend_base") or "https://chatgpt.com/backend-api").rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}{path}", method=method, data=data)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("ChatGPT-Account-Id", account_id)
    req.add_header("User-Agent", "usage-guard/0.1")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    timeout = max(float(config.get("redeem_timeout_ms") or 5000) / 1000.0, 0.1)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode()
            try:
                return response.status, json.loads(raw) if raw else {}
            except Exception:
                return response.status, raw[:500]
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed: Any = json.loads(raw)
        except Exception:
            parsed = raw[:500]
        return exc.code, parsed
    except urllib.error.URLError as exc:
        raise RuntimeError(f"backend request failed: {exc.reason}") from exc


def backend_available_credits(config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    status, body = backend_request(config, "GET", "/wham/rate-limit-reset-credits")
    if status != 200:
        raise RuntimeError(f"list credits failed HTTP {status}: {safe_log_body(body)}")
    if not isinstance(body, dict):
        raise RuntimeError("list credits returned non-object response")
    credits = [credit for credit in body.get("credits") or [] if isinstance(credit, dict) and credit.get("status") == "available"]
    return credits, body


def safe_log_body(body: Any) -> str:
    text = json.dumps(body, sort_keys=True) if isinstance(body, (dict, list)) else str(body)
    return text[:500] + ("...<truncated>" if len(text) > 500 else "")


def attempt_backend_redeem(config: dict[str, Any]) -> tuple[bool, str]:
    credits, payload = backend_available_credits(config)
    available_count = payload.get("available_count")
    if not credits:
        return False, f"no available reset credits; available_count={available_count}"
    target = credits[0]
    credit_id = target.get("id")
    if not credit_id:
        return False, "available credit is missing id"
    request_id = str(uuid.uuid4())
    status, body = backend_request(
        config,
        "POST",
        "/wham/rate-limit-reset-credits/consume",
        {"credit_id": credit_id, "redeem_request_id": request_id},
    )
    if status != 200:
        return False, f"consume failed HTTP {status}: {safe_log_body(body)}"
    summary = {
        "credit_id": credit_id,
        "reset_type": target.get("reset_type"),
        "windows_reset": body.get("windows_reset") if isinstance(body, dict) else None,
        "code": body.get("code") if isinstance(body, dict) else None,
        "redeemed_at": ((body.get("credit") or {}).get("redeemed_at") if isinstance(body, dict) else None),
    }
    return True, f"consumed reset credit: {json.dumps(summary, sort_keys=True)}"


def attempt_command_redeem(config: dict[str, Any], rate_limits: dict[str, Any], window_name: str, used_percent: float) -> tuple[bool, str]:
    """Run a user-configured redeem command.

    This adapter is for explicit local commands supplied by the user or a future
    public CLI.
    """
    argv = command_argv(config.get("redeem_command"))
    if not argv:
        return (
            False,
            "auto_redeem is enabled, but redeem_command is not configured; no public Codex reset-credit command is available",
        )

    env = os.environ.copy()
    env.update(
        {
            "USAGE_GUARD_WINDOW": window_name,
            "USAGE_GUARD_USED_PERCENT": str(used_percent),
            "USAGE_GUARD_LIMIT_ID": str(rate_limits.get("limit_id") or ""),
            "USAGE_GUARD_CREDITS": json.dumps(rate_limits.get("credits"), sort_keys=True),
        }
    )
    timeout = max(float(config.get("redeem_timeout_ms") or 5000) / 1000.0, 0.1)
    try:
        result = subprocess.run(
            argv,
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"redeem_command timed out after {timeout:.1f}s"
    except Exception as exc:
        return False, f"redeem_command failed to start: {exc}"

    output = "\n".join(part.strip() for part in [result.stdout, result.stderr] if part and part.strip())
    if len(output) > 1000:
        output = output[:1000] + "...<truncated>"
    message = f"redeem_command exited {result.returncode}"
    if output:
        message += f": {output}"
    return result.returncode == 0, message


def attempt_redeem(config: dict[str, Any], rate_limits: dict[str, Any], window_name: str, used_percent: float) -> tuple[bool, str]:
    strategy = str(config.get("redeem_strategy") or "chatgpt_backend")
    try:
        if strategy == "chatgpt_backend":
            return attempt_backend_redeem(config)
        if strategy == "command":
            return attempt_command_redeem(config, rate_limits, window_name, used_percent)
        return False, f"unknown redeem_strategy: {strategy}"
    except Exception as exc:
        return False, f"redeem failed: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-file", type=Path, help="Read this session JSONL file instead of discovering the latest one.")
    parser.add_argument("--source", default="manual", help="Callback source label for audit logs.")
    parser.add_argument("--settle-timeout-ms", type=int, help="Override settle timeout for this invocation.")
    parser.add_argument("--settle-interval-ms", type=int, help="Override settle interval for this invocation.")
    parser.add_argument("--print", action="store_true", help="Print the decision JSON.")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_json(CONFIG_PATH, DEFAULT_CONFIG)
    config = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
    state = load_json(STATE_PATH, {"triggered": {}})
    hook_payload = read_hook_stdin()

    session_file = args.event_file or latest_session_file(int(config["recent_seconds"]), hook_payload)
    decision: dict[str, Any] = {
        "ts": int(time.time()),
        "session_file": str(session_file) if session_file else None,
        "source": args.source,
        "action": "none",
    }

    if not session_file:
        decision["reason"] = "no recent session jsonl found"
        append_log(decision)
        if args.print:
            print(json.dumps(decision, indent=2, sort_keys=True))
        return 0

    token_event = settled_token_count(
        session_file,
        int(args.settle_timeout_ms if args.settle_timeout_ms is not None else config["settle_timeout_ms"]),
        int(args.settle_interval_ms if args.settle_interval_ms is not None else config["settle_interval_ms"]),
    )
    if not token_event:
        decision["reason"] = "no token_count event found"
        append_log(decision)
        if args.print:
            print(json.dumps(decision, indent=2, sort_keys=True))
        return 0

    rate_limits = token_event["payload"]["rate_limits"]
    percents = {
        "primary": window_percent(rate_limits, "primary"),
        "secondary": window_percent(rate_limits, "secondary"),
    }
    window_name, used_percent = max(percents.items(), key=lambda item: item[1])
    decision.update(
        {
            "event_timestamp": token_event["timestamp"],
            "limit_id": rate_limits.get("limit_id"),
            "limit_name": rate_limits.get("limit_name"),
            "window": window_name,
            "used_percent": used_percent,
            "credits": rate_limits.get("credits"),
            "rate_limit_reached_type": rate_limits.get("rate_limit_reached_type"),
        }
    )

    if used_percent < float(config["threshold_percent"]):
        decision["reason"] = "below threshold"
        if config.get("log_below_threshold") is True or args.print:
            append_log(decision)
        if args.print:
            print(json.dumps(decision, indent=2, sort_keys=True))
        return 0

    key = trigger_key(rate_limits, window_name)
    if key in state.get("triggered", {}):
        decision["action"] = "deduped"
        decision["trigger_key"] = key
        append_log(decision)
        if args.print:
            print(json.dumps(decision, indent=2, sort_keys=True))
        return 0

    state.setdefault("triggered", {})[key] = {"ts": decision["ts"], "used_percent": used_percent}
    save_json(STATE_PATH, state)

    has_credit = credits_available(rate_limits)
    decision["trigger_key"] = key
    decision["has_redeem_opportunity"] = has_credit
    strategy = str(config.get("redeem_strategy") or "chatgpt_backend")
    should_try_redeem = config.get("auto_redeem") is True and (has_credit or strategy == "chatgpt_backend")

    if should_try_redeem:
        ok, message = attempt_redeem(config, rate_limits, window_name, used_percent)
        decision["action"] = "redeem_attempted"
        decision["redeem_strategy"] = strategy
        decision["redeem_ok"] = ok
        decision["redeem_message"] = message
        if ok:
            decision["has_redeem_opportunity"] = True
    elif has_credit:
        decision["action"] = "notify_redeem_available"
        decision["reason"] = "redeem opportunity detected; auto_redeem is disabled"
    else:
        decision["action"] = "notify_threshold"
        decision["reason"] = "threshold reached; no reset credit visible in token_count event"

    append_log(decision)
    if config.get("notify") is True:
        title = "Codex usage guard"
        msg = f"{window_name} usage is {used_percent:.1f}%"
        if has_credit:
            msg += "; reset credit may be available. Run /usage to confirm."
        notify(title, msg)
    if args.print:
        print(json.dumps(decision, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
