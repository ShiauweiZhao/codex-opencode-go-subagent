# Codex × OpenCode Go `deepseek-v4-flash`：开发基础选型

研究日期：2026-08-11

## 结论

**建议新建一个独立仓库，不直接 fork 任何一个现有仓库作为最终产品。**

新仓库采用两部分已经被分别验证过的设计：

1. **控制面以 `Utopia-V/codex-deepseek-subagent` 为蓝本**：保留“主 Agent 仍走 OpenAI / ChatGPT 登录、provider 只写在 standalone child agent TOML、`fork_turns="none"`、一次性 `SubagentStart` plaintext handoff、no-write 行为约束、skill 路由、安装合同和本地协议测试”。
2. **传输面从 `goldtetsola/opencode-bridge` 提取最小必要代码**：只保留 Codex Responses API 到 OpenCode Go Chat Completions 的协议转换、SSE、function tool call / tool result、`previous_response_id` 状态和健康检查；删除 MissionV1、patch escrow、event log、fallback model、GPT passthrough、certification/canary 等与本需求无关的体系。

不建议把 `oil-oil` 作为基础；也不建议直接采用完整 `opencode-bridge`。原因不是它们不可用，而是二者的变更半径分别过大、运行面过重。

### Live smoke 后的权限修正

真实 native child 已证明 provider/model 路由成功，但也暴露了一个必须写清的上游边界：
当前 Codex 的 spawn 顺序是先加载 custom role，随后再次执行
`apply_spawn_agent_runtime_overrides()`，把 parent turn 或 environment 的实时
permission profile 写回 child。因此 agent TOML 中的 `sandbox_mode = "read-only"`
会被覆盖，不能作为强制隔离。

相关实现可直接核对 Codex 的
[`multi_agents/spawn.rs`](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/multi_agents/spawn.rs)
与
[`multi_agents_common.rs`](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/multi_agents_common.rs)。
本仓库因此移除该误导字段，把首版边界改为：worker developer policy 明确拒绝写入，
parent 负责验证；需要操作系统级写拒绝时，必须从 read-only parent 任务中 spawn。

## 为什么必须有协议适配

OpenCode Go 官方当前把 `deepseek-v4-flash` 暴露为：

- 模型 ID：`deepseek-v4-flash`
- 端点：`https://opencode.ai/zen/go/v1/chat/completions`
- 协议包：`@ai-sdk/openai-compatible`

