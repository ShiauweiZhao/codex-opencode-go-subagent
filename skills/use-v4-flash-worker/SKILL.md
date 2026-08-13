---
name: use-v4-flash-worker
description: Use the OpenCode Go backed v4_flash_worker through the installed one-shot plaintext SubagentStart Hook. Use whenever Codex considers spawning, continuing, or troubleshooting this worker; it governs task suitability, staging, fork_turns=none spawning, return, and the external provider data boundary.
---

# Use V4 Flash Worker

## Choose the worker

- Code implementation defaults to `v4_flash_worker`: features, bug fixes,
  refactors, tests, code-related documentation, and cross-module wiring after
  the parent has resolved interfaces and behavior. Multi-file or cross-module
  complexity is not by itself a reason to refuse V4.
- Keep requirements clarification, analysis, audit, assessment, design,
  planning, architecture, interface and behavior decisions, task decomposition,
  integration-point mapping, test-gap discovery, consequential judgment, code
  review, final verification, and final validation on the preselected GPT
  parent. The parent may spawn `gpt_review_worker`, which inherits that GPT
  model and stays read-only.
- The GPT parent resolves design ambiguity first and decomposes the work into
  one or more bounded coding assignments; V4 then implements each batch.
  Never send an analysis or audit assignment to V4. A read-only task is not
  automatically a V4 job: if it asks what should change, why, what is missing,
  or what risks exist, it belongs to GPT.
- Assign coding only with an explicit writable scope and concrete validation
  commands. Start multiple V4 workers in parallel when the work splits into
  independent, non-conflicting, dependency-free writable scopes; execute
  sequentially only when batches share dependencies or edit the same files.
- Keep consequential decisions, final diff review, independent verification,
  Git operations, and integration in the parent. The worker never commits,
  pushes, creates pull requests, or mutates external systems.
- The worker returns `ESCALATE_TO_GPT` only for unresolved design choices,
  required scope expansion, safety or consequential judgment, a missing writable
  scope or validation oracle, or sandbox/approval boundaries. After the parent
  resolves the boundary, hand the remaining implementation back to V4 where
  feasible.
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
2. Build one complete assignment containing child identity, task mode (`coding` or `extraction`), objective, scope, exclusions, inherited permission
   caveat, evidence/output contract, and stopping condition. A coding assignment
   must also name its explicit writable scope and validation commands. Never
   imply that the worker may modify the whole repository by default.
3. On macOS managed installations, pipe the assignment through stdin to:

   ```text
   "$CODEX_HOME/opencode-go-subagent/bin/codex-opencode-go-service" stage-handoff
   ```

   If `CODEX_HOME` is unset, use `~/.codex`. This sends the assignment only to
   the authenticated localhost managed service; the LaunchAgent performs the
   state transition, so the parent task does not write the handoff state
   directory. On Linux manual installations, use the installed
   `plaintext_handoff.py --mode stage` path directly. Never spawn after a failed
   stage. Do not fall back to direct filesystem staging when managed staging
   fails; report the exact service boundary instead.
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
- An assignment labeled analysis, audit, assessment, design, planning,
  integration mapping, or test-gap discovery is not a V4 job even when it is
  read-only. Keep it on the preselected GPT parent.
- If an assignment still needs a design choice, scope expansion, safety or
  consequential judgment, or lacks a writable scope or validation oracle, stop
  with `ESCALATE_TO_GPT`; do not expand the assignment or continue coding.
- Reject coding evidence that relies only on callback claims: writes must be
  verifiable from the rollout JSONL and the bridge SQLite response chain through
  the structured `apply_patch` tool, never from model-constructed `exec_command`
  writes, heredocs, redirection, or script writes.
- If an action would cross the active sandbox or approval boundary, stop with
  `ESCALATE_TO_GPT`. Auto-review is an approval reviewer, not this project's GPT
  code-review route; never send its model request through the V4 bridge.
- The assignment briefly exists as plaintext in local user state and is then
  sent to OpenCode Go / DeepSeek. The Hook is not a confidential channel.
