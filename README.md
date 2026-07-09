# Codex Public

This repository contains shareable Codex plugins and related assets.

## Plugins

### Usage Guard

`usage-guard` is a callback-only Codex plugin that checks structured Codex usage
events after tool calls and at turn stop time. It is designed to warn when a
usage window crosses a configured threshold, without running a background
daemon, file tailer, or polling loop.

Current default threshold: `97%`.

Plugin path:

```text
plugins/usage-guard
```

## How Usage Guard Works

1. Codex runs the plugin hooks on `PreToolUse`, `PostToolUse`, and `Stop`.
2. The hook executes `scripts/usage_guard_once.py`.
3. The script reads the freshest structured `token_count` event from the local
   Codex session JSONL files.
4. If primary or secondary usage is at or above the configured threshold, the
   plugin writes an audit entry and can show a macOS notification.
5. If structured usage data exposes reset-credit information, the plugin records
   whether a redeem opportunity appears to exist.

The plugin intentionally exits after each callback. It does not stay resident.

When a hook payload includes the current session or transcript path, Usage Guard
uses that file first. Fallback discovery scans recent session files and chooses
the file with the newest `token_count.timestamp`.

## Important Boundary

The current public Codex plugin and hook APIs do not expose a stable command for
consuming rate-limit reset credits. For that reason, `usage-guard` does not fake
TUI input or call private endpoints.

If a possible redeem opportunity is visible in structured usage events, the
plugin alerts the user to run `/usage` and records the finding in its audit log.

`usage-guard` includes an unsupported backend redeem strategy. It reads the
existing Codex login token from `${CODEX_HOME:-~/.codex}/auth.json`, lists
banked reset credits, and consumes one available credit when the configured
threshold is reached.

This path uses ChatGPT backend endpoints that are not exposed as a public Codex
CLI command. It does not bypass limits; it only spends a banked reset credit
already available to the logged-in account.

## Repository Layout

```text
.
|-- .agents/plugins/marketplace.json
|-- plugins/
|   `-- usage-guard/
|       |-- .codex-plugin/plugin.json
|       |-- hooks/hooks.json
|       |-- scripts/usage_guard_once.py
|       `-- skills/usage-guard/SKILL.md
`-- README.md
```

## Local Install

Clone this repository:

```bash
git clone git@github.com:ray123454321/codex-public.git
cd codex-public
```

Then add the repository as a Codex plugin source or install
`plugins/usage-guard` through your Codex plugin workflow.

The included marketplace entry points to the local plugin directory:

```json
{
  "name": "usage-guard",
  "source": {
    "source": "local",
    "path": "./plugins/usage-guard"
  }
}
```

## Runtime Files

Runtime state is stored outside this repository:

```text
${CODEX_HOME:-~/.codex}/usage-guard/config.json
${CODEX_HOME:-~/.codex}/usage-guard/state.json
${CODEX_HOME:-~/.codex}/usage-guard/usage_guard.log.jsonl
```

These files are intentionally not committed.

## Configuration

Default config:

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

Useful settings:

- `threshold_percent`: trigger threshold for primary or secondary usage.
- `recent_seconds`: how far back to search for recent session files.
- `settle_timeout_ms`: wait window for final turn accounting to settle.
- `log_below_threshold`: write logs even when usage is below threshold.
- `notify`: show local notifications for threshold alerts or redeem failures.
- `notify_on_redeem_success`: also notify when auto redeem succeeds. Defaults
  to false so successful auto redeem stays quiet.
- `auto_redeem`: automatically try to redeem when the threshold is reached.
- `redeem_strategy`: `chatgpt_backend` or `command`.
- `chatgpt_backend_base`: backend base URL for the built-in strategy.
- `auth_path`: optional path to Codex `auth.json`.
- `redeem_command`: command array or shell-style string for `command` strategy.
- `redeem_timeout_ms`: maximum runtime for backend calls or `redeem_command`.

To enable built-in auto redeem:

```json
{
  "auto_redeem": true,
  "redeem_strategy": "chatgpt_backend"
}
```

With `chatgpt_backend`, Usage Guard calls:

```text
GET  /wham/rate-limit-reset-credits
POST /wham/rate-limit-reset-credits/consume
```

It sends the bearer token and account id from Codex `auth.json`. Tokens are not
written to the audit log.

When `redeem_command` runs, Usage Guard passes these environment variables:

```text
USAGE_GUARD_WINDOW
USAGE_GUARD_USED_PERCENT
USAGE_GUARD_LIMIT_ID
USAGE_GUARD_CREDITS
```

Example adapter shape:

```json
{
  "auto_redeem": true,
  "redeem_strategy": "command",
  "redeem_command": ["/path/to/supported-redeem-command"],
  "redeem_timeout_ms": 5000
}
```

## Manual Checks

Run the installed checker manually:

```bash
/usr/bin/env python3 "$(ls -dt "${CODEX_HOME:-$HOME/.codex}"/plugins/cache/*/usage-guard/*/scripts/usage_guard_once.py | head -n 1)" --print
```

Inspect recent decisions:

```bash
tail -20 "${CODEX_HOME:-$HOME/.codex}/usage-guard/usage_guard.log.jsonl"
```

## Validation

The plugin should validate with the Codex plugin and skill validation helpers:

```bash
python3 /path/to/validate_plugin.py plugins/usage-guard
python3 /path/to/quick_validate.py plugins/usage-guard/skills/usage-guard
```
