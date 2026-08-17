"""Conservative installer for the custom agent, skill, and plaintext Hook."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


JSON = dict[str, Any]
AGENTS_START = "<!-- codex-opencode-go-subagent:start -->"
AGENTS_END = "<!-- codex-opencode-go-subagent:end -->"
AGENT_NAME = "opencode_go_v4_worker"
HOOK_MATCHER = f"^{AGENT_NAME}$"
MANIFEST_RELATIVE = Path("opencode-go-subagent") / "install-manifest.json"
AUTH_BODY_PLACEHOLDER = "__CODEX_OPENCODE_GO_AUTH_BODY__"
AUTH_BODY_LINE = f"placeholder = \"{AUTH_BODY_PLACEHOLDER}\""
MODEL_CATALOG_PLACEHOLDER = "__CODEX_OPENCODE_GO_MODEL_CATALOG__"
PYTHON_PLACEHOLDER = "__PYTHON_EXECUTABLE__"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return True


def _managed_sources(repo_root: Path) -> list[tuple[Path, Path, int]]:
    sources: list[tuple[Path, Path, int]] = [
        (
            repo_root / "agents" / "opencode-go-v4-worker.toml",
            Path("agents") / "opencode-go-v4-worker.toml",
            0o600,
        ),
        (
            repo_root / "agents" / "gpt-review-worker.toml",
            Path("agents") / "gpt-review-worker.toml",
            0o600,
        ),
        (
            repo_root / "agents" / "deepseek-v4-flash-models.json",
            Path("opencode-go-subagent") / "deepseek-v4-flash-models.json",
            0o600,
        ),
        (
            repo_root / "hooks" / "plaintext_handoff.py",
            Path("hooks") / "codex-opencode-go-subagent" / "plaintext_handoff.py",
            0o700,
        ),
    ]
    skill_root = repo_root / "skills" / "use-opencode-go-v4-worker"
    for source in sorted(path for path in skill_root.rglob("*") if path.is_file()):
        sources.append((source, Path("skills") / "use-opencode-go-v4-worker" / source.relative_to(skill_root), 0o600))
    package_root = repo_root / "src" / "codex_opencode_go_bridge"
    for source in sorted(path for path in package_root.rglob("*.py") if path.is_file()):
        sources.append(
            (
                source,
                Path("opencode-go-subagent")
                / "runtime"
                / "codex_opencode_go_bridge"
                / source.relative_to(package_root),
                0o600,
            )
        )
    sources.append(
        (
            repo_root / "scripts" / "codex-opencode-go-bridge",
            Path("opencode-go-subagent") / "bin" / "codex-opencode-go-bridge",
            0o700,
        )
    )
    sources.append(
        (
            repo_root / "scripts" / "codex-opencode-go-service",
            Path("opencode-go-subagent") / "bin" / "codex-opencode-go-service",
            0o700,
        )
    )
    return sources


def _render_auth_body(service_path: Path, platform: str) -> str:
    """Render provider auth for the active platform.

    macOS keeps the command-backed codex-opencode-go-service
    print-bridge-token flow (Keychain owns the local bearer). Linux has no
    managed service and renders env_key = "CODEX_OPENCODE_BRIDGE_TOKEN" so
    Codex reads the same local bearer exported for the bridge process.
    """
    if platform == "darwin":
        escaped = str(service_path).replace("\\", "\\\\").replace('"', '\\"')
        return (
            "[model_providers.opencode_go_bridge.auth]\n"
            f'command = "{escaped}"\n'
            'args = ["print-bridge-token"]\n'
            "timeout_ms = 5000\n"
            "refresh_interval_ms = 300000"
        )
    return 'env_key = "CODEX_OPENCODE_BRIDGE_TOKEN"'


def _source_data(source: Path, relative: Path, codex_home: Path, platform: str) -> bytes:
    data = source.read_bytes()
    if relative == Path("agents") / "opencode-go-v4-worker.toml":
        service_path = (
            codex_home / "opencode-go-subagent" / "bin" / "codex-opencode-go-service"
        )
        data = data.replace(
            AUTH_BODY_LINE.encode(),
            _render_auth_body(service_path, platform).encode(),
        )
        catalog_path = codex_home / "opencode-go-subagent" / "deepseek-v4-flash-models.json"
        escaped_catalog = str(catalog_path).replace("\\", "\\\\").replace('"', '\\"')
        data = data.replace(MODEL_CATALOG_PLACEHOLDER.encode(), escaped_catalog.encode())
    if relative in {
        Path("opencode-go-subagent") / "bin" / "codex-opencode-go-bridge",
        Path("opencode-go-subagent") / "bin" / "codex-opencode-go-service",
    }:
        escaped_python = (
            str(Path(sys.executable))
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )
        data = data.replace(PYTHON_PLACEHOLDER.encode(), escaped_python.encode())
    return data


def _load_hooks(path: Path) -> JSON:
    if not path.exists():
        return {"description": "Codex user hooks", "hooks": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot parse existing hooks.json: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("hooks", {}), dict):
        raise RuntimeError("existing hooks.json must contain an object-valued hooks field")
    payload.setdefault("hooks", {})
    return payload


def _hook_entry(script_path: Path) -> JSON:
    escaped = str(script_path).replace('"', '\\"')
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": f'python3 "{escaped}" --mode hook',
                "timeout": 10,
                "statusMessage": "Delivering the staged OpenCode Go Flash assignment",
                "additionalContextLimit": 0,
            }
        ],
    }


def _merge_hook(payload: JSON, entry: JSON) -> JSON:
    events = payload.setdefault("hooks", {})
    current = events.get("SubagentStart") or []
    if not isinstance(current, list):
        raise RuntimeError("hooks.SubagentStart must be a list")
    kept = [item for item in current if not isinstance(item, dict) or item.get("matcher") != HOOK_MATCHER]
    events["SubagentStart"] = kept + [entry]
    return payload


def _remove_hook(payload: JSON) -> JSON:
    events = payload.setdefault("hooks", {})
    current = events.get("SubagentStart") or []
    if isinstance(current, list):
        kept = [item for item in current if not isinstance(item, dict) or item.get("matcher") != HOOK_MATCHER]
        if kept:
            events["SubagentStart"] = kept
        else:
            events.pop("SubagentStart", None)
    return payload


def _replace_agents_block(existing: str, block: str | None) -> str:
    start = existing.find(AGENTS_START)
    if start >= 0:
        end = existing.find(AGENTS_END, start)
        if end < 0:
            raise RuntimeError("AGENTS.md contains an unterminated managed block")
        end += len(AGENTS_END)
        if end < len(existing) and existing[end] == "\n":
            end += 1
        prefix = existing[:start]
        if prefix.endswith("\n\n"):
            prefix = prefix[:-1]
        existing = prefix + existing[end:]
    if block is None:
        return existing
    prefix = existing
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    return prefix + block.rstrip() + "\n"


def install(repo_root: Path, codex_home: Path, *, platform: str | None = None) -> JSON:
    repo_root = Path(repo_root).resolve()
    codex_home = Path(codex_home).expanduser().resolve()
    platform = sys.platform if platform is None else platform
    if platform not in {"darwin", "linux"}:
        raise RuntimeError(f"unsupported install platform: {platform}")
    changed = False
    manifest_files: dict[str, str] = {}
    manifest_path = codex_home / MANIFEST_RELATIVE
    old_manifest: JSON = {}
    if manifest_path.exists():
        try:
            old_manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError as error:
            raise RuntimeError(f"cannot parse existing install manifest: {error}") from error
    old_files = old_manifest.get("managed_files") or {}
    sources = _managed_sources(repo_root)

    # Validate every managed target before the first write. Existing identical
    # files may be adopted; different files must either match our prior manifest
    # or be resolved explicitly by the user.
    for source, relative, _mode in sources:
        if not source.is_file():
            raise RuntimeError(f"missing install source: {source}")
        destination = codex_home / relative
        data = _source_data(source, relative, codex_home, platform)
        if not destination.exists() or destination.read_bytes() == data:
            continue
        expected_old = old_files.get(str(relative))
        if not expected_old or _sha256(destination.read_bytes()) != expected_old:
            raise RuntimeError(f"refusing to overwrite unmanaged or modified file: {destination}")

    hooks_path = codex_home / "hooks.json"
    hooks = _merge_hook(
        _load_hooks(hooks_path),
        _hook_entry(codex_home / "hooks" / "codex-opencode-go-subagent" / "plaintext_handoff.py"),
    )
    agents_path = codex_home / "AGENTS.md"
    existing_agents = agents_path.read_text() if agents_path.exists() else ""
    block = (repo_root / "snippets" / "AGENTS.md").read_text()
    merged_agents = _replace_agents_block(existing_agents, block)

    for source, relative, mode in sources:
        data = _source_data(source, relative, codex_home, platform)
        destination = codex_home / relative
        changed = _atomic_write(destination, data, mode) or changed
        manifest_files[str(relative)] = _sha256(data)

    hooks_data = (json.dumps(hooks, ensure_ascii=False, indent=2) + "\n").encode()
    changed = _atomic_write(hooks_path, hooks_data, 0o600) or changed

    changed = _atomic_write(agents_path, merged_agents.encode(), 0o600) or changed

    manifest = {
        "schema_version": 1,
        "managed_files": manifest_files,
        "hook_matcher": HOOK_MATCHER,
        "agent": AGENT_NAME,
    }
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    changed = _atomic_write(manifest_path, manifest_data, 0o600) or changed
    return {
        "status": "installed" if changed else "already_installed",
        "codex_home": str(codex_home),
        "hook_trust_required": True,
        "paid_smoke_run": False,
    }


def uninstall(
    codex_home: Path,
    *,
    service_manager=None,
    purge_secrets: bool = False,
) -> JSON:
    codex_home = Path(codex_home).expanduser().resolve()
    service_report: JSON = {
        "status": "not_managed",
        "secrets_preserved": True,
    }
    if service_manager is None and sys.platform == "darwin":
        default_codex_home = (Path.home() / ".codex").resolve()
        if codex_home == default_codex_home:
            from .managed_service import service_for_codex_home

            service_manager = service_for_codex_home(codex_home)
    if service_manager is not None:
        service_report = service_manager.uninstall(purge_secrets=purge_secrets)
    manifest_path = codex_home / MANIFEST_RELATIVE
    preserved: list[str] = []
    removed: list[str] = []
    manifest: JSON = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    for relative, expected_hash in (manifest.get("managed_files") or {}).items():
        target = codex_home / relative
        if not target.exists():
            continue
        if _sha256(target.read_bytes()) != expected_hash:
            preserved.append(relative)
            continue
        target.unlink()
        removed.append(relative)

    hooks_path = codex_home / "hooks.json"
    if hooks_path.exists():
        hooks = _remove_hook(_load_hooks(hooks_path))
        _atomic_write(hooks_path, (json.dumps(hooks, ensure_ascii=False, indent=2) + "\n").encode(), 0o600)

    agents_path = codex_home / "AGENTS.md"
    if agents_path.exists():
        cleaned = _replace_agents_block(agents_path.read_text(), None)
        _atomic_write(agents_path, cleaned.encode(), 0o600)

    if manifest_path.exists():
        manifest_path.unlink()
    return {
        "status": "uninstalled",
        "removed": removed,
        "preserved_modified": preserved,
        "service": service_report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the OpenCode Go Flash Codex subagent")
    parser.add_argument("action", choices=("install", "uninstall"), nargs="?", default="install")
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--purge-secrets", action="store_true")
    args = parser.parse_args(argv)
    report = (
        install(args.repo_root, args.codex_home)
        if args.action == "install"
        else uninstall(args.codex_home, purge_secrets=args.purge_secrets)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
