import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


from codex_opencode_go_bridge.server import BridgeConfig, BridgeService, make_server
from codex_opencode_go_bridge.state import SQLiteStateStore


class FakeUpstream:
    def __init__(self):
        self.requests = []

    def complete(self, payload):
        self.requests.append(payload)
        return {"choices": [{"message": {"content": "from fake upstream"}}]}


class FakeHandoffStager:
    def __init__(self):
        self.assignments = []

    def stage(self, assignment):
        self.assignments.append(assignment)
        return {
            "staged": True,
            "handoff_id": "12345678-1234-5678-1234-567812345678",
            "agent_type": "opencode_go_v4_worker",
            "expires_at": "2026-08-12T02:00:00+00:00",
        }


class HTTPServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = BridgeConfig(
            upstream_api_key="upstream-secret",
            local_token="local-secret",
            host="127.0.0.1",
            port=0,
        )
        self.upstream = FakeUpstream()
        self.stager = FakeHandoffStager()
        service = BridgeService(
            config,
            SQLiteStateStore(Path(self.tmp.name) / "state.sqlite3"),
            self.upstream,
            handoff_stager=self.stager,
        )
        self.server = make_server(config, service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.server.bridge_service.store.close()
        self.tmp.cleanup()

    def test_health_and_models_do_not_expose_tokens(self):
        health = urllib.request.urlopen(f"{self.base}/healthz", timeout=2).read()
        models = urllib.request.urlopen(f"{self.base}/v1/models", timeout=2).read()

        self.assertEqual(json.loads(health), {"status": "ok"})
        self.assertEqual(json.loads(models)["data"][0]["id"], "deepseek-v4-flash")
        self.assertNotIn(b"secret", health + models)

    def test_responses_endpoint_requires_bearer_and_returns_sse(self):
        payload = json.dumps({"model": "deepseek-v4-flash", "input": "hello"}).encode()
        unauthorized = urllib.request.Request(
            f"{self.base}/v1/responses",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(unauthorized, timeout=2)
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

        authorized = urllib.request.Request(
            f"{self.base}/v1/responses",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer local-secret",
            },
        )
        with urllib.request.urlopen(authorized, timeout=2) as response:
            data = response.read()
            content_type = response.headers.get_content_type()
        self.assertEqual(content_type, "text/event-stream")
        self.assertIn(b"from fake upstream", data)

    def test_managed_handoff_stage_requires_bearer_and_never_calls_upstream(self):
        assignment = "bounded coding assignment\nmarker=managed-stage"
        payload = json.dumps({"assignment": assignment}).encode()
        unauthorized = urllib.request.Request(
            f"{self.base}/internal/handoffs/opencode_go_v4_worker/stage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(unauthorized, timeout=2)
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()

        authorized = urllib.request.Request(
            f"{self.base}/internal/handoffs/opencode_go_v4_worker/stage",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer local-secret",
            },
        )
        with urllib.request.urlopen(authorized, timeout=2) as response:
            result = json.loads(response.read())

        self.assertTrue(result["staged"])
        self.assertEqual(result["agent_type"], "opencode_go_v4_worker")
        self.assertEqual(self.stager.assignments, [assignment])
        self.assertEqual(self.upstream.requests, [])


if __name__ == "__main__":
    unittest.main()
