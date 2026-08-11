"""Localhost Responses API facade backed by OpenCode Go Chat Completions."""

from __future__ import annotations

import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Protocol

from .protocol import (
    ProtocolError,
    SUPPORTED_MODEL,
    build_chat_request,
    build_responses_result,
    encode_sse,
)
from .state import SQLiteStateStore


JSON = dict[str, Any]
DEFAULT_UPSTREAM_BASE = "https://opencode.ai/zen/go/v1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4141
MAX_REQUEST_BYTES = 8 * 1024 * 1024


class ConfigError(RuntimeError):
    pass


class UpstreamError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class BridgeConfig:
    upstream_api_key: str = field(repr=False)
    local_token: str = field(repr=False)
    upstream_base: str = DEFAULT_UPSTREAM_BASE
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    state_path: Path = field(
        default_factory=lambda: Path.home()
        / ".codex"
        / "opencode-go-subagent"
        / "state.sqlite3"
    )
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ConfigError("bridge host must be loopback")
        if not self.upstream_api_key or not self.local_token:
            raise ConfigError("both upstream and local tokens are required")
        if hmac.compare_digest(self.upstream_api_key, self.local_token):
            raise ConfigError("upstream and local tokens must differ")
        upstream_url = urllib.parse.urlsplit(self.upstream_base)
        if not upstream_url.hostname or upstream_url.username or upstream_url.password:
            raise ConfigError("OPENCODE_GO_BASE_URL must be an absolute URL without userinfo")
        if upstream_url.scheme != "https" and not (
            upstream_url.scheme == "http"
            and upstream_url.hostname in {"127.0.0.1", "::1", "localhost"}
        ):
            raise ConfigError("OPENCODE_GO_BASE_URL must use HTTPS unless it targets loopback")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "BridgeConfig":
        values = os.environ if environ is None else environ
        upstream_key = values.get("OPENCODE_GO_API_KEY", "").strip()
        local_token = values.get("CODEX_OPENCODE_BRIDGE_TOKEN", "").strip()
        if not upstream_key:
            raise ConfigError("OPENCODE_GO_API_KEY is required")
        if not local_token:
            raise ConfigError("CODEX_OPENCODE_BRIDGE_TOKEN is required")
        if hmac.compare_digest(upstream_key, local_token):
            raise ConfigError("OPENCODE_GO_API_KEY and CODEX_OPENCODE_BRIDGE_TOKEN must differ")
        host = values.get("CODEX_OPENCODE_BRIDGE_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ConfigError("bridge host must be loopback")
        upstream_base = values.get("OPENCODE_GO_BASE_URL", DEFAULT_UPSTREAM_BASE).rstrip("/")
        state_default = Path.home() / ".codex" / "opencode-go-subagent" / "state.sqlite3"
        return cls(
            upstream_api_key=upstream_key,
            local_token=local_token,
            upstream_base=upstream_base,
            host=host,
            port=int(values.get("CODEX_OPENCODE_BRIDGE_PORT", str(DEFAULT_PORT))),
            state_path=Path(values.get("CODEX_OPENCODE_BRIDGE_STATE", str(state_default))).expanduser(),
            timeout_seconds=float(values.get("CODEX_OPENCODE_UPSTREAM_TIMEOUT", "300")),
        )


class Upstream(Protocol):
    def complete(self, payload: JSON) -> JSON: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenCodeGoClient:
    def __init__(self, config: BridgeConfig):
        self.url = f"{config.upstream_base}/chat/completions"
        self.api_key = config.upstream_api_key
        self.timeout_seconds = config.timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect())

    def complete(self, payload: JSON) -> JSON:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "codex-opencode-go-subagent/0.1",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_REQUEST_BYTES + 1)
        except TimeoutError:
            raise UpstreamError(504, "OpenCode Go request timed out") from None
        except urllib.error.HTTPError as error:
            try:
                raw = error.read(64 * 1024)
            finally:
                error.close()
            message = _safe_upstream_message(raw, self.api_key) or f"OpenCode Go returned HTTP {error.code}"
            raise UpstreamError(error.code, message) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise UpstreamError(504, "OpenCode Go request timed out") from None
            raise UpstreamError(502, "OpenCode Go connection failed") from None
        if len(raw) > MAX_REQUEST_BYTES:
            raise UpstreamError(502, "OpenCode Go response exceeded size limit")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise UpstreamError(502, "OpenCode Go returned invalid JSON") from error
        if not isinstance(parsed, dict):
            raise UpstreamError(502, "OpenCode Go returned a non-object response")
        return parsed


