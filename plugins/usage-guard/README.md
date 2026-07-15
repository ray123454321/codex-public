# Usage & Continuity Guard

`usage-guard` protects an in-flight Codex task when compute or quota pressure
rises. It has exactly three responsibilities:

1. keep the current model when Codex offers a faster, less capable retry;
2. consume one available reset credit when usage reaches 97%;
3. prepare `handoff.org` when that redeem failed and the same window exceeds
   98% usage.

## Runtime boundaries

The quota and handoff path is callback-only. `hooks/hooks.json` invokes
`scripts/usage_guard_once.py`, which reads structured `token_count` events and
persists one decision per reset-window trigger key.

Codex does not expose the **Additional safety checks** view as a plugin hook.
`scripts/codex_keep_waiting.py` is therefore an isolated PTY component installed
at `~/.local/bin/codex`. It injects only the character `2` after one rendered
view contains all four markers:

- `Additional safety checks`
- `1. Retry with a faster model`
- `2. Keep waiting` (or `2. Dismiss and keep waiting`)
- `Press enter to confirm or esc to go back`

It does not use coordinates, screenshots, OCR, mouse events, or generic
approval matching. Unknown or changed UI fails closed and receives no input.

## Install

```bash
python3 scripts/install.py
```

The installer atomically deploys the PTY component, enables the active
97%/98% policy in `${CODEX_HOME:-~/.codex}/usage-guard/config.json`, migrates the
legacy Keep Waiting receipt, and refuses to replace an unrelated `codex`
launcher unless `--force` is explicit.

## Verify

```bash
python3 scripts/verify.py --full
```

This verifies:

- the canonical `usage-guard` marketplace entry;
- the enabled `usage-guard@codex-public` install matches the source manifest version;
- active redeem and handoff policy;
- deployed-wrapper digest and real-Codex version passthrough;
- quota/redeem/handoff tests;
- exact PTY replay, incomplete-view rejection, and redraw deduplication.

## State and evidence

```text
${CODEX_HOME:-~/.codex}/usage-guard/config.json
${CODEX_HOME:-~/.codex}/usage-guard/state.json
${CODEX_HOME:-~/.codex}/usage-guard/usage_guard.log.jsonl
${CODEX_HOME:-~/.codex}/usage-guard/keep_waiting.log.jsonl
${CODEX_HOME:-~/.codex}/usage-guard/keep_waiting_install.json
```

The Keep Waiting receipt contains no prompt or conversation text. Usage Guard
does not bypass limits: it only consumes a banked reset credit already
available to the authenticated account.
