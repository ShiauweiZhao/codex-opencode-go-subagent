import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
        self.assertNotIn("sandbox_mode", agent)
        self.assertIn("reapply the parent turn's runtime permission profile", agent)

        runtime = (
            self.codex_home
            / "opencode-go-subagent"
            / "runtime"
            / "codex_opencode_go_bridge"
            / "server.py"
        )
        launcher = self.codex_home / "opencode-go-subagent" / "bin" / "codex-opencode-go-bridge"
        self.assertTrue(runtime.is_file())
        self.assertTrue(launcher.is_file())
        self.assertTrue(launcher.stat().st_mode & 0o100)
        self.assertIn("codex_opencode_go_bridge", launcher.read_text())

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

    def test_uninstall_removes_only_managed_files_and_hook(self):
        install(self.repo_root, self.codex_home)
        report = uninstall(self.codex_home)

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
