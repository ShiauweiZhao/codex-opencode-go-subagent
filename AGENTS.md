# Repository instructions

- Keep the parent Codex model/provider and ChatGPT login unchanged.
- Code implementation defaults to `v4_flash_worker`: features, bug fixes,
  refactors, tests, code-related documentation, and cross-module wiring after
  interfaces and behavior are resolved. Multi-file or cross-module complexity is
  not by itself a reason to refuse V4; the preselected GPT parent first resolves
  design ambiguity and decomposes the work into one or more bounded coding
  assignments, each with an explicit writable scope and validation commands.
- Requirements clarification, analysis, audit, assessment, design, planning,
  architecture, interface and behavior decisions, task decomposition,
  integration-point mapping, test-gap discovery, consequential judgment, code
  review, final verification, integration decisions, and Git operations stay
  with the preselected GPT parent. It may spawn read-only `gpt_review_worker`,
  which inherits that GPT model.
- When development work can be split into independent, non-conflicting,
  dependency-free writable scopes, start multiple V4 workers in parallel;
  fall back to sequential execution only when batches share dependencies or
  edit the same files.
- The V4 worker returns `ESCALATE_TO_GPT` only for unresolved design choices,
  required scope expansion, safety or consequential judgment, a missing writable
  scope or validation oracle, or sandbox/approval boundaries. After the GPT
  parent resolves the boundary, hand the remaining implementation back to V4
  where feasible.
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
