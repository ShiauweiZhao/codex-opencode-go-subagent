# Security

## Credentials

- `OPENCODE_GO_API_KEY` 只由本地 bridge 进程读取，用于访问 OpenCode Go。
- `CODEX_OPENCODE_BRIDGE_TOKEN` 只用于 Codex → `127.0.0.1` bridge 的本地鉴权。
- 两者必须不同。bridge 会拒绝复用同一个值。
- 不要把任一凭据放入 agent TOML、prompt、Hook assignment、仓库、Issue、日志或截图。
- 默认上游 URL 必须为 HTTPS；只有 loopback 测试地址允许 HTTP。

## Local data

Responses tool continuation state 默认保存在：

```text
~/.codex/opencode-go-subagent/state.sqlite3
```

目录权限为 `0700`、数据库权限为 `0600`。其中可能包含任务文本、模型回复和工具结果，
不应把它当作无敏感信息缓存。卸载器不会自动删除运行 state，避免误删仍需审计的数据；
确认不再需要后可由用户显式删除。

plaintext Hook 的 pending assignment 也会短暂保存在当前用户 state 目录中，并在一次
成功交付后消费。它解决跨 provider 任务正文交付问题，不是机密通道。

## Network boundary

- bridge 只允许绑定 `127.0.0.1`、`::1` 或 `localhost`。
- GPT-family 或其他模型 ID 会 fail closed，不会转发给 OpenCode Go。
- 不存在运行时 fallback。
- `/healthz` 与 `/v1/models` 不调用上游，也不返回凭据；`/v1/responses` 必须携带本地 bearer。

## External data boundary

发送给 child 的 assignment、可见源码片段和工具结果会离开本机，进入 OpenCode Go 和
其 DeepSeek 服务链路。隐私、训练和保留政策可能变化，使用时应重新查看
[OpenCode Go privacy](https://dev.opencode.ai/docs/go/#privacy)。

## Reporting

请通过 GitHub Security Advisory 私下报告可能导致凭据泄漏、localhost 鉴权绕过、
任意模型转发或未经授权写入的问题；不要在公开 Issue 中附带真实凭据或任务数据。
