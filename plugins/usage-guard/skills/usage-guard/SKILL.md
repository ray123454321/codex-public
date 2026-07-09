---
name: usage-guard
description: Inspect, configure, or troubleshoot the local Usage Guard Codex plugin that checks Codex account usage after each turn through a Stop hook without a daemon or polling loop.
---

# Usage Guard

Use this skill when the user asks about the local `usage-guard` plugin, Codex usage alerts, rate-limit threshold behavior, or redeem/reset-credit automation.

## What It Does

Usage Guard is callback-only:

1. Codex runs `hooks/hooks.json` on `PreToolUse`, `PostToolUse`, and `Stop` lifecycle events.
2. The hook executes `scripts/usage_guard_once.py`.
3. The script reads the freshest `token_count` event from `$CODEX_HOME/sessions/**/*.jsonl`.
4. Tool-level callbacks use a short settle window to avoid slowing the agent loop; `Stop` uses a longer settle window to catch final turn accounting.
5. If `primary` or `secondary` usage is at or above the configured threshold, it writes an audit log and optionally shows a macOS notification.

It intentionally does not run a daemon, tail files, or poll in a loop.

## Important Boundary

Current public Codex plugin and hook APIs do not expose a stable command for consuming rate-limit reset credits. The script does not fake TUI input.

Usage Guard includes an unsupported `chatgpt_backend` redeem strategy. It reads the existing Codex login token from `${CODEX_HOME:-~/.codex}/auth.json`, lists banked reset credits, and consumes one available credit when the configured threshold is reached. It does not bypass limits; it only spends a banked reset credit already available to the logged-in account.

For environments that should not use the backend strategy, set `redeem_strategy` to `command` and provide a local `redeem_command`.

## Files

- Plugin root: the installed `usage-guard` plugin directory.
- Hook config: `hooks/hooks.json` inside the plugin.
- One-shot checker: `scripts/usage_guard_once.py` inside the plugin.
- Config: `${CODEX_HOME:-~/.codex}/usage-guard/config.json`.
- Audit log: `${CODEX_HOME:-~/.codex}/usage-guard/usage_guard.log.jsonl`.

## Common Commands

Inspect the latest decision:

```bash
tail -20 "${CODEX_HOME:-$HOME/.codex}/usage-guard/usage_guard.log.jsonl"
```

Run the checker manually:

```bash
/usr/bin/env python3 "$(ls -dt "${CODEX_HOME:-$HOME/.codex}"/plugins/cache/*/usage-guard/*/scripts/usage_guard_once.py | head -n 1)" --print
```

Test against a specific session file:

```bash
/usr/bin/env python3 "$(ls -dt "${CODEX_HOME:-$HOME/.codex}"/plugins/cache/*/usage-guard/*/scripts/usage_guard_once.py | head -n 1)" --event-file ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl --print
```

Change the threshold by editing `state/config.json`:

```json
{
  "threshold_percent": 97.0,
  "recent_seconds": 900,
  "settle_timeout_ms": 3000,
  "settle_interval_ms": 200,
  "log_below_threshold": false,
  "notify": true,
  "notify_on_redeem_success": false,
  "auto_redeem": false,
  "redeem_strategy": "chatgpt_backend",
  "redeem_command": null,
  "redeem_timeout_ms": 5000,
  "chatgpt_backend_base": "https://chatgpt.com/backend-api",
  "auth_path": null
}
```

Set `auto_redeem` to true to enable built-in backend redeem:

```json
{
  "auto_redeem": true,
  "redeem_strategy": "chatgpt_backend"
}
```

With `chatgpt_backend`, the plugin calls:

- `GET /wham/rate-limit-reset-credits`
- `POST /wham/rate-limit-reset-credits/consume`

When `redeem_strategy` is `command`, `redeem_command` receives these environment variables:

- `USAGE_GUARD_WINDOW`
- `USAGE_GUARD_USED_PERCENT`
- `USAGE_GUARD_LIMIT_ID`
- `USAGE_GUARD_CREDITS`
