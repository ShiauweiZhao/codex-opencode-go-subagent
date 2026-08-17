import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch


import codex_opencode_go_bridge.installer as installer_module
from codex_opencode_go_bridge.installer import install, uninstall


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.codex_home = Path(self.tmp.name) / "codex-home"
        self.codex_home.mkdir()
        self.repo_root = Path(__file__).resolve().parents[1]
        (self.codex_home / "config.toml").write_text('model = "gpt-5.6"\n')
        (self.codex_home / "auth.json").write_text('{"existing":"login"}\n')
        (self.codex_home / "AGENTS.md").write_text("existing instructions\n")
        (self.codex_home / "hooks.json").write_text(
            json.dumps(
                {
                    "description": "existing hooks",
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "functions.exec",
                                "hooks": [{"type": "command", "command": "true"}],
                            }
                        ]
                    },
                }
            )
        )
        self.config_digest = digest(self.codex_home / "config.toml")
        self.auth_digest = digest(self.codex_home / "auth.json")

    def test_install_is_idempotent_and_preserves_parent_config_and_login(self):
        first = install(self.repo_root, self.codex_home, platform="linux")
        second = install(self.repo_root, self.codex_home, platform="linux")

        self.assertEqual(first["status"], "installed")
        self.assertEqual(second["status"], "already_installed")
        self.assertEqual(digest(self.codex_home / "config.toml"), self.config_digest)
        self.assertEqual(digest(self.codex_home / "auth.json"), self.auth_digest)

        agent = (self.codex_home / "agents" / "opencode-go-v4-worker.toml").read_text()
        self.assertIn('name = "opencode_go_v4_worker"', agent)
        self.assertIn('model = "deepseek-v4-flash"', agent)
        self.assertIn('wire_api = "responses"', agent)
        self.assertIn("http://127.0.0.1:4141/v1", agent)
        self.assertNotIn("OPENCODE_GO_API_KEY", agent)
        parsed_agent = tomllib.loads(agent)
        provider = parsed_agent["model_providers"]["opencode_go_bridge"]
        self.assertEqual(provider["env_key"], "CODEX_OPENCODE_BRIDGE_TOKEN")
        self.assertNotIn("auth", provider)
        self.assertIn('env_key = "CODEX_OPENCODE_BRIDGE_TOKEN"', agent)
        self.assertNotIn("print-bridge-token", agent)
        self.assertNotIn('args = ["print-bridge-token"]', agent)
        self.assertNotIn("__CODEX_OPENCODE_GO_AUTH_BODY__", agent)
        model_catalog = (
            self.codex_home
            / "opencode-go-subagent"
            / "deepseek-v4-flash-models.json"
        ).resolve()
        self.assertTrue(model_catalog.is_file())
        self.assertIn(f'model_catalog_json = "{model_catalog}"', agent)
        model = json.loads(model_catalog.read_text())["models"][0]
        self.assertEqual(model["slug"], "deepseek-v4-flash")
        self.assertEqual(model["default_reasoning_level"], "max")
        self.assertEqual(
            [level["effort"] for level in model["supported_reasoning_levels"]],
            ["low", "high", "max"],
        )
        self.assertNotIn("apply_patch_tool_type", model)
        self.assertIsNone(model["auto_review_model_override"])
        self.assertNotIn("sandbox_mode", agent)
        self.assertIn("reapply the parent turn's runtime permission profile", agent)

        reviewer = (self.codex_home / "agents" / "gpt-review-worker.toml").read_text()
        self.assertIn('name = "gpt_review_worker"', reviewer)
        self.assertIn('sandbox_mode = "read-only"', reviewer)
        self.assertIn("inherits the GPT model selected by the user", reviewer)
        self.assertNotIn("model_provider =", reviewer)
        self.assertNotIn("model =", reviewer)
        self.assertNotIn("opencode_go_bridge", reviewer)

        runtime = (
            self.codex_home
            / "opencode-go-subagent"
            / "runtime"
            / "codex_opencode_go_bridge"
            / "server.py"
        )
        launcher = self.codex_home / "opencode-go-subagent" / "bin" / "codex-opencode-go-bridge"
        service_launcher = (
            self.codex_home
            / "opencode-go-subagent"
            / "bin"
            / "codex-opencode-go-service"
        )
        self.assertTrue(runtime.is_file())
        self.assertTrue(launcher.is_file())
        self.assertTrue(service_launcher.is_file())
        self.assertTrue(launcher.stat().st_mode & 0o100)
        self.assertTrue(service_launcher.stat().st_mode & 0o100)
        self.assertIn("codex_opencode_go_bridge", launcher.read_text())
        self.assertIn(sys.executable, launcher.read_text())
        self.assertIn(sys.executable, service_launcher.read_text())
        self.assertNotIn("__PYTHON_EXECUTABLE__", launcher.read_text())
        self.assertNotIn("__PYTHON_EXECUTABLE__", service_launcher.read_text())

        hooks = json.loads((self.codex_home / "hooks.json").read_text())
        self.assertIn("PostToolUse", hooks["hooks"])
        target = [
            item
            for item in hooks["hooks"]["SubagentStart"]
            if item.get("matcher") == "^opencode_go_v4_worker$"
        ]
        self.assertEqual(len(target), 1)
        command = target[0]["hooks"][0]["command"]
        self.assertIn("codex-opencode-go-subagent/plaintext_handoff.py", command)
        self.assertIn("--mode hook", command)

        agents_text = (self.codex_home / "AGENTS.md").read_text()
        self.assertTrue(agents_text.startswith("existing instructions\n"))
        self.assertEqual(agents_text.count("codex-opencode-go-subagent:start"), 1)

    def test_installed_policy_defaults_implementation_to_v4_with_explicit_scope(self):
        install(self.repo_root, self.codex_home, platform="linux")

        def collapsed(text):
            return " ".join(text.split())

        agent = collapsed(
            (self.codex_home / "agents" / "opencode-go-v4-worker.toml").read_text()
        )
        skill = collapsed(
            (
                self.codex_home
                / "skills"
                / "use-opencode-go-v4-worker"
                / "SKILL.md"
            ).read_text()
        )
        agents = collapsed((self.codex_home / "AGENTS.md").read_text())

        self.assertIn("Code implementation defaults to", agent)
        self.assertIn("complexity is not by itself a reason", agent)
        self.assertIn("self-contained assignment", agent)
        self.assertIn("task decomposition", agent)
        self.assertNotIn("Start multiple V4 workers", agent)
        self.assertIn("explicitly authorizes a coding task", agent)
        self.assertIn("explicit writable scope", agent)
        self.assertIn("validation commands", agent)
        self.assertIn("ESCALATE_TO_GPT", agent)
        self.assertIn("Never commit, push, create pull requests", agent)
        self.assertIn("final review or verification", agent)
        self.assertIn("fails closed", agent)
        self.assertIn("structured apply_patch", agent)
        self.assertIn("Never construct an exec_command write", agent)
        self.assertIn("apply_patch_freeform", agent)
        self.assertNotIn("apply_patch executable", agent)
        self.assertNotIn("removed the native freeform apply_patch tool", agent)
        self.assertNotIn("Never modify files or external state", agent)
        self.assertNotIn("WRITE_SCOPE_UNSUPPORTED", agent)
        self.assertNotIn("only simple coding", agent)
        self.assertNotIn("only simple, bounded", agent)
        self.assertNotIn("complex implementation stays", agent)

        self.assertIn("Code implementation defaults to", skill)
        self.assertIn("complexity is not by itself a reason", skill)
        self.assertIn(
            "Start multiple V4 workers in parallel when the work splits into "
            "independent, non-conflicting, dependency-free writable scopes",
            skill,
        )
        self.assertIn(
            "sequentially only when batches share dependencies or edit the "
            "same files",
            skill,
        )
        self.assertIn("explicit writable scope", skill)
        self.assertIn("validation commands", skill)
        self.assertIn("preselected", skill)
        self.assertIn("GPT parent", skill)
        self.assertIn("ESCALATE_TO_GPT", skill)
        self.assertIn("rollout JSONL", skill)
        self.assertIn("state.sqlite3", skill)
        self.assertIn("Callback text is not authoritative", skill)
        self.assertIn("safe_exec_apply_patch", skill)
        self.assertIn("[apply_patch, original_patch]", skill)
        self.assertIn("heredocs, redirection, script writes", skill)
        self.assertIn("task mode (`coding` or `extraction`)", skill)
        self.assertIn("Never send an analysis or audit assignment", skill)
        self.assertIn("stage-handoff", skill)
        self.assertIn("Do not fall back to direct filesystem staging", skill)
        self.assertNotIn("only simple coding", skill)
        self.assertNotIn("complex implementation stays", skill)

        self.assertIn("Code implementation defaults to", agents)
        self.assertIn("complexity is not by itself a reason", agents)
        self.assertIn(
            "Start multiple V4 workers in parallel when the work splits into "
            "independent, non-conflicting, dependency-free writable scopes",
            agents,
        )
        self.assertIn(
            "fall back to sequential execution only for dependent batches or "
            "same-file conflicts",
            agents,
        )
        self.assertIn("explicit writable scope", agents)
        self.assertIn("validation", agents)
        self.assertIn("commands", agents)
        self.assertIn("final verification", agents)
        self.assertIn("Git operations", agents)
        self.assertIn("preselected GPT parent", agents)
        self.assertIn("gpt_review_worker", agents)
        self.assertIn("analysis, audit, assessment", agents)
        self.assertIn("pure extraction", agents)
        self.assertIn("fails closed", agents)
        self.assertNotIn("Analysis is non-mutating by default", agents)
        self.assertNotIn("only simple coding", agents)
        self.assertNotIn("complex implementation stays", agents)

    def test_every_v4_policy_surface_keeps_judgment_and_git_on_gpt(self):
        surfaces = {
            "root AGENTS": (self.repo_root / "AGENTS.md").read_text(),
            "installed AGENTS snippet": (self.repo_root / "snippets" / "AGENTS.md").read_text(),
            "worker role": (self.repo_root / "agents" / "opencode-go-v4-worker.toml").read_text(),
            "worker skill": (
                self.repo_root / "skills" / "use-opencode-go-v4-worker" / "SKILL.md"
            ).read_text(),
            "worker skill yaml": (
                self.repo_root
                / "skills"
                / "use-opencode-go-v4-worker"
                / "agents"
                / "openai.yaml"
            ).read_text(),
        }

        for name, policy in surfaces.items():
            with self.subTest(name=name):
                lowered = policy.lower()
                # Default code implementation belongs to V4.
                self.assertIn("implementation", lowered)
                self.assertIn("v4", lowered)
                # Judgment, review, final verification, and Git stay on GPT.
                self.assertIn("gpt", lowered)
                self.assertIn("analysis", lowered)
                self.assertIn("audit", lowered)
                self.assertIn("review", lowered)
                self.assertIn("final verification", lowered)
                self.assertIn("git", lowered)
                # Every coding assignment stays explicit-scope and validated.
                self.assertIn("writable scope", lowered)
                self.assertIn("validation", lowered)
                self.assertIn("escalate_to_gpt", lowered)
                # Old routing semantics must not remain.
                self.assertNotIn("only simple coding", lowered)
                self.assertNotIn("only simple, bounded", lowered)
                self.assertNotIn("complex implementation stays", lowered)
                self.assertNotIn("complex implementation remains", lowered)
                self.assertNotIn("ambiguous or complex implementation", lowered)
                self.assertNotIn("analysis is non-mutating by default", lowered)
                self.assertNotIn("analysis by default", lowered)

    def test_docs_and_security_describe_default_implementation_routing(self):
        docs = {
            "README": (self.repo_root / "README.md").read_text(),
            "SECURITY": (self.repo_root / "SECURITY.md").read_text(),
            "architecture decision": (
                self.repo_root / "docs" / "architecture-decision.md"
            ).read_text(),
            "design plan": (
                self.repo_root
                / "docs"
                / "plans"
                / "2026-08-11-explicit-coding-worker-design.md"
            ).read_text(),
        }

        for name, text in docs.items():
            with self.subTest(name=name):
                lowered = text.lower()
                self.assertIn("v4", lowered)
                self.assertIn("gpt", lowered)
                self.assertIn("implementation", lowered)
                self.assertIn("writable scope", lowered)
                self.assertIn("validation", lowered)
                self.assertIn("escalate_to_gpt", lowered)
                self.assertIn("git", lowered)
                self.assertNotIn("only simple coding", lowered)
                self.assertNotIn("only simple, bounded", lowered)
                self.assertNotIn("complex implementation stays", lowered)

        concurrency_tokens = {
            "README": ("并行", "顺序执行", "同一文件"),
            "SECURITY": ("并行", "顺序执行", "同一文件"),
            "architecture decision": ("并行", "顺序执行", "同一文件"),
            "design plan": ("in parallel", "sequential", "same files"),
        }
        stale_semantics = ("默认单 worker", "单 worker 默认")
        for name, tokens in concurrency_tokens.items():
            with self.subTest(name=name + " concurrency policy"):
                lowered = docs[name].lower()
                self.assertIn("independent", lowered)
                self.assertIn("non-conflicting", lowered)
                self.assertIn("dependency-free", lowered)
                for token in tokens:
                    self.assertIn(token, lowered)
                for stale in stale_semantics:
                    self.assertNotIn(stale, lowered)

    def test_uninstall_removes_only_managed_files_and_hook(self):
        install(self.repo_root, self.codex_home, platform="linux")

        class FakeManagedService:
            def __init__(self):
                self.calls = []

            def uninstall(self, *, purge_secrets):
                self.calls.append(purge_secrets)
                return {
                    "status": "uninstalled",
                    "secrets_preserved": not purge_secrets,
                }

        service = FakeManagedService()
        report = uninstall(self.codex_home, service_manager=service)

        self.assertEqual(report["status"], "uninstalled")
        self.assertFalse((self.codex_home / "agents" / "opencode-go-v4-worker.toml").exists())
        self.assertFalse(
            (self.codex_home / "hooks" / "codex-opencode-go-subagent" / "plaintext_handoff.py").exists()
        )
        self.assertFalse(
            (
                self.codex_home
                / "opencode-go-subagent"
                / "runtime"
                / "codex_opencode_go_bridge"
                / "server.py"
            ).exists()
        )
        hooks = json.loads((self.codex_home / "hooks.json").read_text())
        self.assertIn("PostToolUse", hooks["hooks"])
        self.assertNotIn("SubagentStart", hooks["hooks"])
        self.assertEqual((self.codex_home / "AGENTS.md").read_text(), "existing instructions\n")
        self.assertEqual(digest(self.codex_home / "config.toml"), self.config_digest)
        self.assertEqual(digest(self.codex_home / "auth.json"), self.auth_digest)
        self.assertEqual(service.calls, [False])
        self.assertTrue(report["service"]["secrets_preserved"])

    def test_uninstall_only_purges_keychain_when_explicitly_requested(self):
        class FakeManagedService:
            def __init__(self):
                self.calls = []

            def uninstall(self, *, purge_secrets):
                self.calls.append(purge_secrets)
                return {
                    "status": "uninstalled",
                    "secrets_preserved": not purge_secrets,
                }

        install(self.repo_root, self.codex_home, platform="linux")
        service = FakeManagedService()

        report = uninstall(
            self.codex_home,
            service_manager=service,
            purge_secrets=True,
        )

        self.assertEqual(service.calls, [True])
        self.assertFalse(report["service"]["secrets_preserved"])

    def test_uninstall_without_service_manager_is_portable_off_macos(self):
        install(self.repo_root, self.codex_home, platform="linux")

        with patch.object(installer_module.sys, "platform", "linux"):
            report = uninstall(self.codex_home)

        self.assertEqual(report["service"]["status"], "not_managed")

    def test_repository_install_script_runs_without_preconfigured_pythonpath(self):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [
                sys.executable,
                str(self.repo_root / "scripts" / "install.py"),
                "install",
                "--repo-root",
                str(self.repo_root),
                "--codex-home",
                str(self.codex_home),
            ],
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "installed")

    def test_install_refuses_to_overwrite_an_unmanaged_agent(self):
        agent = self.codex_home / "agents" / "opencode-go-v4-worker.toml"
        agent.parent.mkdir(parents=True)
        agent.write_text("user-owned agent\n")

        with self.assertRaisesRegex(RuntimeError, "unmanaged or modified"):
            install(self.repo_root, self.codex_home, platform="linux")

        self.assertEqual(agent.read_text(), "user-owned agent\n")
        self.assertFalse((self.codex_home / "opencode-go-subagent").exists())


    def test_install_renders_macos_command_auth_when_platform_is_darwin(self):
        report = install(self.repo_root, self.codex_home, platform="darwin")

        self.assertEqual(report["status"], "installed")
        agent = (self.codex_home / "agents" / "opencode-go-v4-worker.toml").read_text()
        expected_service = (
            self.codex_home
            / "opencode-go-subagent"
            / "bin"
            / "codex-opencode-go-service"
        ).resolve()
        self.assertIn(f'command = "{expected_service}"', agent)
        parsed_agent = tomllib.loads(agent)
        provider = parsed_agent["model_providers"]["opencode_go_bridge"]
        self.assertNotIn("env_key", provider)
        self.assertEqual(provider["auth"]["command"], str(expected_service))
        self.assertIn('args = ["print-bridge-token"]', agent)
        self.assertIn("timeout_ms = 5000", agent)
        self.assertIn("refresh_interval_ms = 300000", agent)
        self.assertNotIn('env_key = "CODEX_OPENCODE_BRIDGE_TOKEN"', agent)
        self.assertNotIn("__CODEX_OPENCODE_GO_AUTH_BODY__", agent)
        self.assertNotIn("__CODEX_OPENCODE_GO_MODEL_CATALOG__", agent)

    def test_install_rejects_unknown_platform(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported install platform"):
            install(self.repo_root, self.codex_home, platform="windows")

    def test_install_manifest_records_new_identity_and_matcher(self):
        install(self.repo_root, self.codex_home, platform="linux")

        manifest = json.loads(
            (
                self.codex_home
                / "opencode-go-subagent"
                / "install-manifest.json"
            ).read_text()
        )
        self.assertEqual(manifest["agent"], "opencode_go_v4_worker")
        self.assertEqual(manifest["hook_matcher"], "^opencode_go_v4_worker$")
        self.assertIn("agents/opencode-go-v4-worker.toml", manifest["managed_files"])
        self.assertIn("agents/gpt-review-worker.toml", manifest["managed_files"])
        self.assertIn(
            "skills/use-opencode-go-v4-worker/SKILL.md", manifest["managed_files"]
        )
        self.assertNotIn("agents/v4-flash-worker.toml", manifest["managed_files"])
        self.assertNotIn("skills/use-v4-flash-worker/SKILL.md", manifest["managed_files"])

    def test_install_refuses_unmanaged_collision_on_skill_path(self):
        skill = (
            self.codex_home / "skills" / "use-opencode-go-v4-worker" / "SKILL.md"
        )
        skill.parent.mkdir(parents=True)
        skill.write_text("user-owned skill\n")

        with self.assertRaisesRegex(RuntimeError, "unmanaged or modified"):
            install(self.repo_root, self.codex_home, platform="linux")

        self.assertEqual(skill.read_text(), "user-owned skill\n")
        self.assertFalse((self.codex_home / "opencode-go-subagent").exists())

    def test_old_identity_files_are_left_to_the_existing_direct_deepseek_worker(self):
        old_agent = self.codex_home / "agents" / "v4-flash-worker.toml"
        old_agent.parent.mkdir(parents=True)
        old_agent.write_text("direct-deepseek agent\n")
        old_skill = self.codex_home / "skills" / "use-v4-flash-worker" / "SKILL.md"
        old_skill.parent.mkdir(parents=True)
        old_skill.write_text("direct-deepseek skill\n")
        manifest_path = (
            self.codex_home / "opencode-go-subagent" / "install-manifest.json"
        )
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "managed_files": {
                        "agents/v4-flash-worker.toml": digest(old_agent),
                        "skills/use-v4-flash-worker/SKILL.md": digest(old_skill),
                    },
                    "hook_matcher": "^v4_flash_worker$",
                    "agent": "v4_flash_worker",
                }
            )
        )

        report = install(self.repo_root, self.codex_home, platform="linux")

        self.assertEqual(report["status"], "installed")
        self.assertEqual(old_agent.read_text(), "direct-deepseek agent\n")
        self.assertEqual(old_skill.read_text(), "direct-deepseek skill\n")
        manifest = json.loads(manifest_path.read_text())
        self.assertNotIn("agents/v4-flash-worker.toml", manifest["managed_files"])
        self.assertNotIn(
            "skills/use-v4-flash-worker/SKILL.md", manifest["managed_files"]
        )
        uninstall(self.codex_home)
        self.assertTrue(old_agent.exists())
        self.assertTrue(old_skill.exists())
        self.assertFalse(
            (self.codex_home / "agents" / "opencode-go-v4-worker.toml").exists()
        )

    def test_worker_identity_is_opencode_go_v4_worker_everywhere(self):
        self.assertEqual(installer_module.AGENT_NAME, "opencode_go_v4_worker")
        self.assertEqual(installer_module.HOOK_MATCHER, "^opencode_go_v4_worker$")
        role = (self.repo_root / "agents" / "opencode-go-v4-worker.toml").read_text()
        skill = (
            self.repo_root / "skills" / "use-opencode-go-v4-worker" / "SKILL.md"
        ).read_text()
        skill_yaml = (
            self.repo_root
            / "skills"
            / "use-opencode-go-v4-worker"
            / "agents"
            / "openai.yaml"
        ).read_text()
        snippet = (self.repo_root / "snippets" / "AGENTS.md").read_text()
        hooks_example = (self.repo_root / "hooks" / "hooks.posix.example.json").read_text()
        reviewer = (self.repo_root / "agents" / "gpt-review-worker.toml").read_text()

        self.assertIn('name = "opencode_go_v4_worker"', role)
        self.assertIn("$use-opencode-go-v4-worker", role)
        self.assertIn("opencode_go_v4_worker", skill)
        self.assertIn("name: use-opencode-go-v4-worker", skill)
        self.assertIn("$use-opencode-go-v4-worker", skill_yaml)
        self.assertIn("opencode_go_v4_worker", snippet)
        self.assertIn("$use-opencode-go-v4-worker", snippet)
        self.assertIn('"matcher": "^opencode_go_v4_worker$"', hooks_example)
        self.assertIn("opencode_go_v4_worker", reviewer)
        self.assertNotIn("v4_flash_worker", reviewer)

        for surface in (role, skill, skill_yaml, snippet, hooks_example):
            with self.subTest(surface=surface[:40]):
                self.assertNotIn("v4_flash_worker", surface)
                self.assertNotIn("use-v4-flash-worker", surface)


if __name__ == "__main__":
    unittest.main()
