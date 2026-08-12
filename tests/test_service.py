import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


from codex_opencode_go_bridge.server import (
    BridgeConfig,
    BridgeService,
    ConfigError,
    HandoffStageError,
    SubprocessHandoffStager,
    UpstreamError,
)
from codex_opencode_go_bridge.state import SQLiteStateStore


class FakeUpstream:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def complete(self, payload):
        self.requests.append(payload)
        return self.response


class TimeoutUpstream:
    def complete(self, payload):
        raise UpstreamError(504, "OpenCode Go request timed out")


class BridgeConfigTests(unittest.TestCase):
    def test_from_env_requires_both_tokens_and_rejects_token_reuse(self):
        with self.assertRaisesRegex(ConfigError, "OPENCODE_GO_API_KEY"):
            BridgeConfig.from_env({"CODEX_OPENCODE_BRIDGE_TOKEN": "local"})
        with self.assertRaisesRegex(ConfigError, "CODEX_OPENCODE_BRIDGE_TOKEN"):
            BridgeConfig.from_env({"OPENCODE_GO_API_KEY": "upstream"})
        with self.assertRaisesRegex(ConfigError, "must differ"):
            BridgeConfig.from_env(
                {
                    "OPENCODE_GO_API_KEY": "same",
                    "CODEX_OPENCODE_BRIDGE_TOKEN": "same",
                }
            )

    def test_repr_does_not_expose_tokens(self):
        config = BridgeConfig(upstream_api_key="upstream-secret", local_token="local-secret")
        rendered = repr(config)
        self.assertNotIn("upstream-secret", rendered)
        self.assertNotIn("local-secret", rendered)

    def test_from_env_rejects_plain_http_upstream_except_loopback(self):
        common = {
            "OPENCODE_GO_API_KEY": "upstream",
            "CODEX_OPENCODE_BRIDGE_TOKEN": "local",
        }
        with self.assertRaisesRegex(ConfigError, "HTTPS"):
            BridgeConfig.from_env({**common, "OPENCODE_GO_BASE_URL": "http://example.com/v1"})
        local = BridgeConfig.from_env(
            {**common, "OPENCODE_GO_BASE_URL": "http://127.0.0.1:9999/v1"}
        )
        self.assertEqual(local.upstream_base, "http://127.0.0.1:9999/v1")

    def test_direct_config_rejects_non_loopback_listener(self):
        with self.assertRaisesRegex(ConfigError, "loopback"):
            BridgeConfig(
                upstream_api_key="upstream",
                local_token="local",
                host="0.0.0.0",
            )

    def test_direct_config_rejects_insecure_remote_upstream(self):
        with self.assertRaisesRegex(ConfigError, "HTTPS"):
            BridgeConfig(
                upstream_api_key="upstream",
                local_token="local",
                upstream_base="http://example.com/v1",
            )


class SubprocessHandoffStagerTests(unittest.TestCase):
    def test_passes_assignment_only_through_stdin_and_returns_sanitized_metadata(self):
        calls = []
        assignment = "bounded coding assignment\nmarker=managed-stage"

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {
                        "staged": True,
                        "handoff_id": "12345678-1234-5678-1234-567812345678",
                        "agent_type": "v4_flash_worker",
                        "expires_at": "2026-08-12T02:00:00+00:00",
                        "pending_path": "/private/state/pending.json",
                    }
                ),
                "",
            )

        stager = SubprocessHandoffStager(
            Path("/installed/plaintext_handoff.py"),
            runner=runner,
            environ={
                "PATH": "/usr/bin:/bin",
                "XDG_STATE_HOME": "/must/not/be/inherited",
                "CODEX_DEEPSEEK_HANDOFF_DIR": "/must/not/be/inherited",
                "OPENCODE_GO_API_KEY": "upstream-secret",
                "CODEX_OPENCODE_BRIDGE_TOKEN": "local-secret",
            },
        )

        report = stager.stage(assignment)

        args, kwargs = calls[0]
        self.assertEqual(
            args,
            [
                sys.executable,
                "/installed/plaintext_handoff.py",
                "--mode",
                "stage",
            ],
        )
        self.assertEqual(kwargs["input"], assignment)
        self.assertTrue(kwargs["text"])
        self.assertTrue(kwargs["capture_output"])
        self.assertFalse(kwargs["check"])
        self.assertEqual(
            kwargs["env"],
            {"PATH": "/usr/bin:/bin"},
        )
        self.assertNotIn("pending_path", report)
        self.assertNotIn(assignment, json.dumps(report))

    def test_failure_never_echoes_the_assignment(self):
        assignment = "do not echo this bounded assignment"

        def runner(args, **kwargs):
            return subprocess.CompletedProcess(
                args,
                12,
                "",
                f"could not stage {assignment} with upstream-secret or local-secret",
            )

        stager = SubprocessHandoffStager(
            Path("/installed/plaintext_handoff.py"),
            runner=runner,
            environ={
                "OPENCODE_GO_API_KEY": "upstream-secret",
                "CODEX_OPENCODE_BRIDGE_TOKEN": "local-secret",
            },
            redactions=("upstream-secret", "local-secret"),
        )

        with self.assertRaises(HandoffStageError) as caught:
            stager.stage(assignment)

        self.assertNotIn(assignment, str(caught.exception))
        self.assertNotIn("upstream-secret", str(caught.exception))
        self.assertNotIn("local-secret", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))


class BridgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SQLiteStateStore(Path(self.tmp.name) / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.config = BridgeConfig(upstream_api_key="upstream-secret", local_token="local-secret")

    def test_rejects_missing_local_bearer_without_calling_upstream(self):
        upstream = FakeUpstream({"choices": [{"message": {"content": "unused"}}]})
        service = BridgeService(self.config, self.store, upstream)

        status, content_type, data = service.respond(
            {"model": "deepseek-v4-flash", "input": "hello"}, authorization=None
        )

        self.assertEqual(status, 401)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(json.loads(data)["error"]["type"], "authentication_error")
        self.assertEqual(upstream.requests, [])

    def test_preserves_explicit_gateway_timeout_status(self):
        service = BridgeService(self.config, self.store, TimeoutUpstream())

        status, content_type, data = service.respond(
            {"model": "deepseek-v4-flash", "input": "hello"},
            authorization="Bearer local-secret",
        )

        self.assertEqual(status, 504)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(
            json.loads(data)["error"]["message"],
            "OpenCode Go request timed out",
        )

    def test_proxies_text_response_as_responses_sse_and_persists_state(self):
        upstream = FakeUpstream(
            {
                "choices": [{"message": {"content": "done"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )
        service = BridgeService(self.config, self.store, upstream)

        status, content_type, data = service.respond(
            {"model": "deepseek-v4-flash", "input": "hello", "stream": True},
            authorization="Bearer local-secret",
        )

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "text/event-stream")
        self.assertIn(b"response.completed", data)
        self.assertEqual(upstream.requests[0]["model"], "deepseek-v4-flash")
        response_id = self._completed_response(data)["id"]
        self.assertEqual(self.store.get(response_id)["messages"][-1]["content"], "done")

    def test_maps_structured_safe_patch_to_canonical_exec_command(self):
        patch = (
            "*** Begin Patch\n"
            "*** Add File: quoted.txt\n"
            "+$(touch SENTINEL) ' \" `touch SENTINEL` ; & |\n"
            "*** End Patch"
        )
        upstream = FakeUpstream(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_safe_patch",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch",
                                        "arguments": json.dumps(
                                            {"patch": patch, "workdir": "/tmp/authorized repo"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )
        service = BridgeService(self.config, self.store, upstream)

        status, _, data = service.respond(
            {
                "model": "deepseek-v4-flash",
                "input": "implement",
                "tools": [
                    {
                        "type": "function",
                        "name": "exec_command",
                        "parameters": {
                            "type": "object",
                            "properties": {"cmd": {"type": "string"}},
                            "required": ["cmd"],
                        },
                    }
                ],
            },
            authorization="Bearer local-secret",
        )

        self.assertEqual(status, 200)
        upstream_tool_names = {
            item["function"]["name"] for item in upstream.requests[0]["tools"]
        }
        self.assertEqual(upstream_tool_names, {"exec_command", "apply_patch"})
        response = self._completed_response(data)
        call = response["output"][0]
        arguments = json.loads(call["arguments"])
        self.assertEqual(call["name"], "exec_command")
        self.assertEqual(arguments["workdir"], "/tmp/authorized repo")
        self.assertEqual(arguments["cmd"], f"apply_patch {shlex.quote(patch)}")
        self.assertEqual(shlex.split(arguments["cmd"]), ["apply_patch", patch])
        state = self.store.get(response["id"])
        self.assertEqual(state["tool_types"]["apply_patch"], "safe_exec_apply_patch")

    def test_continues_tool_result_using_previous_response_state(self):
        first = FakeUpstream(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "inspect",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "exec", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        )
        service = BridgeService(self.config, self.store, first)
        _, _, first_data = service.respond(
            {
                "model": "deepseek-v4-flash",
                "input": "inspect",
                "tools": [
                    {
                        "type": "function",
                        "name": "exec",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
            },
            authorization="Bearer local-secret",
        )
        first_id = self._completed_response(first_data)["id"]

        second = FakeUpstream({"choices": [{"message": {"content": "final"}}]})
        service.upstream = second
        status, _, second_data = service.respond(
            {
                "model": "deepseek-v4-flash",
                "previous_response_id": first_id,
                "input": [
                    {"type": "function_call_output", "call_id": "call_1", "output": "ok"}
                ],
            },
            authorization="Bearer local-secret",
        )

        self.assertEqual(status, 200)
        self.assertEqual(second.requests[0]["messages"][-1]["role"], "tool")
        self.assertEqual(second.requests[0]["messages"][-2]["reasoning_content"], "inspect")
        self.assertEqual(self._completed_response(second_data)["output"][0]["content"][0]["text"], "final")

    def test_continues_tool_result_after_bridge_store_reopens(self):
        first = FakeUpstream(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_after_restart",
                                    "type": "function",
                                    "function": {"name": "exec", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        )
        service = BridgeService(self.config, self.store, first)
        _, _, first_data = service.respond(
            {
                "model": "deepseek-v4-flash",
                "input": "inspect",
                "tools": [
                    {
                        "type": "function",
                        "name": "exec",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
            },
            authorization="Bearer local-secret",
        )
        first_id = self._completed_response(first_data)["id"]
        state_path = self.store.path
        self.store.close()

        reopened_store = SQLiteStateStore(state_path)
        self.addCleanup(reopened_store.close)
        second = FakeUpstream({"choices": [{"message": {"content": "continued"}}]})
        restarted_service = BridgeService(self.config, reopened_store, second)

        status, _, second_data = restarted_service.respond(
            {
                "model": "deepseek-v4-flash",
                "previous_response_id": first_id,
                "input": [
                    {
                        "type": "function_call_output",
                        "call_id": "call_after_restart",
                        "output": "ok",
                    }
                ],
            },
            authorization="Bearer local-secret",
        )

        self.assertEqual(status, 200)
        self.assertEqual(second.requests[0]["messages"][-1]["role"], "tool")
        self.assertEqual(
            self._completed_response(second_data)["output"][0]["content"][0]["text"],
            "continued",
        )

    def test_continues_latest_tool_group_when_codex_resends_full_history(self):
        first = FakeUpstream(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_a",
                                    "type": "function",
                                    "function": {"name": "exec", "arguments": "{}"},
                                },
                                {
                                    "id": "call_b",
                                    "type": "function",
                                    "function": {"name": "exec", "arguments": "{}"},
                                },
                            ],
                        }
                    }
                ]
            }
        )
        service = BridgeService(self.config, self.store, first)
        initial_message = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "inspect"}],
        }
        status, _, _ = service.respond(
            {"model": "deepseek-v4-flash", "input": [initial_message]},
            authorization="Bearer local-secret",
        )
        self.assertEqual(status, 200)

        first_history = [
            initial_message,
            {"type": "reasoning"},
            {"type": "function_call", "call_id": "call_a", "name": "exec"},
            {"type": "function_call", "call_id": "call_b", "name": "exec"},
            {"type": "function_call_output", "call_id": "call_a", "output": "a"},
            {"type": "function_call_output", "call_id": "call_b", "output": "b"},
        ]
        second = FakeUpstream(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_c",
                                    "type": "function",
                                    "function": {"name": "exec", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        )
        service.upstream = second
        status, _, _ = service.respond(
            {"model": "deepseek-v4-flash", "input": first_history},
            authorization="Bearer local-secret",
        )
        self.assertEqual(status, 200)

        third = FakeUpstream({"choices": [{"message": {"content": "done"}}]})
        service.upstream = third
        status, _, data = service.respond(
            {
                "model": "deepseek-v4-flash",
                "input": first_history
                + [
                    {"type": "reasoning"},
                    {
                        "type": "function_call",
                        "call_id": "call_c",
                        "name": "exec",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_c",
                        "output": "c",
                    },
                ],
            },
            authorization="Bearer local-secret",
        )

        self.assertEqual(status, 200)
        self.assertEqual(third.requests[0]["messages"][-1]["tool_call_id"], "call_c")
        self.assertEqual(
            sum(
                message.get("role") == "user" and message.get("content") == "inspect"
                for message in third.requests[0]["messages"]
            ),
            1,
        )
        self.assertEqual(
            self._completed_response(data)["output"][0]["content"][0]["text"],
            "done",
        )

    def test_continues_latest_custom_tool_group_when_codex_resends_full_history(self):
        first = FakeUpstream(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_patch",
                                    "type": "function",
                                    "function": {
                                        "name": "apply_patch",
                                        "arguments": '{"patch":"*** Begin Patch\\n*** End Patch"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )
        service = BridgeService(self.config, self.store, first)
        initial_message = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "implement"}],
        }
        tools = [
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "Apply a patch.",
            }
        ]
        status, _, _ = service.respond(
            {"model": "deepseek-v4-flash", "input": [initial_message], "tools": tools},
            authorization="Bearer local-secret",
        )
        self.assertEqual(status, 200)

        final = FakeUpstream({"choices": [{"message": {"content": "done"}}]})
        service.upstream = final
        status, _, data = service.respond(
            {
                "model": "deepseek-v4-flash",
                "input": [
                    initial_message,
                    {
                        "type": "custom_tool_call",
                        "call_id": "call_patch",
                        "name": "apply_patch",
                        "input": "*** Begin Patch\n*** End Patch",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_patch",
                        "output": "Done!",
                    },
                ],
            },
            authorization="Bearer local-secret",
        )

        self.assertEqual(status, 200)
        self.assertEqual(final.requests[0]["messages"][-1]["tool_call_id"], "call_patch")
        self.assertEqual(
            self._completed_response(data)["output"][0]["content"][0]["text"],
            "done",
        )

    def test_does_not_replay_an_older_output_for_an_unfinished_latest_call(self):
        first = FakeUpstream(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_old",
                                    "type": "function",
                                    "function": {"name": "exec", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        )
        service = BridgeService(self.config, self.store, first)
        initial_message = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "inspect"}],
        }
        status, _, _ = service.respond(
            {"model": "deepseek-v4-flash", "input": [initial_message]},
            authorization="Bearer local-secret",
        )
        self.assertEqual(status, 200)

        should_not_run = FakeUpstream({"choices": [{"message": {"content": "wrong"}}]})
        service.upstream = should_not_run
        status, _, _ = service.respond(
            {
                "model": "deepseek-v4-flash",
                "input": [
                    initial_message,
                    {"type": "function_call", "call_id": "call_old", "name": "exec"},
                    {
                        "type": "function_call_output",
                        "call_id": "call_old",
                        "output": "old",
                    },
                    {"type": "function_call", "call_id": "call_new", "name": "exec"},
                ],
            },
            authorization="Bearer local-secret",
        )

        self.assertEqual(status, 400)
        self.assertEqual(should_not_run.requests, [])

    @staticmethod
    def _completed_response(data):
        for line in data.decode().splitlines():
            if line.startswith("data: {") and '"type":"response.completed"' in line:
                return json.loads(line[6:])["response"]
        raise AssertionError("response.completed not found")


if __name__ == "__main__":
    unittest.main()
