import hashlib
import json
import os
import subprocess
import sys
import tempfile
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
        first = install(self.repo_root, self.codex_home)
        second = install(self.repo_root, self.codex_home)

        self.assertEqual(first["status"], "installed")
        self.assertEqual(second["status"], "already_installed")
        self.assertEqual(digest(self.codex_home / "config.toml"), self.config_digest)
        self.assertEqual(digest(self.codex_home / "auth.json"), self.auth_digest)

        agent = (self.codex_home / "agents" / "v4-flash-worker.toml").read_text()
        self.assertIn('model = "deepseek-v4-flash"', agent)
        self.assertIn('wire_api = "responses"', agent)
        self.assertIn("http://127.0.0.1:4141/v1", agent)
        self.assertNotIn("OPENCODE_GO_API_KEY", agent)
        self.assertNotIn('env_key = "CODEX_OPENCODE_BRIDGE_TOKEN"', agent)
        self.assertIn("[model_providers.opencode_go_bridge.auth]", agent)
        expected_service = (
            self.codex_home
            / "opencode-go-subagent"
            / "bin"
            / "codex-opencode-go-service"
        ).resolve()
        self.assertIn(f'command = "{expected_service}"', agent)
        model_catalog = (
            self.codex_home
            / "opencode-go-subagent"
            / "deepseek-v4-flash-models.json"
        ).resolve()
        self.assertTrue(model_catalog.is_file())
        self.assertIn(f'model_catalog_json = "{model_catalog}"', agent)
        model = json.loads(model_catalog.read_text())["models"][0]
        self.assertEqual(model["slug"], "deepseek-v4-flash")
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
            if item.get("matcher") == "^v4_flash_worker$"
        ]
        self.assertEqual(len(target), 1)
        command = target[0]["hooks"][0]["command"]
        self.assertIn("codex-opencode-go-subagent/plaintext_handoff.py", command)
        self.assertIn("--mode hook", command)

        agents_text = (self.codex_home / "AGENTS.md").read_text()
        self.assertTrue(agents_text.startswith("existing instructions\n"))
        self.assertEqual(agents_text.count("codex-opencode-go-subagent:start"), 1)

    def test_installed_policy_allows_only_explicitly_scoped_coding(self):
        install(self.repo_root, self.codex_home)

        agent = (self.codex_home / "agents" / "v4-flash-worker.toml").read_text()
        skill = (
            self.codex_home
            / "skills"
            / "use-v4-flash-worker"
            / "SKILL.md"
        ).read_text()
        agents = (self.codex_home / "AGENTS.md").read_text()

        self.assertIn("explicitly authorizes a coding task", agent)
        self.assertIn("simple, bounded work", agent)
        self.assertIn("ESCALATE_TO_GPT", agent)
        self.assertIn("writable scope", agent)
        self.assertIn("Never commit, push, create pull requests", agent)
        self.assertIn("structured apply_patch", agent)
        self.assertIn("Never construct an exec_command write", agent)
        self.assertIn("apply_patch_freeform", agent)
        self.assertNotIn("apply_patch executable", agent)
        self.assertNotIn("removed the native freeform apply_patch tool", agent)
        self.assertNotIn("Never modify files or external state", agent)
        self.assertNotIn("WRITE_SCOPE_UNSUPPORTED", agent)

        self.assertIn("simple, bounded, mechanically verifiable coding", skill)
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

        self.assertIn("simple, bounded, mechanically verifiable coding", agents)
        self.assertIn("explicit writable scope", agents)
        self.assertIn("final verification", agents)
        self.assertIn("Git operations", agents)
        self.assertIn("preselected GPT parent", agents)
        self.assertIn("gpt_review_worker", agents)
        self.assertIn("analysis, audit, assessment", agents)
        self.assertIn("pure extraction", agents)
        self.assertNotIn("Analysis is non-mutating by default", agents)

        self.assertIn("task mode (`coding` or `extraction`)", skill)
        self.assertIn("Never send an analysis or audit assignment", skill)
        self.assertIn("stage-handoff", skill)
        self.assertIn("Do not fall back to direct filesystem staging", skill)

    def test_every_v4_policy_surface_keeps_analysis_and_audit_on_gpt(self):
        surfaces = {
            "root AGENTS": (self.repo_root / "AGENTS.md").read_text(),
            "installed AGENTS snippet": (self.repo_root / "snippets" / "AGENTS.md").read_text(),
            "worker role": (self.repo_root / "agents" / "v4-flash-worker.toml").read_text(),
            "worker skill": (
                self.repo_root / "skills" / "use-v4-flash-worker" / "SKILL.md"
            ).read_text(),
        }

        for name, policy in surfaces.items():
            with self.subTest(name=name):
                lowered = policy.lower()
                self.assertIn("analysis", lowered)
                self.assertIn("audit", lowered)
                self.assertIn("gpt", lowered)
                self.assertNotIn("analysis is non-mutating by default", lowered)
                self.assertNotIn("analysis by default", lowered)

    def test_uninstall_removes_only_managed_files_and_hook(self):
        install(self.repo_root, self.codex_home)

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
        self.assertFalse((self.codex_home / "agents" / "v4-flash-worker.toml").exists())
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

        install(self.repo_root, self.codex_home)
        service = FakeManagedService()

        report = uninstall(
            self.codex_home,
            service_manager=service,
            purge_secrets=True,
        )

        self.assertEqual(service.calls, [True])
        self.assertFalse(report["service"]["secrets_preserved"])

    def test_uninstall_without_service_manager_is_portable_off_macos(self):
        install(self.repo_root, self.codex_home)

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
        agent = self.codex_home / "agents" / "v4-flash-worker.toml"
        agent.parent.mkdir(parents=True)
        agent.write_text("user-owned agent\n")

        with self.assertRaisesRegex(RuntimeError, "unmanaged or modified"):
            install(self.repo_root, self.codex_home)

        self.assertEqual(agent.read_text(), "user-owned agent\n")
        self.assertFalse((self.codex_home / "opencode-go-subagent").exists())


if __name__ == "__main__":
    unittest.main()
