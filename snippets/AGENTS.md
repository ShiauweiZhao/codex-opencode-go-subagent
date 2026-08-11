<!-- codex-opencode-go-subagent:start -->
- Route simple, bounded, mechanically verifiable coding, repository lookup, log
  search, extraction, enumeration, and high-volume reading to `v4_flash_worker`.
- Keep planning, architecture, ambiguous or complex implementation, consequential
  judgment, code review, final verification, Git operations, and integration on
  the preselected GPT parent. It may spawn read-only `gpt_review_worker`, which
  inherits the GPT model selected by the user.
- Keep sandbox and approval-boundary actions on the GPT parent. The V4 worker must
  return `ESCALATE_TO_GPT` instead of requesting escalation.
- Analysis is non-mutating by default. Coding assignments must provide an
  explicit writable scope and validation commands. Native children inherit the
  parent turn's runtime permission profile. If the assignment becomes complex or
  ambiguous, the V4 worker must stop and return it to GPT.
- Before spawning or troubleshooting `v4_flash_worker`, use
  `$use-v4-flash-worker` and its plaintext-Hook workflow. Never bypass it with
  inherited turns, direct API calls, another CLI, or a fallback provider.
<!-- codex-opencode-go-subagent:end -->
