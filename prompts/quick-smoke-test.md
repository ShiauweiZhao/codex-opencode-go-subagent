# Quick native smoke test

This test makes a small paid OpenCode Go request. Run it only after the user has
explicitly accepted that call and the localhost bridge is healthy.

1. Generate a fresh random marker in the parent. Do not reuse the example below.
2. Use `$use-opencode-go-v4-worker` exactly as installed.
3. Stage one self-contained assignment for `opencode_go_v4_worker` that asks the child to:
   - return the marker exactly;
   - run one harmless read-only workspace command such as `pwd`;
   - calculate `17 * 19` and return `arithmetic=323`;
   - make no file changes.
4. Spawn the native child with `agent_type="opencode_go_v4_worker"` and
   `fork_turns="none"`; do not use direct API, MCP, OpenCode CLI, another Codex
   process, inherited turns, or a fallback provider.
5. Wait for the native callback and verify the marker, arithmetic, tool evidence,
   zero changed files, child agent type, model `deepseek-v4-flash`, provider
   `opencode_go_bridge`, unchanged parent provider/model, and the child permission
   profile recorded by Codex. The child profile is expected to inherit the
   parent profile; do not report it as independently sandboxed read-only.
6. Report `ready` only if all evidence agrees. Otherwise report the exact failed
   boundary and keep the status `locally_verified` or `failed`.
