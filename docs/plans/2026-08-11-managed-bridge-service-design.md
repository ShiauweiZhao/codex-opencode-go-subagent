# Managed bridge service design

Date: 2026-08-11

## Decision

Keep the Responses-to-Chat bridge as a separate localhost process because the
two upstream protocols still differ, but remove all manual foreground startup
from the normal macOS workflow. The installer will deploy a per-user
LaunchAgent and a small service CLI. `launchd` starts the bridge at login and
restarts it after an unexpected exit.

The first release of this lifecycle integration is macOS-specific. Linux keeps
the existing explicit bridge command until a secret-service backend is designed
and tested. No Docker runtime, second Codex process, MCP server, or model
fallback is introduced.

## Components and data flow

`codex-opencode-go-service` owns `configure`, `install`, `start`, `stop`,
`restart`, `status`, `doctor`, `run`, and `print-bridge-token` actions.
`configure` reads the OpenCode Go key with a hidden prompt, generates a distinct
local bearer token, stores both as generic-password items in the user's macOS
Keychain, writes a secret-free LaunchAgent plist, bootstraps it, and waits for
`/healthz`.

The LaunchAgent runs `codex-opencode-go-service run`. That action retrieves both
credentials from Keychain, places them only in the bridge process environment,
and starts the existing localhost server. Neither credential appears in the
plist, process arguments, repository, handoff state, or normal logs.

The custom agent provider no longer relies on a GUI-wide `launchctl setenv`.
Instead it uses Codex's command-backed provider authentication. The installer
renders the absolute installed service-launcher path into the managed agent TOML;
Codex executes `print-bridge-token`, caches the returned local token, and sends
it only to `127.0.0.1:4141`. The upstream key is never returned to Codex.

## Lifecycle and failure behavior

The plist uses `RunAtLoad` and restart-on-unsuccessful-exit. Service operations
are idempotent: installing an already loaded service refreshes the managed plist
and restarts it; starting an unloaded service bootstraps it; stopping unloads
it. `status` and `doctor` report structured JSON without credential values.

Configuration fails before changing launchd state if Keychain writes fail.
Service start fails visibly when credentials are missing, the plist is invalid,
port 4141 is occupied, or health never becomes ready. The bridge remains bound
to loopback and keeps its independent bearer check.

Normal uninstall unloads the LaunchAgent and removes only manifest-managed
files and the managed plist. It preserves Keychain items and reports them as
preserved. Secret deletion requires the explicit `--purge-secrets` option so an
ordinary reinstall cannot accidentally destroy the user's upstream key.

## Verification

Tests are written before production changes. Unit tests cover native Keychain
backend delegation and redacted failures, plist contents, generated
agent command-auth, idempotent lifecycle commands, status redaction, installer
upgrade, uninstall preservation, and explicit secret purge. Integration tests
run the service entry point with temporary credentials and a fake upstream; they
must never contact OpenCode Go.

On the real macOS host, verification covers plist validation, bootstrap,
`/healthz`, killing and automatic restart, configuration/login-file hash
preservation, and clean bootout. After those no-cost checks pass, one authorized
native child smoke verifies the provider auth command, managed bridge, plaintext
handoff, `deepseek-v4-flash` routing, callback, and unchanged parent model.
