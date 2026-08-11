import json
import tempfile
import unittest
from pathlib import Path


from codex_opencode_go_bridge.server import BridgeConfig, BridgeService, ConfigError
from codex_opencode_go_bridge.state import SQLiteStateStore


class FakeUpstream:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def complete(self, payload):
        self.requests.append(payload)
        return self.response


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

    @staticmethod
    def _completed_response(data):
        for line in data.decode().splitlines():
            if line.startswith("data: {") and '"type":"response.completed"' in line:
                return json.loads(line[6:])["response"]
        raise AssertionError("response.completed not found")


if __name__ == "__main__":
    unittest.main()
