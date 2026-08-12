# Explicitly authorized coding worker design

Date: 2026-08-11
Updated: 2026-08-12 - V4 becomes the default implementer; GPT resolves
interfaces and behavior and decomposes work into bounded coding assignments.
Concurrency is the default expectation: when development work can be split into
independent, non-conflicting, dependency-free writable scopes, the GPT parent
proactively starts multiple V4 workers in parallel; batches execute sequentially
only when they share dependencies or edit the same files.

## Decision

Make `v4_flash_worker` the default implementer: features, fixes, refactoring,
tests, code-related documentation, and cross-module wiring land with V4 once the
GPT parent has resolved the interfaces and behavior. Complexity, multiple files,
or cross-module reach does not itself require GPT to write code; GPT first
removes ambiguity and decomposes the work into one or more bounded coding
assignments, and V4 implements them. Requirements clarification, analysis,
audit, assessment, design, planning, architecture, interface and behavior
decisions, task decomposition, consequential judgment, code review, and final
validation remain on the preselected GPT parent. This follows the lightweight
conditional-write model:
the native child works in the parent's workspace and inherits the parent's
runtime permission profile. No second Codex process, implicit privilege change,
automatic worktree, or patch-escrow runtime is introduced.

The role configuration is a behavioral contract, not an independent sandbox.
If the parent runtime denies a requested write, the child reports that boundary
instead of bypassing it. If stronger isolation is required, the parent creates
an isolated worktree or starts from a more restrictive permission profile.

## Assignment contract

Every staged assignment identifies its mode as extraction or coding and
defaults to implementation by V4. Analysis, audit, assessment, design,
planning, interface/behavior decisions, task decomposition, and test-gap
discovery stay on the preselected GPT parent; once resolved, GPT hands back a
bounded coding assignment. A coding assignment must provide:

- the concrete objective and repository or directory in scope;
- an explicit writable scope consisting of named files or directories;
- exclusions, including credentials and unrelated external state;
- required validation commands and the expected evidence;
- a stopping condition and instructions to report blocked writes without
  broadening permissions.

When development work can be split into independent, non-conflicting,
dependency-free writable scopes, the GPT parent proactively starts multiple V4
workers in parallel; batches execute sequentially only when they share
dependencies or edit the same files. Each batch still carries an explicit scope
and validation.

The worker escalates with `ESCALATE_TO_GPT` only for unresolved design choices,
scope expansion, security-sensitive or consequential judgment, a missing
scope/oracle, or a sandbox/approval boundary. After the GPT parent resolves the
issue, it returns the remaining coding to V4 whenever possible; the worker never
expands its own role or bypasses permissions.

The worker may inspect the minimum context needed, edit within the writable
scope, and run the assigned validation. It must not modify files outside that
scope, commit, push, create pull requests, change credentials, or mutate
external systems unless a future policy explicitly adds those operations.
Bridge/provider failures fail closed rather than falling back to another model
or provider.

The preselected GPT parent remains responsible for independently reviewing the
resulting diff, running final verification, deciding whether to keep the changes,
and performing all Git and pull-request operations. It may spawn the read-only
`gpt_review_worker`, which inherits the user's selected GPT model.

## User-facing changes

The agent description and developer instructions will describe V4 as the default
implementer (features, fixes, refactoring, tests, code-related documentation,
and post-resolution cross-module wiring) rather than a fixed text-only no-write
worker or a simple-task-only coder. The installed skill and managed `AGENTS.md`
block will keep requirements clarification, analysis, design, planning,
interface/behavior decisions, task decomposition, consequential judgment,
review, and final verification on GPT, while requiring the V4 writable-scope and
validation contract and defaulting to proactive parallel V4 workers for
independent, non-conflicting, dependency-free batches with sequential execution
only for dependent or same-file batches.
Documentation will continue to distinguish inherited runtime permissions from
policy authorization.

## Verification

Static regression tests will install the managed agent and assert that its
instructions make V4 the default implementer while keeping requirements
clarification, design, planning, interface/behavior decisions, review, final
verification, and Git/PR on GPT; require the writable scope and validation per
batch; require proactive parallel V4 workers for independent, non-conflicting,
dependency-free writable scopes and sequential execution only when batches share
dependencies or edit the same files; prohibit scope expansion, privilege
escalation, and Git/external actions; and no longer contain the fixed
`WRITE_SCOPE_UNSUPPORTED` behavior.

The normal test suite must remain green. A live native-child smoke will use a
throwaway Git repository under `/private/tmp`, authorize one source file, ask
the child to implement a small function and run its test, and then let the
GPT parent verify the exact diff, test result, provider/model metadata, absence
of approval-boundary requests, and absence of out-of-scope changes. The smoke
must not touch the product repository or perform Git network operations.
