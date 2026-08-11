# Codex OpenCode Go Subagent

让 Codex 主任务继续使用原来的 OpenAI 模型和 ChatGPT 登录，只把 OpenCode Go
套餐中的 `deepseek-v4-flash` 注册为原生、面向只读任务的 `v4_flash_worker` 子 Agent。

OpenCode Go 目前为该模型提供的是
`https://opencode.ai/zen/go/v1/chat/completions`，而当前 Codex custom provider
使用 Responses 协议。本仓库因此提供一个只绑定 localhost 的小型
Responses→Chat bridge，并复用经过并发/一次性交付测试的 plaintext
`SubagentStart` Hook。

## 边界

- 主 Agent 的顶层 `model`、`model_provider`、`config.toml` 和 ChatGPT 登录不变。
- child 固定为 `deepseek-v4-flash`，不会 fallback 到其他模型。
- child 只接受文本任务，并由 developer policy 拒绝写入；当前 Codex 会在加载角色后
  重新应用 parent 的实时 permission profile，因此角色 TOML 不能独立强制降为只读。
- 若需要操作系统级强制只读，请从 `read-only` parent 任务中 spawn；不要把 child
  的行为约束误当成 sandbox 证据。
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

- `~/.codex/agents/v4-flash-worker.toml`
- `~/.codex/skills/use-v4-flash-worker/`
- `~/.codex/hooks/codex-opencode-go-subagent/plaintext_handoff.py`
- `~/.codex/opencode-go-subagent/runtime/`
- `~/.codex/opencode-go-subagent/bin/codex-opencode-go-bridge`
- `~/.codex/opencode-go-subagent/bin/codex-opencode-go-service`
- 精确匹配 `^v4_flash_worker$` 的 `SubagentStart` Hook
- `~/.codex/AGENTS.md` 中的受管路由块

已有 Hook 和 `AGENTS.md` 内容会被保留；重复安装是幂等的。安装器不会伪造
Hook trust hash。

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

provider 通过受管 service 命令只读取本地 bearer；上游 key 不会返回给 Codex。
每次 `configure` 也会轮换本地 bearer，避免继承旧的 GUI-wide token。

## Linux 手动启动

Linux 尚未提供受测的 Secret Service/systemd 用户服务集成，暂时在可信 shell 或
secret manager 中准备两个不同的值后显式启动：

```bash
export OPENCODE_GO_API_KEY="..."
export CODEX_OPENCODE_BRIDGE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
~/.codex/opencode-go-subagent/bin/codex-opencode-go-bridge
```

## 信任 Hook 与 smoke

1. 在 Codex 输入 `/hooks`。
2. 核对 matcher 仅为 `^v4_flash_worker$`，命令指向已安装的
   `plaintext_handoff.py --mode hook`，然后手动信任。
3. 新建 Codex 任务。
4. 明确接受一次很小的 OpenCode Go 调用后，执行
   [prompts/quick-smoke-test.md](prompts/quick-smoke-test.md)。

只有 native child、marker、无写入工具调用、callback、provider/model 路由全部可核验，
才能把状态称为 `ready`。本地测试通过但没有 live smoke 时只能称为
`locally_verified`。

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
- 没有图像输入、模型 fallback、写工作区或多模型路由。
- Codex 当前会让 native child 继承 parent 的运行时权限。仓库能约束任务路由和
  worker 行为，但不能从 agent TOML 独立收紧 sandbox；这是上游 spawn 配置顺序的限制。
- OpenCode Go 的模型列表、限额、价格和数据保留会变化，使用前以
  [官方 Go 文档](https://dev.opencode.ai/docs/go/) 为准。

## License

Apache-2.0。第三方来源与修改说明见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
