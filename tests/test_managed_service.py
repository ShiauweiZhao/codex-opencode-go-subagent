import io
import json
import os
import plistlib
import secrets
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


import codex_opencode_go_bridge.managed_service as managed_service_module
from codex_opencode_go_bridge.managed_service import (
    KeychainStore,
    ManagedBridgeService,
    main,
    print_bridge_token,
    render_launch_agent,
    run_bridge,
)


class FakeRunner:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def __call__(self, args, *, input_text=None):
        self.calls.append((list(args), input_text))
        if self.results:
            return self.results.pop(0)
        return subprocess.CompletedProcess(args, 0, "", "")


class FakeKeychain:
    def __init__(self):
        self.values = {}
        self.deleted = []

    def get(self, account):
        return self.values.get(account)

    def put(self, account, secret):
        self.values[account] = secret

    def delete(self, account):
        self.deleted.append(account)
        self.values.pop(account, None)


class SequenceHealth:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


class KeychainStoreTests(unittest.TestCase):
    def test_put_delegates_to_native_backend_without_starting_a_subprocess(self):
        backend = FakeKeychain()
        store = KeychainStore(backend=backend)

        with patch.object(
            managed_service_module.subprocess,
            "run",
            side_effect=AssertionError("Keychain must not use a subprocess"),
        ):
            store.put("upstream-api-key", "super-secret-value")

        self.assertEqual(backend.values["upstream-api-key"], "super-secret-value")

    def test_get_returns_the_native_backend_value(self):
        backend = FakeKeychain()
        backend.values["bridge-token"] = "local-token-value"
        store = KeychainStore(backend=backend)

        value = store.get("bridge-token")

        self.assertEqual(value, "local-token-value")

    def test_put_failure_raises_a_redacted_error_without_a_leaking_cause(self):
        class FailingBackend:
            def put(self, account, secret):
                raise RuntimeError(f"failed for {secret}")

        store = KeychainStore(backend=FailingBackend())

        with self.assertRaisesRegex(RuntimeError, "could not update Keychain") as caught:
            store.put("upstream-api-key", "super-secret-value")

        self.assertNotIn("super-secret-value", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_delete_delegates_only_the_account_identifier(self):
        backend = FakeKeychain()
        store = KeychainStore(backend=backend)

        store.delete("bridge-token")

        self.assertEqual(backend.deleted, ["bridge-token"])


@unittest.skipUnless(
    os.getenv("CODEX_OPENCODE_RUN_KEYCHAIN_TESTS") == "1",
    "real Keychain test requires explicit opt-in",
)
class RealKeychainStoreTests(unittest.TestCase):
    def test_real_keychain_round_trip_does_not_use_process_arguments_for_the_secret(self):
        store = KeychainStore()
        account = f"integration-test-{secrets.token_hex(8)}"
        value = secrets.token_urlsafe(32)
        try:
            store.put(account, value)
            self.assertEqual(store.get(account), value)
        finally:
            store.delete(account)


class LaunchAgentTests(unittest.TestCase):
    def test_plist_runs_the_managed_service_without_embedding_credentials(self):
        launcher = Path("/Users/test/.codex/opencode-go-subagent/bin/codex-opencode-go-service")

        data = plistlib.loads(
            render_launch_agent(
                launcher=launcher,
                stdout_path=Path("/Users/test/.codex/opencode-go-subagent/logs/bridge.log"),
                stderr_path=Path("/Users/test/.codex/opencode-go-subagent/logs/bridge.err.log"),
            )
        )

        self.assertEqual(data["ProgramArguments"], [str(launcher), "run"])
        self.assertTrue(data["RunAtLoad"])
        self.assertEqual(data["KeepAlive"], {"SuccessfulExit": False})
        serialized = plistlib.dumps(data).decode()
        self.assertNotIn("OPENCODE_GO_API_KEY", serialized)
        self.assertNotIn("CODEX_OPENCODE_BRIDGE_TOKEN", serialized)
        self.assertNotIn("EnvironmentVariables", data)


class ManagedBridgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.codex_home = self.root / ".codex"
        self.user_home = self.root / "home"
        self.keychain = FakeKeychain()

    def test_configure_stores_distinct_credentials_bootstraps_and_reports_no_secrets(self):
        runner = FakeRunner(
            [
                subprocess.CompletedProcess([], 113, "", "not loaded"),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
        )
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=runner,
            health_checker=SequenceHealth(False, True),
            token_factory=lambda: "generated-local-token",
            uid=501,
        )

        report = service.configure("upstream-secret-value")

        self.assertEqual(self.keychain.values["upstream-api-key"], "upstream-secret-value")
        self.assertEqual(self.keychain.values["bridge-token"], "generated-local-token")
        plist_path = (
            self.user_home
            / "Library"
            / "LaunchAgents"
            / "com.shiauweizhao.codex-opencode-go-subagent.plist"
        )
        self.assertTrue(plist_path.is_file())
        self.assertEqual(
            runner.calls[-1][0],
            ["/bin/launchctl", "bootstrap", "gui/501", str(plist_path)],
        )
        serialized = json.dumps(report)
        self.assertNotIn("upstream-secret-value", serialized)
        self.assertNotIn("generated-local-token", serialized)
        self.assertEqual(report["status"], "configured")
        self.assertTrue(report["healthy"])

    def test_configure_refuses_a_healthy_port_not_owned_by_the_launch_agent(self):
        runner = FakeRunner(
            [subprocess.CompletedProcess([], 113, "", "not loaded")]
        )
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=runner,
            health_checker=lambda: True,
            uid=501,
        )

        with self.assertRaisesRegex(RuntimeError, "already occupied"):
            service.configure("upstream-secret-value")

        self.assertEqual(self.keychain.values, {})
        self.assertFalse(service.plist_path.exists())

    def test_configure_rotates_an_existing_local_token(self):
        self.keychain.values["bridge-token"] = "legacy-local-token"
        runner = FakeRunner(
            [
                subprocess.CompletedProcess([], 113, "", "not loaded"),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
        )
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=runner,
            health_checker=SequenceHealth(False, True),
            token_factory=lambda: "rotated-local-token",
            uid=501,
        )

        service.configure("upstream-secret-value")

        self.assertEqual(self.keychain.values["bridge-token"], "rotated-local-token")

    def test_rotate_local_token_restarts_without_returning_either_secret(self):
        self.keychain.values.update(
            {
                "upstream-api-key": "upstream-secret-value",
                "bridge-token": "legacy-local-token",
            }
        )
        runner = FakeRunner(
            [
                subprocess.CompletedProcess([], 0, "loaded", ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
        )
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=runner,
            health_checker=lambda: True,
            token_factory=lambda: "rotated-local-token",
            uid=501,
        )

        report = service.rotate_local_token()

        self.assertEqual(self.keychain.values["bridge-token"], "rotated-local-token")
        self.assertEqual(self.keychain.values["upstream-api-key"], "upstream-secret-value")
        serialized = json.dumps(report)
        self.assertNotIn("rotated-local-token", serialized)
        self.assertNotIn("upstream-secret-value", serialized)
        self.assertEqual(report, {"status": "local_token_rotated", "healthy": True})

    def test_configure_fails_when_service_never_becomes_healthy(self):
        runner = FakeRunner(
            [
                subprocess.CompletedProcess([], 113, "", "not loaded"),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
        )
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=runner,
            health_checker=lambda: False,
            token_factory=lambda: "generated-local-token",
            uid=501,
        )

        with self.assertRaisesRegex(RuntimeError, "did not become healthy"):
            service.configure("upstream-secret-value")

    def test_status_reports_presence_and_health_without_returning_secret_values(self):
        self.keychain.values.update(
            {
                "upstream-api-key": "upstream-secret-value",
                "bridge-token": "local-secret-value",
            }
        )
        runner = FakeRunner([subprocess.CompletedProcess([], 0, "loaded details", "")])
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=runner,
            health_checker=lambda: True,
            uid=501,
        )
        service._write_plist()

        report = service.status()

        self.assertEqual(
            report,
            {
                "configured": True,
                "plist_installed": True,
                "loaded": True,
                "healthy": True,
            },
        )
        serialized = json.dumps(report)
        self.assertNotIn("upstream-secret-value", serialized)
        self.assertNotIn("local-secret-value", serialized)

    def test_uninstall_unloads_service_and_preserves_credentials_by_default(self):
        runner = FakeRunner(
            [
                subprocess.CompletedProcess([], 0, "loaded", ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
        )
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=runner,
            health_checker=lambda: False,
            uid=501,
        )
        service._write_plist()
        self.keychain.values.update(
            {"upstream-api-key": "upstream", "bridge-token": "local"}
        )

        report = service.uninstall(purge_secrets=False)

        self.assertFalse(service.plist_path.exists())
        self.assertEqual(self.keychain.deleted, [])
        self.assertTrue(report["secrets_preserved"])
        self.assertEqual(
            runner.calls[-1][0],
            [
                "/bin/launchctl",
                "bootout",
                "gui/501/com.shiauweizhao.codex-opencode-go-subagent",
            ],
        )

    def test_uninstall_can_explicitly_purge_both_keychain_items(self):
        runner = FakeRunner([subprocess.CompletedProcess([], 113, "", "not loaded")])
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=runner,
            health_checker=lambda: False,
            uid=501,
        )

        report = service.uninstall(purge_secrets=True)

        self.assertEqual(
            self.keychain.deleted,
            ["upstream-api-key", "bridge-token"],
        )
        self.assertFalse(report["secrets_preserved"])

    def test_restart_uses_launchctl_kickstart_for_a_loaded_service(self):
        runner = FakeRunner(
            [
                subprocess.CompletedProcess([], 0, "loaded", ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
        )
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=runner,
            health_checker=lambda: True,
            uid=501,
        )
        service._write_plist()

        report = service.restart()

        self.assertEqual(report, {"status": "restarted", "healthy": True})
        self.assertEqual(
            runner.calls[-1][0],
            [
                "/bin/launchctl",
                "kickstart",
                "-k",
                "gui/501/com.shiauweizhao.codex-opencode-go-subagent",
            ],
        )

    def test_stage_handoff_delegates_to_local_service_without_filesystem_write(self):
        self.keychain.values["bridge-token"] = "local-secret-value"
        calls = []
        service = ManagedBridgeService(
            codex_home=self.codex_home,
            user_home=self.user_home,
            keychain=self.keychain,
            runner=FakeRunner(),
            health_checker=lambda: True,
            handoff_sender=lambda assignment, token: calls.append((assignment, token))
            or {
                "staged": True,
                "handoff_id": "12345678-1234-5678-1234-567812345678",
                "agent_type": "opencode_go_v4_worker",
                "expires_at": "2026-08-12T02:00:00+00:00",
            },
        )

        report = service.stage_handoff("bounded assignment")

        self.assertTrue(report["staged"])
        self.assertEqual(calls, [("bounded assignment", "local-secret-value")])
        self.assertNotIn("bounded assignment", json.dumps(report))


class ServiceEntryPointTests(unittest.TestCase):
    def test_local_handoff_opener_ignores_environment_proxies(self):
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://proxy.invalid:8080",
                "HTTPS_PROXY": "http://proxy.invalid:8080",
                "NO_PROXY": "",
            },
            clear=False,
        ):
            opener = managed_service_module._local_only_opener()

        proxy_handlers = [
            handler
            for handler in opener.handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(proxy_handlers, [])

    def test_local_handoff_client_refuses_redirect_without_leaking_bearer(self):
        class RedirectHandler(BaseHTTPRequestHandler):
            sink_authorizations = []

            def do_POST(self):
                if self.path == "/stage":
                    self.send_response(302)
                    self.send_header("Location", "/sink")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.sink_authorizations.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"staged":true,"agent_type":"opencode_go_v4_worker"}')

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup_server():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(cleanup_server)

        url = f"http://127.0.0.1:{server.server_port}/stage"
        with patch.object(managed_service_module, "HANDOFF_STAGE_URL", url):
            with self.assertRaisesRegex(RuntimeError, "HTTP 302") as caught:
                managed_service_module._send_handoff_to_local_bridge(
                    "bounded assignment",
                    "local-secret",
                )

        self.assertEqual(RedirectHandler.sink_authorizations, [])
        self.assertNotIn("bounded assignment", str(caught.exception))
        self.assertNotIn("local-secret", str(caught.exception))

    def test_local_handoff_client_redacts_untrusted_error_body(self):
        assignment = "sensitive assignment text"
        token = "local-secret"
        body = json.dumps(
            {"error": {"message": f"received {assignment} with Bearer {token}"}}
        ).encode()
        error = managed_service_module.urllib.error.HTTPError(
            managed_service_module.HANDOFF_STAGE_URL,
            409,
            "Conflict",
            {},
            io.BytesIO(body),
        )

        class FailingOpener:
            def open(self, request, timeout):
                raise error

        with patch.object(
            managed_service_module,
            "_local_only_opener",
            return_value=FailingOpener(),
        ):
            with self.assertRaises(RuntimeError) as caught:
                managed_service_module._send_handoff_to_local_bridge(
                    assignment,
                    token,
                )

        self.assertNotIn(assignment, str(caught.exception))
        self.assertNotIn(token, str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_local_handoff_survives_reset_while_reading_error_body(self):
        assignment = "sensitive assignment text"
        token = "local-secret"

        class ResettingBody:
            def __init__(self):
                self.close_calls = 0

            def read(self, size=-1):
                raise ConnectionResetError("connection reset by peer")

            def close(self):
                self.close_calls += 1

        body = ResettingBody()
        error = managed_service_module.urllib.error.HTTPError(
            managed_service_module.HANDOFF_STAGE_URL,
            302,
            "Found",
            {},
            body,
        )

        class FailingOpener:
            def open(self, request, timeout):
                raise error

        with patch.object(
            managed_service_module,
            "_local_only_opener",
            return_value=FailingOpener(),
        ):
            with self.assertRaises(RuntimeError) as caught:
                managed_service_module._send_handoff_to_local_bridge(
                    assignment,
                    token,
                )

        self.assertIn("HTTP 302", str(caught.exception))
        self.assertNotIn(assignment, str(caught.exception))
        self.assertNotIn(token, str(caught.exception))
        self.assertEqual(body.close_calls, 1)

    def test_print_bridge_token_never_prints_the_upstream_key(self):
        keychain = FakeKeychain()
        keychain.values.update(
            {"upstream-api-key": "upstream-secret", "bridge-token": "local-token"}
        )
        output = io.StringIO()

        print_bridge_token(keychain, output)

        self.assertEqual(output.getvalue(), "local-token\n")
        self.assertNotIn("upstream-secret", output.getvalue())

    def test_run_bridge_exposes_credentials_only_while_server_is_running(self):
        keychain = FakeKeychain()
        keychain.values.update(
            {"upstream-api-key": "upstream-secret", "bridge-token": "local-token"}
        )
        environ = {"PRESERVED": "yes"}
        observed = {}

        def bridge_main():
            observed.update(environ)
            return 7

        result = run_bridge(keychain, environ, bridge_main)

        self.assertEqual(result, 7)
        self.assertEqual(observed["OPENCODE_GO_API_KEY"], "upstream-secret")
        self.assertEqual(observed["CODEX_OPENCODE_BRIDGE_TOKEN"], "local-token")
        self.assertEqual(environ, {"PRESERVED": "yes"})

    def test_run_bridge_exposes_and_restores_the_installed_codex_home(self):
        keychain = FakeKeychain()
        keychain.values.update(
            {"upstream-api-key": "upstream-secret", "bridge-token": "local-token"}
        )
        environ = {"CODEX_HOME": "/previous/codex-home"}
        observed = {}

        result = run_bridge(
            keychain,
            environ,
            lambda: observed.update(environ) or 0,
            codex_home=Path("/installed/codex-home"),
        )

        self.assertEqual(result, 0)
        self.assertEqual(observed["CODEX_HOME"], "/installed/codex-home")
        self.assertEqual(environ, {"CODEX_HOME": "/previous/codex-home"})

    def test_status_cli_prints_structured_json(self):
        class FakeService:
            def status(self):
                return {
                    "configured": True,
                    "plist_installed": True,
                    "loaded": True,
                    "healthy": True,
                }

        output = io.StringIO()

        code = main(
            ["status"],
            service_factory=lambda: FakeService(),
            output=output,
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["healthy"], True)

    def test_cli_uses_default_service_factory_when_not_injected(self):
        class FakeService:
            def status(self):
                return {
                    "configured": True,
                    "plist_installed": True,
                    "loaded": True,
                    "healthy": True,
                }

        output = io.StringIO()
        with patch.object(
            managed_service_module,
            "default_service",
            return_value=FakeService(),
        ):
            code = main(["status"], output=output)

        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["healthy"])

    def test_configure_cli_reads_key_with_hidden_reader_and_never_prints_it(self):
        class FakeService:
            def __init__(self):
                self.received = None

            def configure(self, value):
                self.received = value
                return {"status": "configured", "healthy": True}

        service = FakeService()
        prompts = []
        output = io.StringIO()

        code = main(
            ["configure"],
            service_factory=lambda: service,
            secret_reader=lambda prompt: prompts.append(prompt) or "upstream-secret",
            output=output,
        )

        self.assertEqual(code, 0)
        self.assertEqual(service.received, "upstream-secret")
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("upstream-secret", output.getvalue())

    def test_uninstall_cli_requires_explicit_flag_to_purge_secrets(self):
        class FakeService:
            def __init__(self):
                self.purge = None

            def uninstall(self, *, purge_secrets):
                self.purge = purge_secrets
                return {"status": "uninstalled", "secrets_preserved": not purge_secrets}

        service = FakeService()
        output = io.StringIO()

        code = main(
            ["uninstall", "--purge-secrets"],
            service_factory=lambda: service,
            output=output,
        )

        self.assertEqual(code, 0)
        self.assertTrue(service.purge)
        self.assertFalse(json.loads(output.getvalue())["secrets_preserved"])

    def test_doctor_cli_returns_nonzero_when_service_is_not_ready(self):
        class FakeService:
            def doctor(self):
                return {"ok": False, "healthy": False}

        output = io.StringIO()

        code = main(
            ["doctor"],
            service_factory=lambda: FakeService(),
            output=output,
        )

        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_rotate_local_token_cli_uses_the_explicit_service_action(self):
        class FakeService:
            def rotate_local_token(self):
                return {"status": "local_token_rotated", "healthy": True}

        output = io.StringIO()

        code = main(
            ["rotate-local-token"],
            service_factory=lambda: FakeService(),
            output=output,
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "local_token_rotated")

    def test_stage_handoff_cli_reads_assignment_from_stdin_without_echoing_it(self):
        class FakeService:
            def __init__(self):
                self.assignment = None

            def stage_handoff(self, assignment):
                self.assignment = assignment
                return {
                    "staged": True,
                    "handoff_id": "12345678-1234-5678-1234-567812345678",
                    "agent_type": "opencode_go_v4_worker",
                    "expires_at": "2026-08-12T02:00:00+00:00",
                }

        service = FakeService()
        output = io.StringIO()

        code = main(
            ["stage-handoff"],
            service_factory=lambda: service,
            input_stream=io.StringIO("secret-free bounded assignment"),
            output=output,
        )

        self.assertEqual(code, 0)
        self.assertEqual(service.assignment, "secret-free bounded assignment")
        self.assertNotIn("secret-free bounded assignment", output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["staged"])

    def test_stage_handoff_cli_failure_is_controlled_and_does_not_echo_assignment(self):
        assignment = "do not echo failed assignment"

        class FakeService:
            def stage_handoff(self, received):
                raise RuntimeError(f"slot occupied while staging {received}")

        output = io.StringIO()
        errors = io.StringIO()

        code = main(
            ["stage-handoff"],
            service_factory=lambda: FakeService(),
            input_stream=io.StringIO(assignment),
            output=output,
            error_output=errors,
        )

        self.assertEqual(code, 12)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("stage-handoff failed", errors.getvalue())
        self.assertNotIn(assignment, errors.getvalue())


if __name__ == "__main__":
    unittest.main()
