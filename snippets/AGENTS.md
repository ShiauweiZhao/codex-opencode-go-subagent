<!-- codex-opencode-go-subagent:start -->
- For bounded, preferably read-only text, code, log, search, extraction,
  enumeration, or high-volume reading, the main agent may use
  `v4_flash_worker`; the parent retains verification and integration.
- The worker must not write. Native children inherit the parent turn's runtime
  permission profile on current Codex releases, so use a read-only parent when
  enforced write denial is required.
- Before spawning or troubleshooting `v4_flash_worker`, use
  `$use-v4-flash-worker` and its plaintext-Hook workflow. Never bypass it with
  inherited turns, direct API calls, another CLI, or a fallback provider.
<!-- codex-opencode-go-subagent:end -->
