---
name: use-v4-flash-worker
description: Use the OpenCode Go backed v4_flash_worker through the installed one-shot plaintext SubagentStart Hook. Use whenever Codex considers spawning, continuing, or troubleshooting this worker; it governs task suitability, staging, fork_turns=none spawning, return, and the external provider data boundary.
---

# Use V4 Flash Worker

## Choose the worker

- Use it only for simple, bounded, mechanically verifiable coding, repository
  lookup, text, code, log, search, extraction, enumeration, or high-volume
  reading work.
- Keep planning, architecture, ambiguous or cross-cutting implementation,
  consequential judgment, code review, and final validation on the preselected
  GPT parent. The parent may spawn `gpt_review_worker`, which inherits that GPT
  model and stays read-only.
- Analysis and review are non-mutating by default. Assign coding only with an
  explicit writable scope and concrete validation commands.
- Keep consequential decisions, final diff review, independent verification,
  Git operations, and integration in the parent.
- Native children currently inherit the parent turn's runtime permission
  profile after role loading. Permission to write is bounded by both that
  profile and the assignment; neither one expands the other.
- Do not send secrets, private source, personal data, or regulated material
  unless the user has authorized the OpenCode Go and DeepSeek data boundary.
- Do not switch the parent model, provider, or ChatGPT login.
- Never put `OPENCODE_GO_API_KEY` or `CODEX_OPENCODE_BRIDGE_TOKEN` in a prompt,
  staged assignment, command argument, log, screenshot, or repository.

## Deliver one self-contained job

1. Confirm `http://127.0.0.1:4141/healthz` is healthy. On macOS, use the
   installed `codex-opencode-go-service doctor` if it is not. Do not call the
   paid upstream merely to test health or start a separate foreground bridge.
2. Build one complete assignment containing child identity, task mode
   (`analysis` or `coding`), objective, scope, exclusions, inherited permission
   caveat, evidence/output contract, and stopping condition. A coding assignment
   must also name its explicit writable scope and validation commands. Never
   imply that the worker may modify the whole repository by default.
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
6. For coding work, inspect the exact diff and out-of-scope paths, then run the
   final validation independently. The worker must not commit, push, create a
   pull request, or mutate external systems. The parent decides whether to keep
   and integrate the changes.

## Audit the coding oracle

- The GPT parent must audit parent-visible child rollout JSONL selected by the
  marker and `v4_flash_worker` session metadata, plus the bridge SQLite response
  chain selected by the same marker. Callback text is not authoritative.
- Rollout JSONL is under `$CODEX_HOME/sessions` (or `~/.codex/sessions`);
  bridge state is `$CODEX_HOME/opencode-go-subagent/state.sqlite3`. Never print
  unrelated prompt/state content or secrets.
- Require bridge `tool_types.apply_patch` equal to `safe_exec_apply_patch` and
  upstream structured apply_patch evidence with `{patch, workdir}`.
- Require the mapped `exec_command` to be canonical shell quoting whose parsed
  argv is exactly `[apply_patch, original_patch]`. Reject direct
  model-generated `exec_command` writes, heredocs, redirection, script writes,
  and approval requests.
- Fact scope: the Codex 0.147 feature list marks `apply_patch_freeform` as
  removed and the observed 0.147 V4 custom-child request did not expose a custom
  apply_patch tool. Do not generalize to other children or future versions.

## Fail safely

- Handoff is one-shot and at-most-once. Resolve stale or quarantined state
  explicitly; do not overwrite it automatically.
- A missing assignment, unhealthy bridge, failed stage, absent callback, or
  provider error is a visible failure. Never silently fall back to another
  model/provider.
- A coding assignment without a writable scope or validation commands is
  incomplete. Report the missing contract instead of guessing or writing.
- If a simple assignment reveals ambiguity, broad impact, architectural choices,
  or security-sensitive judgment, stop with `ESCALATE_TO_GPT`; do not expand the
  assignment or continue coding.
- Reject coding evidence that relies only on callback claims: writes must be
  verifiable from the rollout JSONL and the bridge SQLite response chain through
  the structured `apply_patch` tool, never from model-constructed `exec_command`
  writes, heredocs, redirection, or script writes.
- If an action would cross the active sandbox or approval boundary, stop with
  `ESCALATE_TO_GPT`. Auto-review is an approval reviewer, not this project's GPT
  code-review route; never send its model request through the V4 bridge.
- The assignment briefly exists as plaintext in local user state and is then
  sent to OpenCode Go / DeepSeek. The Hook is not a confidential channel.
