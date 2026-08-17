# Codex OpenCode Go Subagent

让 Codex 主任务继续使用用户预选的 GPT 模型和 ChatGPT 登录，把 OpenCode Go
套餐中的 `deepseek-v4-flash` 注册为默认负责代码实现（default code
implementation）的 `opencode_go_v4_worker` 子 Agent。需求、分析、设计、架构、拆解、
审查、最终验证、集成和 Git 由 GPT 负责。

OpenCode Go 目前为该模型提供的是
`https://opencode.ai/zen/go/v1/chat/completions`，而当前 Codex custom provider
使用 Responses 协议。本仓库因此提供一个只绑定 localhost 的小型
Responses→Chat bridge，并复用经过并发/一次性交付测试的 plaintext
`SubagentStart` Hook。

## 边界

- 主 Agent 的顶层 `model`、`model_provider`、`config.toml` 和 ChatGPT 登录不变，
  安装器也不读取或改写主模型设置。
- child 固定为 `deepseek-v4-flash`，不会 fallback 到其他模型。
- 部署身份固定为 `opencode_go_v4_worker`（agent TOML、skill、Hook matcher 与
  staged handoff 目标一致），以便与已安装的直接 DeepSeek `v4_flash_worker`
  共存：两者不共享 agent/skill/Hook 路径或 matcher，安装器也不会改写对方的
  文件、Hook 条目或 `AGENTS.md` 受管块。
- V4 Flash 默认负责代码实现：功能、修复、重构、测试、代码相关文档，以及 GPT
  已解析接口/行为后的跨模块接线；不需要推理判断的逐项检索、提取和枚举也可以
  交给 V4。复杂、多文件、跨模块本身不是拒绝 V4 的理由；GPT 先消除歧义并拆成
  有界任务，再让 V4 实现。需求、分析、设计、架构、拆解、审查、最终验证、集成和
  Git 由主 Agent 当前预选的 GPT 负责；`gpt_review_worker` 只读并继承该模型。
- 凡是要回答“为什么、缺什么、应该怎么接、风险是什么”的任务，或涉及架构选择、
  审查、最终验收、Git/PR、密钥/权限升级或 provider fallback 的工作，都属于 GPT。
  编码任务必须由 GPT parent 明确给出 writable scope、validation commands 和
  停止条件；child 不得超出范围，也不得 commit、push 或操作外部系统。V4 只在未
  解决设计选择、需要扩大 scope、安全/后果性判断、缺失 scope/oracle 或 approval
  boundary 时停止并返回 `ESCALATE_TO_GPT`；GPT 解决后尽可能把剩余编码重新交给 V4。
- 并发策略：当开发工作可以拆成独立（independent）、无冲突（non-conflicting）、
  无相互依赖（dependency-free）的 writable scopes 时，GPT parent 应主动并行启动
  多个 V4 workers；只有批次之间存在依赖或修改同一文件时才顺序执行。
- 当前 Codex 会在加载角色后重新应用 parent 的实时 permission profile。实际写入必须
  同时满足 parent sandbox 和 assignment 授权；角色指令本身不是独立权限边界。
- Codex Auto-review 只处理 sandbox 边界审批，不等同于代码 review。V4 任务必须保持在
  已授权 sandbox 内；需要任何提权时停止并回交 GPT，不把审批请求转发给 DeepSeek。
- 已验证 Codex 0.147 custom-child 路径中，特性列表把 `apply_patch_freeform`
  标记为移除，且观测到的 0.147 V4 custom-child request 未暴露自定义 apply_patch
  工具（仅指该已验证路径，不推广到所有 child 或未来版本）。当 Codex 暴露
  `exec_command` 而未暴露自定义 apply_patch 时，bridge 向 V4 暴露结构化
  `apply_patch` 函数（参数 `{patch, workdir}`），并以 `shlex.quote` 把该调用
  映射为 argv 恰好等于 `[apply_patch, 原始patch]` 的规范 Codex `exec_command`。
  所有授权写入必须使用该结构化工具；禁止手工构造 `exec_command` 写命令、
  heredoc、重定向以及 `cat`/`sed`/`perl`/`python` 写文件技巧，也不允许审批
  绕过。结构化 apply_patch 缺失或写入被拒绝时，V4 必须停止并返回
  `ESCALATE_TO_GPT`。
- GPT 负责需求、分析、设计、架构、拆解、审查、最终验证、集成与 Git；V4 默认负责
  明确 writable scope 内的代码实现。per-child model catalog 只提供模型元数据，不声明
  `apply_patch_tool_type`，也不启用任何原生自定义工具。
