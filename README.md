# Codex Public

English | [简体中文](README.zh-CN.md)

`codex-public` is a public marketplace for shareable Codex plugins. The root
documentation is an index; each plugin owns its detailed behavior, installation,
safety, and verification documentation under `plugins/<name>/`.

## Published plugins

| Plugin | Purpose | Documentation |
| --- | --- | --- |
| `usage-guard` | Keep the current model under compute pressure, automatically use a reset opportunity at 97%, and prepare `handoff.org` after a failed redeem when usage exceeds 98%. | [English](plugins/usage-guard/README.md) · [简体中文](plugins/usage-guard/README.zh-CN.md) |

The marketplace source of truth is
[`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json). A plugin
is published only when it is registered there and its plugin directory validates.

## Install from the marketplace

Add this repository as a Git marketplace and install Usage Guard:

```bash
codex plugin marketplace add git@github.com:ray123454321/codex-public.git --ref main
codex plugin add usage-guard@codex-public
```

Refresh an existing installation:

```bash
codex plugin marketplace upgrade codex-public
```

After installing or upgrading Usage Guard, follow its
[installation and verification instructions](plugins/usage-guard/README.md#install).
Marketplace metadata alone is not runtime proof.

## Repository layout

```text
.
|-- .agents/plugins/marketplace.json
|-- plugins/
|   `-- usage-guard/
|       |-- .codex-plugin/plugin.json
|       |-- README.md
|       |-- README.zh-CN.md
|       |-- hooks/
|       |-- scripts/
|       |-- skills/
|       `-- tests/
|-- tests/test_readmes.py
|-- README.md
`-- README.zh-CN.md
```

Runtime state is stored outside this repository. See the selected plugin's
README for its exact state paths and operational boundaries.

## Validate documentation links

```bash
python3 -m unittest discover -s tests -v
```

This checks the root language switch and verifies that every published plugin
is linked from both root README versions.
