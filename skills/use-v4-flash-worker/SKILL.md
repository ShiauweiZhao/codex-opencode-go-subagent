---
name: use-v4-flash-worker
description: Use the OpenCode Go backed v4_flash_worker through the installed one-shot plaintext SubagentStart Hook. Use whenever Codex considers spawning, continuing, or troubleshooting this worker; it governs task suitability, staging, fork_turns=none spawning, return, and the external provider data boundary.
---

# Use V4 Flash Worker

## Choose the worker

- Use it for bounded, preferably read-only text, code, log, search, extraction,
  enumeration, or high-volume reading work.
- Keep consequential decisions, verification, writes, and final integration in
  the parent. This worker is text-only.
- Native children currently inherit the parent turn's runtime permission
  profile after role loading. Treat the worker's no-write rule as a behavioral
  contract, not an independently enforced sandbox. Use a read-only parent when
  OS-level write denial is required.
- Do not send secrets, private source, personal data, or regulated material
  unless the user has authorized the OpenCode Go and DeepSeek data boundary.
- Do not switch the parent model, provider, or ChatGPT login.
- Never put `OPENCODE_GO_API_KEY` or `CODEX_OPENCODE_BRIDGE_TOKEN` in a prompt,
  staged assignment, command argument, log, screenshot, or repository.

## Deliver one self-contained job

1. Confirm `http://127.0.0.1:4141/healthz` is healthy. On macOS, use the
   installed `codex-opencode-go-service doctor` if it is not. Do not call the
   paid upstream merely to test health or start a separate foreground bridge.
2. Build one complete assignment containing child identity, objective, scope,
   exclusions, inherited permission caveat, explicit no-write rule,
   evidence/output contract, and stopping condition.
3. Pipe the assignment through stdin to:

   ```text
   python3 "$CODEX_HOME/hooks/codex-opencode-go-subagent/plaintext_handoff.py" --mode stage
   ```

   If `CODEX_HOME` is unset, use `~/.codex`. Never spawn after a failed stage.
4. Immediately call native `spawn_agent` with exact agent type
   `v4_flash_worker`, a unique task name, and `fork_turns="none"`. The spawn
   message should only identify the trusted one-shot Hook.
5. Receive the child through the native callback/wait path. Do not replace it
   with OpenCode CLI, MCP, direct API requests, or inherited root turns.
6. Verify the contribution in proportion to the parent claim, then integrate it.

## Fail safely

- Handoff is one-shot and at-most-once. Resolve stale or quarantined state
  explicitly; do not overwrite it automatically.
- A missing assignment, unhealthy bridge, failed stage, absent callback, or
  provider error is a visible failure. Never silently fall back to another
  model/provider.
- The assignment briefly exists as plaintext in local user state and is then
  sent to OpenCode Go / DeepSeek. The Hook is not a confidential channel.
