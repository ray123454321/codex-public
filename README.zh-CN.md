# Codex Public

[English](README.md) | 简体中文

`codex-public` 是用于共享 Codex 插件的公开 marketplace。根目录文档只承担索引职责；
每个插件的具体行为、安装、安全边界和验证方式都由 `plugins/<name>/` 下的插件文档维护。

## 已发布插件

| 插件 | 用途 | 文档 |
| --- | --- | --- |
| `usage-guard` | 算力紧张时保留当前模型；使用率达到 97% 时自动使用重置机会；redeem 失败且使用率超过 98% 时准备 `handoff.org`。 | [English](plugins/usage-guard/README.md) · [简体中文](plugins/usage-guard/README.zh-CN.md) |

Marketplace 的单一真相源是
[`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json)。只有同时登记在该文件中且
插件目录通过验证，插件才算正式发布。

## 从 Marketplace 安装

将本仓库添加为 Git marketplace，然后安装 Usage Guard：

```bash
codex plugin marketplace add git@github.com:ray123454321/codex-public.git --ref main
codex plugin add usage-guard@codex-public
```

刷新已有安装：

```bash
codex plugin marketplace upgrade codex-public
```

安装或升级 Usage Guard 后，继续执行插件文档中的
[安装与验证步骤](plugins/usage-guard/README.zh-CN.md#安装)。仅更新 marketplace 元数据不能证明
运行态已经生效。

## 仓库结构

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

运行时状态保存在仓库之外。具体状态路径和操作边界以所选插件的 README 为准。

## 验证文档链接

```bash
python3 -m unittest discover -s tests -v
```

该测试会检查根目录语言切换，并验证 marketplace 中每个已发布插件都能从两个根 README
链接到对应插件文档。
