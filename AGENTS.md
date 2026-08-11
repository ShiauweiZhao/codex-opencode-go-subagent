# Repository instructions

- Keep the parent Codex model/provider and ChatGPT login unchanged.
- Keep `v4_flash_worker` text-only and behaviorally no-write. Do not claim its
  role TOML independently enforces a read-only sandbox: current Codex reapplies
  the parent turn's runtime permission profile after role loading.
- Maintain one explicit transport path: Codex Responses → localhost bridge →
  OpenCode Go Chat Completions. Do not add silent model or provider fallback.
- Never read, print, persist in the repository, or place in command arguments
  the values of `OPENCODE_GO_API_KEY` or `CODEX_OPENCODE_BRIDGE_TOKEN`.
- Write tests first for behavior changes. Run
  `PYTHONPATH=src python3 -m unittest discover -s tests -v` before completion.
- Local tests and fake-upstream smoke must not contact paid APIs. A live OpenCode
  Go or native-child smoke requires explicit user authorization.
- Preserve third-party notices when changing imported plaintext handoff code.
