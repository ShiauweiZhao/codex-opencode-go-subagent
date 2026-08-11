# Quick native smoke test

This test makes a small paid OpenCode Go request. Run it only after the user has
explicitly accepted that call and the localhost bridge is healthy.

1. Generate a fresh random marker in the parent. Do not reuse the example below.
2. Use `$use-v4-flash-worker` exactly as installed.
3. Stage one self-contained assignment for `v4_flash_worker` that asks the child to:
   - return the marker exactly;
   - run one harmless read-only workspace command such as `pwd`;
   - calculate `17 * 19` and return `arithmetic=323`;
   - make no file changes.
4. Spawn the native child with `agent_type="v4_flash_worker"` and
   `fork_turns="none"`; do not use direct API, MCP, OpenCode CLI, another Codex
   process, inherited turns, or a fallback provider.
5. Wait for the native callback and verify the marker, arithmetic, tool evidence,
   no-write boundary, child agent type, model `deepseek-v4-flash`, provider
   `opencode_go_bridge`, and unchanged parent provider/model.
6. Report `ready` only if all evidence agrees. Otherwise report the exact failed
   boundary and keep the status `locally_verified` or `failed`.
