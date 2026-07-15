---
name: usage-guard
description: Install, inspect, configure, or troubleshoot the local Usage Guard Codex plugin that keeps the current model during safety buffering, automatically consumes a reset credit at 97%, and prepares handoff.org after a failed redeem when usage exceeds 98%.
---

# Usage & Continuity Guard

Use this skill for the local `usage-guard` plugin, Codex usage alerts,
reset-credit behavior, emergency handoff, or automatic Keep Waiting selection.

## Three responsibilities

1. **Preserve model capability.** When the complete `Additional safety checks`
   menu offers `1. Retry with a faster model` and `2. Keep waiting`, the PTY
   component chooses `2`. It does not handle approvals or arbitrary menus.
2. **Use a reset opportunity.** At 97% usage, consume one banked reset credit
   for the current reset-window trigger key. Never repeat the same redeem merely
   because another hook fires.
3. **Preserve the task.** If that redeem is proven failed and the same usage
   window later exceeds 98%, create or refresh the managed Usage Guard block in
   the active task's `handoff.org` while preserving all other notes.

## Runtime architecture

- `hooks/hooks.json` invokes `scripts/usage_guard_once.py` on `PreToolUse`,
  `PostToolUse`, and `Stop`.
- `usage_guard_once.py` reads the hook payload's `transcript_path` when present,
  otherwise the freshest structured `token_count` event.
- `scripts/codex_keep_waiting.py` is a separate semantic PTY component because
  Codex does not expose the safety-buffering view as a plugin hook.
- Neither component runs a daemon or a polling loop.
- Unknown UI, unknown redeem truth, and malformed handoff markers fail closed.

The `chatgpt_backend` redeem strategy uses the existing Codex login from
`${CODEX_HOME:-~/.codex}/auth.json` to list and consume a banked reset credit.
It is not a public Codex API and does not bypass limits. Environments that do
not accept that strategy must use `redeem_strategy=command` with an explicit
local command.

## Install and verify

From the plugin root:

```bash
python3 scripts/install.py
python3 scripts/verify.py --full
```

Treat the following as executable truth before reporting success:

- the active plugin is `usage-guard@codex-public`;
- a new shell resolves `codex` to `~/.local/bin/codex`;
- the deployed wrapper digest equals the plugin source;
- wrapped and direct `codex --version` outputs match;
- quota/handoff tests and exact PTY replay pass;
- active config has `auto_redeem=true`, threshold `97.0`, and strict handoff
  threshold `>98.0`.

## Files

- Hook config: `hooks/hooks.json`
- Quota/handoff component: `scripts/usage_guard_once.py`
- Keep Waiting component: `scripts/codex_keep_waiting.py`
- Installer: `scripts/install.py`
- Verifier: `scripts/verify.py`
- Config: `${CODEX_HOME:-~/.codex}/usage-guard/config.json`
- Quota audit: `${CODEX_HOME:-~/.codex}/usage-guard/usage_guard.log.jsonl`
- Keep Waiting audit: `${CODEX_HOME:-~/.codex}/usage-guard/keep_waiting.log.jsonl`
- Install receipt: `${CODEX_HOME:-~/.codex}/usage-guard/keep_waiting_install.json`
- Emergency handoff: `<session_meta.cwd>/handoff.org`

## Safety invariants

- Exact 98% does not write a handoff; the threshold is strictly greater.
- Handoff requires persisted `redeem_ok=false` for the same reset-window key.
- A successful redeem never creates a handoff.
- Keep Waiting requires the title, numbered retry option, numbered waiting
  option, and confirmation footer in one rendered view.
- Immediate menu redraws produce at most one injected `2` and one receipt.
- No prompt text, cookies, authentication token, or conversation content is
  written to the Keep Waiting audit.
- Plugin updates require refreshing the installed cache and rerunning the
  installer; marketplace metadata alone is not runtime proof.

## Common diagnostics

```bash
tail -20 "${CODEX_HOME:-$HOME/.codex}/usage-guard/usage_guard.log.jsonl"
tail -20 "${CODEX_HOME:-$HOME/.codex}/usage-guard/keep_waiting.log.jsonl"
~/.local/bin/codex --usage-guard-diagnose
```
