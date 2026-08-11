import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


from codex_opencode_go_bridge.server import (
    BridgeConfig,
    OpenCodeGoClient,
    UpstreamError,
)


class SinkHandler(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):
        self.__class__.requests.append(dict(self.headers))
        data = json.dumps({"choices": [{"message": {"content": "redirected"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.do_GET()

    def log_message(self, format, *args):
        pass


class RedirectHandler(BaseHTTPRequestHandler):
    target = ""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(302)
        self.send_header("Location", self.__class__.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        pass


class OpenCodeGoClientSecurityTests(unittest.TestCase):
    def setUp(self):
        SinkHandler.requests = []
        self.sink = ThreadingHTTPServer(("127.0.0.1", 0), SinkHandler)
        RedirectHandler.target = f"http://127.0.0.1:{self.sink.server_port}/captured"
        self.redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        self.threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (self.sink, self.redirect)
        ]
        for thread in self.threads:
            thread.start()

    def tearDown(self):
        for server in (self.redirect, self.sink):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5)

    def test_does_not_follow_redirects_with_the_upstream_bearer(self):
        config = BridgeConfig(
            upstream_api_key="upstream-secret",
            local_token="local-secret",
            upstream_base=f"http://127.0.0.1:{self.redirect.server_port}/v1",
        )
        client = OpenCodeGoClient(config)

        with self.assertRaises(UpstreamError) as caught:
            client.complete({"model": "deepseek-v4-flash", "messages": [], "stream": False})

        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(SinkHandler.requests, [])

    def test_reports_upstream_timeout_explicitly(self):
        class TimeoutOpener:
            def open(self, request, timeout):
                raise socket.timeout("timed out with upstream-secret")

        config = BridgeConfig(
            upstream_api_key="upstream-secret",
            local_token="local-secret",
        )
        client = OpenCodeGoClient(config)
        client._opener = TimeoutOpener()

        with self.assertRaises(UpstreamError) as caught:
            client.complete({"model": "deepseek-v4-flash", "messages": [], "stream": False})

        self.assertEqual(caught.exception.status, 504)
        self.assertEqual(str(caught.exception), "OpenCode Go request timed out")
        self.assertNotIn("upstream-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