- 推理级别：模型目录中 `deepseek-v4-flash` 的 `default_reasoning_level=max`，
  `supported_reasoning_levels` 仅含 `low`/`high`/`max`。Codex Responses 的
  `reasoning.effort` 由 bridge 原样转发为上游 Chat 请求的 `reasoning_effort`
  （值不变）；请求未携带 `reasoning` 时上游不带该字段。格式错误或不支持的取值
  （如 `medium`）会显式失败，不静默降级或 fallback；native 调用方也可以显式
  设置 `reasoning_effort=max`。
- 控制面沿用 Utopia-V 的 standalone child agent、一次性 plaintext Hook 与 native
  callback 边界：上游 Utopia 仓库仅作只读参考，本仓库不随其改版自动变更；这里的
  编码只允许在明确 writable scope 与验证命令下进行，需求、分析、设计、架构、
  审查、最终验证、集成与 Git 留在预选 GPT。
- 不使用 MCP、OpenCode CLI、第二个 Codex CLI 或完整 `opencode-bridge` runtime。
- macOS 上游 API key 与独立本地 bearer 存在登录 Keychain；LaunchAgent plist、
  agent TOML、Hook 状态、进程参数和日志都不包含密钥值。
- 当前支持 macOS 托管主链与 Linux 手动主链；bridge 会缓冲一个模型回合后再输出
  Responses SSE，不提供逐 token 展示。

架构选型和上游比较见 [docs/architecture-decision.md](docs/architecture-decision.md)。

## 要求

- macOS 或 Linux
- Python 3.11+
- 当前 Codex Desktop/CLI，且支持 standalone custom agents 与 Responses provider
- OpenCode Go 订阅及其 API key

## 安装

安装本身不读取 key、不调用付费 API，也不会改
`~/.codex/config.toml` 或 `~/.codex/auth.json`：

```bash
python3 scripts/install.py install
```

它会安装：

- `~/.codex/agents/opencode-go-v4-worker.toml`
- `~/.codex/agents/gpt-review-worker.toml`
- `~/.codex/skills/use-opencode-go-v4-worker/`
- `~/.codex/hooks/codex-opencode-go-subagent/plaintext_handoff.py`
- `~/.codex/opencode-go-subagent/runtime/`
- `~/.codex/opencode-go-subagent/deepseek-v4-flash-models.json`
- `~/.codex/opencode-go-subagent/bin/codex-opencode-go-bridge`
- `~/.codex/opencode-go-subagent/bin/codex-opencode-go-service`
- 精确匹配 `^opencode_go_v4_worker$` 的 `SubagentStart` Hook
- `~/.codex/AGENTS.md` 中的受管路由块

已有 Hook 和 `AGENTS.md` 内容会被保留；重复安装是幂等的。安装器不会伪造
Hook trust hash。`gpt_review_worker` 不固定 model/provider，由主 GPT Agent spawn 时
继承用户当前预选的 GPT 模型和 OpenAI provider。

## macOS 配置托管服务

安装后执行一次：

```bash
~/.codex/opencode-go-subagent/bin/codex-opencode-go-service configure
```

命令会使用隐藏输入读取 OpenCode Go API key，生成不同的本地 bearer，把两者写入
macOS 登录 Keychain，并安装、启动按用户运行的 LaunchAgent。正常使用不需要单独打开
bridge 终端，也不需要 `launchctl setenv`。不要把真实 key 写进仓库、聊天、Issue、
命令参数或截图。

状态和无付费诊断：

```bash
~/.codex/opencode-go-subagent/bin/codex-opencode-go-service status
~/.codex/opencode-go-subagent/bin/codex-opencode-go-service doctor
curl -fsS http://127.0.0.1:4141/healthz
```

其他生命周期命令：

```bash
~/.codex/opencode-go-subagent/bin/codex-opencode-go-service start
~/.codex/opencode-go-subagent/bin/codex-opencode-go-service stop
~/.codex/opencode-go-subagent/bin/codex-opencode-go-service restart
~/.codex/opencode-go-subagent/bin/codex-opencode-go-service rotate-local-token
```

macOS 上一次性 handoff 通过受管服务暂存，assignment 从 stdin 读取，不进入命令参数：

```bash
~/.codex/opencode-go-subagent/bin/codex-opencode-go-service stage-handoff < assignment.txt
```

