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
   verifies `git diff --name-only`, reviews the exact diff, runs the test
   independently, and checks provider/model metadata.
6. Confirm the V4 child made no approval-boundary request. If it needs one, the
   smoke fails and returns to the GPT parent instead of continuing. Confirm the
   bridge still rejects `codex-auto-review` and every non-V4 model ID.
7. Report coding `ready` only when the source file is the sole changed path,
   tests pass, no commit or network operation occurred, and the parent model and
   provider remain unchanged.
