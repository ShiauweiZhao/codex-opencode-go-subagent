<!-- codex-opencode-go-subagent:start -->
- Route only simple, bounded, mechanically verifiable coding and pure extraction,
  literal lookup, or enumeration to `v4_flash_worker`.
- Keep analysis, audit, assessment, design, planning, architecture, integration-
  point mapping, test-gap discovery, ambiguous or complex implementation,
  consequential judgment, code review, final verification, Git operations, and
  integration on the preselected GPT parent. It may spawn read-only
  `gpt_review_worker`, which inherits the GPT model selected by the user.
- A cross-module wiring task is not simple merely because its requested diff is
  small. Route it to V4 only after GPT has resolved the interfaces, behavior,
  writable scope, and validation oracle into one mechanical implementation job.
- Keep sandbox and approval-boundary actions on the GPT parent. The V4 worker must
  return `ESCALATE_TO_GPT` instead of requesting escalation.
- Coding assignments must provide an explicit writable scope and validation
  commands. Native children inherit the parent turn's runtime permission profile.
  If the assignment requires analysis, judgment, or becomes complex or ambiguous,
  the V4 worker must stop and return it to GPT.
- Before spawning or troubleshooting `v4_flash_worker`, use
  `$use-v4-flash-worker` and its plaintext-Hook workflow. Never bypass it with
  inherited turns, direct API calls, another CLI, or a fallback provider.
<!-- codex-opencode-go-subagent:end -->