该命令只访问带本地 bearer 鉴权的 loopback endpoint，禁用代理和重定向，也不调用
付费上游；实际 handoff 状态由 LaunchAgent 写入，避免 workspace sandbox 直接写
`~/.codex/opencode-go-subagent/handoff-state`。stage 与 native Hook 都从已安装 Hook
脚本位置解析同一个绝对目录，不受两个进程各自的 `HOME`/`XDG_STATE_HOME` 影响。
Hook stage 子进程只继承最小运行环境，不继承上游 key 或本地 bearer。受管 staging
失败时明确停止，不静默改用直接脚本或其他 provider。

macOS 上 agent TOML 使用 command-backed provider auth：Codex 执行受管
`codex-opencode-go-service print-bridge-token` 只读取本地 bearer；上游 key 不会
返回给 Codex。每次 `configure` 也会轮换本地 bearer，避免继承旧的 GUI-wide token。

## Linux 手动启动

Linux 尚未提供受测的 Secret Service/systemd 用户服务集成。安装器在 Linux 上把
agent TOML 的 provider auth 渲染为 `env_key = "CODEX_OPENCODE_BRIDGE_TOKEN"`，
因此需要在启动 Codex 的同一可信 shell 中导出与 bridge 相同的本地 bearer，再显式
启动 bridge（两个值必须不同）：

```bash
export OPENCODE_GO_API_KEY="..."
export CODEX_OPENCODE_BRIDGE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
~/.codex/opencode-go-subagent/bin/codex-opencode-go-bridge
```

Codex 会从 `CODEX_OPENCODE_BRIDGE_TOKEN` 读取同一个本地 bearer；不要在 agent
TOML、prompt、Hook assignment、命令参数、日志或仓库中写入任一凭据值。

## 信任 Hook 与 smoke

1. 在 Codex 输入 `/hooks`。
2. 核对 matcher 仅为 `^opencode_go_v4_worker$`，命令指向已安装的
   `plaintext_handoff.py --mode hook`，然后手动信任。
3. 新建 Codex 任务。
4. 明确接受一次很小的 OpenCode Go 调用后，先执行路由检查
   [prompts/quick-smoke-test.md](prompts/quick-smoke-test.md)。
5. 需要启用编码能力时，再在一次性临时 Git 仓库执行
   [prompts/coding-smoke-test.md](prompts/coding-smoke-test.md)。

只有 native child、callback、V4 编码路由、预选 GPT reviewer 路由、限定范围写入、
独立 diff 审查和测试全部可核验，才能把编码能力称为 `ready`。本地测试通过但没有
live coding smoke 时只能称为 `locally_verified`。

## 测试

测试不需要 API key，也不会连接 OpenCode Go：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

覆盖协议转换、工具调用续轮、DeepSeek `reasoning_content` 回放、Responses SSE、
localhost 鉴权、私有 SQLite state、HTTP fake upstream、安装/卸载，以及 plaintext
handoff 的原子发布、TTL、冲突、replay 和并发 at-most-once 行为。

macOS Keychain 真机 round-trip 是显式 opt-in，使用随机临时条目并在测试后删除：

```bash
CODEX_OPENCODE_RUN_KEYCHAIN_TESTS=1 PYTHONPATH=src \
  python3 -m unittest tests.test_managed_service.RealKeychainStoreTests -v
```

## 卸载

```bash
python3 scripts/install.py uninstall
```

卸载器只删除 manifest 中哈希仍匹配的受管文件；用户修改过的文件会保留。它会移除
本仓库的 LaunchAgent、Hook 和 `AGENTS.md` 受管块，不碰主配置与登录。默认保留
Keychain 凭据，明确需要删除时才使用：

```bash
python3 scripts/install.py uninstall --purge-secrets
```

## 当前限制

- Linux 还没有 Secret Service/systemd 用户服务托管，需显式启动 bridge。
- 没有 Windows PowerShell handoff。
- 没有图像输入、fallback 或自动 worktree 隔离。
- Codex 当前会让 native child 继承 parent 的运行时权限。仓库能约束任务路由和
  worker 行为，但不能从 agent TOML 独立收紧 sandbox；这是上游 spawn 配置顺序的限制。
- 特性列表把 Codex 0.147 的 `apply_patch_freeform` 标记为移除，且观测到的
  0.147 V4 custom-child request 未暴露自定义 apply_patch；该结论只覆盖已验证
  路径，不推广到所有 child 或未来版本。V4 写入必须走 bridge 提供的结构化
  `apply_patch` 工具（`{patch, workdir}`），不能依赖 agent 级原生工具或手工
  构造写命令。
- OpenCode Go 的模型列表、限额、价格和数据保留会变化，使用前以
  [官方 Go 文档](https://dev.opencode.ai/docs/go/) 为准。

## License

Apache-2.0。第三方来源与修改说明见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