同一张官方表中，Responses 模型会明确指向 `/v1/responses`；`deepseek-v4-flash` 并非这一类。[OpenCode Go endpoints](https://dev.opencode.ai/docs/go/#endpoints)

Codex 官方允许 custom model provider 定义 `base_url`、wire API 和认证。
[Codex Advanced Configuration](https://developers.openai.com/codex/config-advanced/#custom-model-providers)
但 Codex 上游已经宣布移除旧 `chat/completions` wire，并要求 custom provider 迁移到
Responses；移除窗口为 2026 年 2 月。
[Codex chat/completions deprecation](https://github.com/openai/codex/discussions/7782)

因此当前主链只有一种：**child 始终对 localhost bridge 使用 Responses，bridge 再对
OpenCode Go 使用 Chat Completions**。多出的本地进程换来了明确、可测试的协议边界；
直接 `wire_api="chat"` 不作为当前实现、开发探针或运行时 fallback。

## 三个上游的比较

| 维度 | `oil-oil/codex-deepseek-subagent` | `Utopia-V/codex-deepseek-subagent` | `goldtetsola/opencode-bridge` |
|---|---|---|---|
| 原始目标 | DeepSeek 官方 Responses API 的原生 Codex child 管理器 | OpenAI parent + DeepSeek child 的轻量配置和可靠任务交付 | 把 OpenCode Go Chat Completions 包装成 Responses，并增加完整 OSS runtime 治理 |
| 主模型保持不变 | 是，但会修改顶层 provider block、模型目录和 multi-agent 开关 | 是；provider 仅在 standalone child TOML | 是；README 明确禁止把 bridge 设为顶层 provider |
| 任务载体 | 强制整个 session 使用 multi-agent V1 明文路径 | V2 + one-shot `SubagentStart` plaintext Hook | 主要依赖 handoff prompt / MissionV1；没有解决 OpenAI parent 生成跨 provider ciphertext 的同一控制面问题 |
| OpenCode Go Flash 协议 | 不匹配：硬编码 DeepSeek 官方 `/responses` 和官方模型目录 | 不匹配：agent TOML 硬编码 DeepSeek 官方 `/responses` | 匹配：本地 `/v1/responses` 转 OpenCode Go `/chat/completions` |
| 安装/回滚 | 完整 manager，事务备份、凭据库、status/test/repair/disable/uninstall | 安装合同 + 幂等文件合并；人工审查 Hook trust | CLI install/up/down/doctor/smoke，且需要本地 daemon |
| 测试资产 | 约 30 个 manager 单测，覆盖事务、配置、路由证据 | POSIX 27 个 handoff 协议测试，另有 Windows 并发/恢复测试和 smoke 合同 | 51 个测试文件、约 6.1 万行顶层源码/测试；能力强但明显超出本需求 |
| 运行依赖 | Python 3.11+；无 daemon；macOS Keychain / Windows Credential Manager | macOS/Linux Python 3；Windows PowerShell；无 daemon | Python 本地 HTTP daemon、SQLite state、supervisor、项目 `.codex-oss` 工件 |
| 许可证 | MIT | MIT | Apache-2.0 |
| 主要风险 | 改顶层 `config.toml`、自定义 model catalog、关闭 V2、父模型变化后 repair；对当前目标还要重写协议层 | 控制面最窄；但 provider/model/hook matcher/smoke oracle 全部绑定原 DeepSeek，不能只换 URL | 生产能力最完整，但 7,000+ 行单体 bridge 和 MissionV1/runtime/certification 带来较大运维与维护成本 |

### 1. `oil-oil`：管理器很强，但不是合适的基础

它的优点是安装体验完整：事务备份、原子替换、系统凭据、冲突检测、直连测试、原生 spawn 后查询 Codex state DB 证明实际 provider/model 路由。这些验收思想值得借用。[README](https://github.com/oil-oil/codex-deepseek-subagent/blob/4641ace5233366c2885aa95fdd321b4935eb0617/README.md) [manager source](https://github.com/oil-oil/codex-deepseek-subagent/blob/4641ace5233366c2885aa95fdd321b4935eb0617/codex-deepseek-subagent/scripts/codex_deepseek.py) [tests](https://github.com/oil-oil/codex-deepseek-subagent/blob/4641ace5233366c2885aa95fdd321b4935eb0617/scripts/test_manager.py)

但是它把 `https://api.deepseek.com/`、`wire_api="responses"`、DeepSeek 官方模型目录下载、顶层 provider block、自定义模型目录、`features.multi_agent_v2=false` 和父模型 `multi_agent_version=v1` 组合成一个整体。虽然“不切换顶层主模型”，它仍修改主 session 的全局 multi-agent 行为和 provider/catalog 配置；父模型改变后还需 `repair`。[compatibility](https://github.com/oil-oil/codex-deepseek-subagent/blob/4641ace5233366c2885aa95fdd321b4935eb0617/codex-deepseek-subagent/references/compatibility.md)

这与“OpenCode Go 只影响一个 Flash child，主模型和主 provider 完全不动”的目标不够贴合。把它改造成 OpenCode Go 版本，等于需要替换它最核心的 provider/catalog/V1 策略，剩下的主要价值只是 manager 外壳。

### 2. `Utopia-V`：最适合作为控制面蓝本

它把 provider 定义放在独立 `v4_flash_worker.toml` 内，不向顶层 `config.toml` 增加 DeepSeek provider，也不切换 parent；这与 Codex 官方的 custom agent 配置层模型一致。Codex 官方说明 standalone custom agent 可以覆盖普通 session 支持的 model/provider 等设置，且 agent 文件中的 model 优先于 parent。[Codex Subagents](https://developers.openai.com/codex/subagents/#custom-agents)

它还正面处理了 OpenAI parent → 非 OpenAI child 的任务正文可能落入 provider-internal ciphertext 的问题：父 Agent 先把完整 assignment 写入一次性本地状态，精确匹配 `^v4_flash_worker$` 的受信 `SubagentStart` Hook 原子 claim，再作为 developer context 注入；child 仍使用 Codex 原生 spawn、wait/callback。[advanced design](https://github.com/Utopia-V/codex-deepseek-subagent/blob/1377b7655ea98ed50a5131172b579b56ed744793/docs/advanced.md) [POSIX hook](https://github.com/Utopia-V/codex-deepseek-subagent/blob/1377b7655ea98ed50a5131172b579b56ed744793/hooks/plaintext_handoff.py) [27-test POSIX suite](https://github.com/Utopia-V/codex-deepseek-subagent/blob/1377b7655ea98ed50a5131172b579b56ed744793/tests/test_plaintext_handoff.py)

本机此前安装同一提交并完成过 27 项无付费本地协议测试，但没有进行 live provider/child smoke；因此它能证明 handoff 实现与本机配置等价，不能替代 OpenCode Go 实时兼容验收。

不能直接 fork 后只改 `base_url`：上游自己明确要求新的 provider/model 必须联动修改 agent、认证、Hook matcher、skill、AGENTS 路由和 smoke oracle，并重新验证 native spawn、任务交付、工具、callback 与 cancel。[provider adaptation section](https://github.com/Utopia-V/codex-deepseek-subagent/blob/1377b7655ea98ed50a5131172b579b56ed744793/docs/advanced.md#适配其他-providermodel)

### 3. `opencode-bridge`：传输层来源正确，但完整采用太重

它正好实现本需求缺少的协议层：接收 Codex `/v1/responses`，转换 messages 与 function tools，调用 OpenCode Go `/chat/completions`，把普通/流式回复和 tool calls 转回 Responses 事件，保存 `previous_response_id` 相关状态，并映射 `ocg-deepseek-v4-flash` 到 `deepseek-v4-flash`。[bridge source](https://github.com/goldtetsola/opencode-bridge/blob/61c79e5c18a04448f02a472ea8734fd0a134c0fb/bridge.py) [Flash agent](https://github.com/goldtetsola/opencode-bridge/blob/61c79e5c18a04448f02a472ea8734fd0a134c0fb/agents/oss-flash-support.toml) [Codex provider example](https://github.com/goldtetsola/opencode-bridge/blob/61c79e5c18a04448f02a472ea8734fd0a134c0fb/config.toml.example)

但该仓库的定位已经远超协议 bridge：MissionV1 A2-A6、runtime-owned tools、append-only event log、RunRecord、patch escrow、isolated worktree、review packet、certification、canary、scorecard、fallback 和 daemon supervisor 都是其产品的一部分。[README](https://github.com/goldtetsola/opencode-bridge/blob/61c79e5c18a04448f02a472ea8734fd0a134c0fb/README.md) [architecture](https://github.com/goldtetsola/opencode-bridge/blob/61c79e5c18a04448f02a472ea8734fd0a134c0fb/architecture.md)

如果目标只是一个低成本、面向只读任务的 Flash 子 Agent，完整引入会让日常故障域从“agent + Hook + API”扩大为“agent + Hook + daemon + SQLite + runtime policy + artifacts + bridge version”。因此应移植其传输契约与相应测试，而不是继承整个 runtime。

## 推荐的最小架构

```text
Codex 主 Agent（原 OpenAI provider / ChatGPT 登录）
  |
  | stage 一次性明文 assignment
  | spawn_agent(agent_type="v4_flash_worker", fork_turns="none")
  v
SubagentStart plaintext Hook（Utopia-V 模式）
  |
  | developer context
  v
standalone child agent TOML
  model_provider = "opencode_go_bridge"
  model = "deepseek-v4-flash"
  developer policy = no-write
  runtime permissions = inherited from parent
  |
  | OpenAI Responses API
  v
127.0.0.1 上的最小 bridge
  |
  | OpenAI-compatible Chat Completions
  v
https://opencode.ai/zen/go/v1/chat/completions
```

### 新仓库只保留这些模块

- `agents/opencode-go-flash-worker.toml`
- `skills/use-opencode-go-flash-worker/`
- `hooks/plaintext_handoff.py` 及其 POSIX 协议测试
- `bridge/`：Responses↔Chat 的纯协议适配、SSE、tool calls、tool-result continuation、短期 state、`/health`
- `scripts/install.py`：幂等安装、备份/回滚、Hook 合并、静态检查
- `scripts/service.py`：`up/down/status/doctor`；仅绑定 `127.0.0.1`
- `tests/`：handoff 协议、转换 fixtures、SSE、工具续轮、安装等价性
- `prompts/quick-smoke-test.md`：一次最小付费 native child 测试

明确不做：修改顶层 `model` / `model_provider`、自定义全局 model catalog、关闭整个 session 的 V2、fallback 到其他模型、GPT passthrough、MCP、另一个 Codex CLI、MissionV1、自动写工作区，或虚假宣称 child role 能独立强制只读。

### 凭据边界

- 上游凭据只使用 `OPENCODE_GO_API_KEY`，由 bridge 进程读取；不得进入 agent TOML、prompt、handoff state 或日志。
- Codex → 本地 bridge 使用独立的本地 bearer token；不可复用上游 key。
- plaintext Hook 只承载 assignment，不承载任何凭据。
- OpenCode Go 官方说明该 child 的上下文与工具结果会进入相应外部模型数据边界；官方当前把 DeepSeek V4 Flash 标为“不用于训练、0 天保留”，并特别说明其 ZDR 协议按月续期，故安装器/文档应提示用户在使用时重新核验，而不是把当前状态写成永久保证。[OpenCode Go privacy](https://dev.opencode.ai/docs/go/#privacy)

## 许可证与仓库策略

- `oil-oil` 与 `Utopia-V` 均为 MIT。[oil license](https://github.com/oil-oil/codex-deepseek-subagent/blob/4641ace5233366c2885aa95fdd321b4935eb0617/LICENSE) [Utopia-V license](https://github.com/Utopia-V/codex-deepseek-subagent/blob/1377b7655ea98ed50a5131172b579b56ed744793/LICENSE)
- `opencode-bridge` 为 Apache-2.0。[bridge license](https://github.com/goldtetsola/opencode-bridge/blob/61c79e5c18a04448f02a472ea8734fd0a134c0fb/LICENSE)

如果复制或改写上游代码，建议新仓库总体采用 **Apache-2.0**，并在 `THIRD_PARTY_NOTICES.md` 中保留：

- Utopia-V 对应 MIT copyright + license；
- opencode-bridge 对应 Apache-2.0 copyright/license，并标明修改过的文件；
- 若只借鉴 oil-oil 的验收思路而不复制代码，无需把它作为代码来源；若复制 manager 片段，同样保留其 MIT notice。

这能把组合授权做清楚，也避免让新仓库的 Git 历史假装是某个上游的单一路线。

## 验收门槛

在以下证据全部通过前，只能叫“configured”，不能叫“ready”：

1. 静态证明 parent 的顶层 model/provider/auth 文件未被改动。
2. plaintext handoff 的 collision、原子发布、精确 role、一次消费、replay 拒绝、TTL/损坏恢复、并发 at-most-once 测试通过。
3. bridge fixtures 证明 Responses input、function tools、tool call delta、tool result continuation、SSE terminal event 和 usage 转换。
4. bridge `doctor` 证明请求实际映射到 `deepseek-v4-flash`，且 GPT-family 请求 fail closed。
5. 一次显式授权的小额 live smoke：OpenAI parent 原生 spawn 指定 child，child 收到随机 marker、完成一个无写入工具调用并通过 native callback 返回；同时回查 child thread 的 provider/model 与实际 permission profile 元数据。
6. cancel、超时、bridge 重启后的状态丢失均明确失败，不静默换模型、直连 API 或继承 parent 历史。

## 最终决策

**建新仓库。** 可以把它理解为“Utopia-V control plane + 精简 opencode-bridge transport”，而不是第三个大而全的 agent runtime。

开发顺序建议：先做单模型、behaviorally no-write、macOS/POSIX 主链并跑 live smoke；再补 Windows；最后才考虑写任务或更多 OpenCode Go 模型。首版不要加入 runtime fallback，失败应明确暴露在 assignment、bridge、provider 或 callback 的具体边界。

## 上游快照

- `oil-oil/codex-deepseek-subagent`：[`4641ace`](https://github.com/oil-oil/codex-deepseek-subagent/commit/4641ace5233366c2885aa95fdd321b4935eb0617)，2026-08-05。
- `Utopia-V/codex-deepseek-subagent`：[`1377b76`](https://github.com/Utopia-V/codex-deepseek-subagent/commit/1377b7655ea98ed50a5131172b579b56ed744793)，2026-08-08。
- `goldtetsola/opencode-bridge`：[`61c79e5`](https://github.com/goldtetsola/opencode-bridge/commit/61c79e5c18a04448f02a472ea8734fd0a134c0fb)，2026-06-06。
