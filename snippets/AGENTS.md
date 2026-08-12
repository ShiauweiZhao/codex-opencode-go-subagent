<!-- codex-opencode-go-subagent:start -->
- Code implementation defaults to `v4_flash_worker`: features, bug fixes,
  refactors, tests, code-related documentation, and cross-module wiring after
  the parent has resolved interfaces and behavior. Multi-file or cross-module
  complexity is not by itself a reason to refuse V4; the preselected GPT parent
  first resolves design ambiguity and decomposes the work into one or more
  bounded coding assignments.
- Keep requirements clarification, analysis, audit, assessment, design,
  planning, architecture, interface and behavior decisions, task decomposition,
  integration-point mapping, test-gap discovery, consequential judgment, code
  review, final verification, integration decisions, and Git operations on the
  preselected GPT parent. It may spawn read-only `gpt_review_worker`, which
  inherits the GPT model selected by the user.
- Every V4 assignment must declare an explicit writable scope and validation
  commands. Start multiple V4 workers in parallel when the work splits into
  independent, non-conflicting, dependency-free writable scopes; fall back to
  sequential execution only for dependent batches or same-file conflicts. V4
  also handles pure
  extraction, literal lookup, and enumeration when the requested output is
  mechanically checkable.
- The V4 worker returns `ESCALATE_TO_GPT` only for unresolved design choices,
  required scope expansion, safety or consequential judgment, a missing writable
  scope or validation oracle, or sandbox/approval boundaries. After the GPT
  parent resolves the boundary, hand the remaining implementation back to V4
  where feasible.
- Keep sandbox and approval-boundary actions on the GPT parent. The V4 worker must
  return `ESCALATE_TO_GPT` instead of requesting escalation. It never commits,
  pushes, creates pull requests, performs final review or verification, handles
  provider fallback or credentials, or escalates permissions; the OpenCode Go
  bridge fails closed with no provider switch.
- Before spawning or troubleshooting `v4_flash_worker`, use
  `$use-v4-flash-worker` and its plaintext-Hook workflow. Never bypass it with
  inherited turns, direct API calls, another CLI, or a fallback provider.
<!-- codex-opencode-go-subagent:end -->
