# Explicitly authorized coding worker design

Date: 2026-08-11

## Decision

Allow `v4_flash_worker` to edit code when, and only when, the parent assignment
explicitly authorizes a coding task. Analysis and review assignments remain
non-mutating by default. This follows the lightweight conditional-write model:
the native child works in the parent's workspace and inherits the parent's
runtime permission profile. No second Codex process, implicit privilege change,
automatic worktree, or patch-escrow runtime is introduced.

The role configuration is a behavioral contract, not an independent sandbox.
If the parent runtime denies a requested write, the child reports that boundary
instead of bypassing it. If stronger isolation is required, the parent creates
an isolated worktree or starts from a more restrictive permission profile.

## Assignment contract

Every staged assignment identifies its mode as analysis or coding. A coding
assignment must provide:

- the concrete objective and repository or directory in scope;
- an explicit writable scope consisting of named files or directories;
- exclusions, including credentials and unrelated external state;
- required validation commands and the expected evidence;
- a stopping condition and instructions to report blocked writes without
  broadening permissions.

The worker may inspect the minimum context needed, edit within the writable
scope, and run the assigned validation. It must not modify files outside that
scope, commit, push, create pull requests, change credentials, or mutate
external systems unless a future policy explicitly adds those operations.

The parent remains responsible for independently reviewing the resulting diff,
running final verification, deciding whether to keep the changes, and performing
all Git and pull-request operations.

## User-facing changes

The agent description and developer instructions will describe bounded coding,
repository research, review, and verification rather than a fixed text-only
no-write worker. The installed skill and managed `AGENTS.md` block will route
explicit coding assignments to the worker and require the writable-scope and
validation contract. Documentation will continue to distinguish inherited
runtime permissions from policy authorization.

## Verification

Static regression tests will install the managed agent and assert that its
instructions permit writes only when the parent explicitly requests them,
require the writable scope, prohibit scope expansion and Git/external actions,
and no longer contain the fixed `WRITE_SCOPE_UNSUPPORTED` behavior.

The normal test suite must remain green. A live native-child smoke will use a
throwaway Git repository under `/private/tmp`, authorize one source file, ask
the child to implement a small function and run its test, and then let the
parent verify the exact diff, test result, provider/model metadata, and absence
of out-of-scope changes. The smoke must not touch the product repository or
perform Git network operations.
