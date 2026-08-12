# Repository instructions

- Keep the parent Codex model/provider and ChatGPT login unchanged.
- Route only simple, bounded, mechanically verifiable coding and pure literal
  lookup, extraction, or enumeration to `v4_flash_worker`. Coding requires an
  explicit writable scope and validation commands. If inference, ambiguity, or
  complexity appears, stop and return the task to the preselected GPT parent.
- Analysis, audit, assessment, design, planning, architecture, integration-point
  mapping, test-gap discovery, consequential judgment, complex implementation,
  code review, final verification, Git operations, and integration stay with the
  preselected GPT parent. It may spawn `gpt_review_worker`, which inherits that
  GPT model and is read-only.
- Any sandbox or approval-boundary action stays with the GPT parent. The V4 child
  must stop with `ESCALATE_TO_GPT` instead of requesting escalation.
- Current Codex reapplies the parent runtime permission profile after role loading;
  inherited capability never broadens authorization.
- Maintain one explicit transport path: Codex Responses → localhost bridge →
  OpenCode Go Chat Completions. Do not add silent model or provider fallback.
- Never read, print, persist in the repository, or place in command arguments
  the values of `OPENCODE_GO_API_KEY` or `CODEX_OPENCODE_BRIDGE_TOKEN`.
- Write tests first for behavior changes. Run
  `PYTHONPATH=src python3 -m unittest discover -s tests -v` before completion.
- Local tests and fake-upstream smoke must not contact paid APIs. A live OpenCode
  Go or native-child smoke requires explicit user authorization.
- Preserve third-party notices when changing imported plaintext handoff code.
