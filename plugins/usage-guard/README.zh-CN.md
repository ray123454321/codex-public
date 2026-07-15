# Usage & Continuity Guard（用量与连续性守卫）

[English](README.md) | 简体中文

`usage-guard` 在算力或额度紧张时保护正在执行的 Codex 任务。它只承担三项职责：

1. Codex 提议改用更快但能力更弱的模型时，继续等待当前模型；
2. 使用率达到 97% 时，自动使用一个可用的重置机会；
3. redeem 已确认失败且同一窗口使用率超过 98% 时，准备 `handoff.org`。

## 1. 算力紧张时保留当前模型

当 Codex 显示完整的 **Additional safety checks** 菜单，并同时出现
`1. Retry with a faster model` 与 `2. Keep waiting` 时，Usage Guard 自动选择
选项 `2`。这样会继续等待当前模型完成请求，不会为了更快响应而静默降低模型能力。

PTY 组件要求同一渲染视图中同时存在标题、两个带编号的选项以及确认提示。它只记录一次
选择收据，不记录 prompt 或对话正文。视图不完整、顺序不符或语义未知时不会注入任何输入；
Usage Guard 也不会处理审批菜单或其他终端提示。

## 2. 使用率达到 97% 时自动使用重置机会

生命周期 hooks 会在工具活动后及回合结束时读取结构化 Codex `token_count` 事件。当任一受
监控的使用窗口达到 97% 时，Usage Guard 使用当前 Codex 登录态自动消费一个已有的重置机会。

该动作以重置窗口作为幂等键，因此重复触发 hook 不会重复消费同一个机会。成功消费会持久化
为证据；未知或失败的结果不会被改写为成功。Usage Guard 不能制造重置机会，也不能绕过账户
额度限制。

## 3. Redeem 失败且超过 98% 时准备任务交接

只有同一重置窗口同时满足以下事实，Usage Guard 才会写入 `handoff.org`：

- 使用率严格大于 98%；
- 97% 阈值处已经尝试 redeem；
- 持久化证据确认该次 redeem 失败。

它只创建或刷新 `# usage-guard:begin` 与 `# usage-guard:end` 之间的托管区块，保留其他全部
任务笔记。使用率恰好等于 98%、redeem 成功、redeem 结果未知或托管标记损坏时，都不会覆盖
文件。这样可以在额度耗尽前保存当前任务现场，同时不会冒充任务已经完成。

## 运行边界

额度与交接路径完全由 callback 驱动。`hooks/hooks.json` 调用
`scripts/usage_guard_once.py`，后者读取结构化 `token_count` 事件，并为每个重置窗口持久化
一次决策。

Codex 没有把 **Additional safety checks** 视图暴露为 plugin hook，因此
`scripts/codex_keep_waiting.py` 是安装在 `~/.local/bin/codex` 的独立 PTY 组件。只有同一
渲染视图包含以下四个标记时，它才会注入字符 `2`：

- `Additional safety checks`
- `1. Retry with a faster model`
- `2. Keep waiting`（或 `2. Dismiss and keep waiting`）
- `Press enter to confirm or esc to go back`

它不使用坐标、截图、OCR、鼠标事件或通用审批匹配。未知或已经变化的 UI 会 fail closed，
不会收到任何自动输入。

## 安装

```bash
python3 scripts/install.py
```

安装器会原子部署 PTY 组件，在
`${CODEX_HOME:-~/.codex}/usage-guard/config.json` 中启用 97%/98% 策略，迁移旧 Keep
Waiting 收据；除非显式传入 `--force`，否则不会替换无关的 `codex` 启动器。

## 验证

```bash
python3 scripts/verify.py --full
```

验证内容包括：

- `usage-guard` 的规范 marketplace 条目；
- 已启用的 `usage-guard@codex-public` 安装版本与源码 manifest 一致；
- redeem 与 handoff 策略已启用；
- 已部署包装器的摘要以及真实 Codex 版本透传；
- quota、redeem 与 handoff 测试；
- 精确 PTY 回放、不完整视图拒绝以及重复渲染去重。

## 状态与证据

```text
${CODEX_HOME:-~/.codex}/usage-guard/config.json
${CODEX_HOME:-~/.codex}/usage-guard/state.json
${CODEX_HOME:-~/.codex}/usage-guard/usage_guard.log.jsonl
${CODEX_HOME:-~/.codex}/usage-guard/keep_waiting.log.jsonl
${CODEX_HOME:-~/.codex}/usage-guard/keep_waiting_install.json
```

Keep Waiting 收据不包含 prompt 或对话正文。Usage Guard 不会绕过额度限制，只会使用当前
登录账户中已经存在的重置机会。