def _safe_upstream_message(raw: bytes, secret: str) -> str:
    text = raw.decode("utf-8", errors="replace")[:4096]
    if secret:
        text = text.replace(secret, "[REDACTED]")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "")[:1000]
    return str(payload.get("message") or "")[:1000]


def _json_error(status: int, error_type: str, message: str) -> tuple[int, str, bytes]:
    payload = {
        "error": {
            "type": error_type,
            "message": message,
        }
    }
    return status, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8")


class BridgeService:
    def __init__(self, config: BridgeConfig, store: SQLiteStateStore, upstream: Upstream):
        self.config = config
        self.store = store
        self.upstream = upstream

    def _authorized(self, authorization: str | None) -> bool:
        if authorization is None:
            return False
        expected = f"Bearer {self.config.local_token}"
        return hmac.compare_digest(authorization, expected)

    def respond(self, body: JSON, authorization: str | None) -> tuple[int, str, bytes]:
        if not self._authorized(authorization):
            return _json_error(401, "authentication_error", "invalid local bridge token")
        try:
            previous = None
            previous_id = body.get("previous_response_id")
            if previous_id:
                previous = self.store.get(str(previous_id))
            if previous is None:
                call_ids = [
                    str(item.get("call_id"))
                    for item in body.get("input") or []
                    if isinstance(item, dict) and item.get("type") == "function_call_output"
                ] if isinstance(body.get("input"), list) else []
                if call_ids:
                    previous = self.store.find_by_call_ids(call_ids)
            payload, context = build_chat_request(body, previous=previous)
            upstream_response = self.upstream.complete(payload)
            response, state = build_responses_result(body, upstream_response, context)
            self.store.put(response["id"], state)
            return 200, "text/event-stream", encode_sse(response)
        except ProtocolError as error:
            return _json_error(400, "invalid_request_error", str(error))
        except UpstreamError as error:
            status = error.status if 400 <= error.status < 500 or error.status == 504 else 502
            return _json_error(status, "upstream_error", str(error))
        except Exception as error:
            print(f"bridge internal error: {type(error).__name__}", file=sys.stderr)
            return _json_error(500, "bridge_error", "local bridge failed")


class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "CodexOpenCodeGoBridge/0.1"

    @property
    def service(self) -> BridgeService:
        return self.server.bridge_service  # type: ignore[attr-defined]

    def _send(self, status: int, content_type: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/healthz":
            data = json.dumps({"status": "ok"}, separators=(",", ":")).encode()
            self._send(200, "application/json", data)
            return
        if self.path.rstrip("/") == "/v1/models":
            payload = {
                "object": "list",
                "data": [{"id": SUPPORTED_MODEL, "object": "model", "owned_by": "opencode-go"}],
            }
            self._send(200, "application/json", json.dumps(payload, separators=(",", ":")).encode())
            return
        self._send(*_json_error(404, "not_found", "endpoint not found"))

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/responses":
            self._send(*_json_error(404, "not_found", "endpoint not found"))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(*_json_error(400, "invalid_request_error", "invalid Content-Length"))
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send(*_json_error(413, "invalid_request_error", "request body size is invalid"))
            return
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._send(*_json_error(400, "invalid_request_error", "request body is not valid JSON"))
            return
        if not isinstance(body, dict):
            self._send(*_json_error(400, "invalid_request_error", "request body must be an object"))
            return
        result = self.service.respond(body, self.headers.get("Authorization"))
        self._send(*result)

    def log_message(self, format: str, *args: Any) -> None:
        if os.getenv("CODEX_OPENCODE_BRIDGE_ACCESS_LOG") == "1":
            super().log_message(format, *args)


def make_server(config: BridgeConfig, service: BridgeService | None = None) -> ThreadingHTTPServer:
    if service is None:
        store = SQLiteStateStore(config.state_path)
        service = BridgeService(config, store, OpenCodeGoClient(config))
    server = ThreadingHTTPServer((config.host, config.port), _BridgeHandler)
    server.bridge_service = service  # type: ignore[attr-defined]
    server.daemon_threads = True
    return server


def main() -> int:
    try:
        config = BridgeConfig.from_env()
    except (ConfigError, ValueError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    server = make_server(config)
    print(f"codex-opencode-go bridge listening on http://{config.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
