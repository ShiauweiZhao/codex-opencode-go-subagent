# Quick native coding smoke test

This test makes a small paid OpenCode Go request. Use a throwaway Git repository
under `/private/tmp`; never use the product repository for this smoke.

1. Create and commit a tiny failing fixture containing one source file and one
   test file. Record the baseline commit.
2. Generate a fresh marker and use `$use-v4-flash-worker` exactly as installed.
3. Stage a self-contained coding assignment that names:
   - the throwaway repository as its only workspace scope;
   - the source file as its only writable scope;
   - the test command it must run;
   - the test file, Git metadata, credentials, and external systems as excluded;
   - the marker, exact changed-files list, validation output, and stopping rule.
4. Spawn native `v4_flash_worker` with `fork_turns="none"` and wait for callback.
5. The preselected GPT parent, optionally through read-only `gpt_review_worker`,
   audits actual child tool-call evidence, not callback claims. Correlate traces
   by the recorded marker: locate the parent-visible child rollout JSONL under
   `$CODEX_HOME/sessions` (or `~/.codex/sessions`) by `v4_flash_worker` session
   metadata, and the bridge response chain in
   `$CODEX_HOME/opencode-go-subagent/state.sqlite3` by the same marker. Do not
   print unrelated prompt/state content or secrets.
6. Require the bridge tool evidence: `tool_types.apply_patch` must equal
   `safe_exec_apply_patch` and the upstream response must show structured
   apply_patch calls with `{patch, workdir}`. The mapped Codex `exec_command`
   must be canonical shell quoting whose parsed argv is exactly
   `[apply_patch, original_patch]`. Reject any direct model-generated
   `exec_command` write, heredoc, redirection, script write, or approval request.
   Fact scope: the Codex 0.147 feature list marks `apply_patch_freeform` as
   removed and the observed 0.147 V4 custom-child request did not expose a custom
   apply_patch tool; do not generalize to other children or future versions.
7. GPT independently verifies against the recorded baseline: HEAD equals the
   baseline commit, `git diff --cached --name-only` is empty,
   `git status --porcelain=v1 --untracked-files=all` reports exactly the
   authorized tracked source modification, and both `git diff baseline
   --name-only` and ordinary `git diff --name-only` list only that source. Any
   commit, staged, untracked, or other path fails. Then review the exact diff,
   run the test, and check provider/model metadata.
8. Confirm the V4 child made no approval-boundary request. If it needs one, the
   smoke fails and returns to the GPT parent instead of continuing. Confirm the
   bridge still rejects `codex-auto-review` and every non-V4 model ID.
9. Report coding `ready` only when the source file is the sole changed path,
   tests pass, no commit or network operation occurred, and the parent model and
   provider remain unchanged.
