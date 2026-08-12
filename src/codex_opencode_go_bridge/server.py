"""Localhost Responses API facade backed by OpenCode Go Chat Completions."""

from __future__ import annotations

import hmac
import json
import os
import subprocess
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


class HandoffStageError(RuntimeError):
    """The managed service could not publish the one-shot handoff."""


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
    handoff_script_path: Path = field(
        default_factory=lambda: Path.home()
        / ".codex"
        / "hooks"
        / "codex-opencode-go-subagent"
        / "plaintext_handoff.py"
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
        codex_home = Path(values.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        state_default = codex_home / "opencode-go-subagent" / "state.sqlite3"
        handoff_default = (
            codex_home
            / "hooks"
            / "codex-opencode-go-subagent"
            / "plaintext_handoff.py"
        )
        return cls(
            upstream_api_key=upstream_key,
            local_token=local_token,
            upstream_base=upstream_base,
            host=host,
            port=int(values.get("CODEX_OPENCODE_BRIDGE_PORT", str(DEFAULT_PORT))),
            state_path=Path(values.get("CODEX_OPENCODE_BRIDGE_STATE", str(state_default))).expanduser(),
            handoff_script_path=Path(
                values.get("CODEX_OPENCODE_HANDOFF_SCRIPT", str(handoff_default))
            ).expanduser(),
            timeout_seconds=float(values.get("CODEX_OPENCODE_UPSTREAM_TIMEOUT", "300")),
        )


class Upstream(Protocol):
    def complete(self, payload: JSON) -> JSON: ...


class HandoffStager(Protocol):
    def stage(self, assignment: str) -> JSON: ...


HANDOFF_ENV_ALLOWLIST = (
    "LANG",
    "LC_ALL",
    "PATH",
    "TMPDIR",
)


class SubprocessHandoffStager:
    def __init__(
        self,
        script_path: Path,
        *,
        timeout_seconds: float = 10.0,
        runner=None,
        environ: Mapping[str, str] | None = None,
        redactions: tuple[str, ...] = (),
    ):
        self.script_path = Path(script_path)
        self.timeout_seconds = timeout_seconds
        self.runner = subprocess.run if runner is None else runner
        source_environment = os.environ if environ is None else environ
        self.environment = {
            name: source_environment[name]
            for name in HANDOFF_ENV_ALLOWLIST
            if source_environment.get(name)
        }
        self.redactions = tuple(value for value in redactions if value)

    def stage(self, assignment: str) -> JSON:
        if not assignment.strip():
            raise HandoffStageError("refusing to stage an empty Flash assignment")
        try:
            result = self.runner(
                [sys.executable, str(self.script_path), "--mode", "stage"],
                input=assignment,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                env=self.environment,
            )
        except (OSError, subprocess.SubprocessError):
            raise HandoffStageError("managed plaintext handoff process failed") from None
        if result.returncode != 0:
            message = (result.stderr or "").strip()[:1000]
            for sensitive_value in (assignment, *self.redactions):
                if sensitive_value:
                    message = message.replace(sensitive_value, "[REDACTED]")
            raise HandoffStageError(message or "managed plaintext handoff stage failed")
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise HandoffStageError("managed plaintext handoff returned invalid JSON") from None
        if (
            not isinstance(parsed, dict)
            or parsed.get("staged") is not True
            or parsed.get("agent_type") != "v4_flash_worker"
        ):
            raise HandoffStageError("managed plaintext handoff returned an invalid result")
        return {
            key: parsed[key]
            for key in ("staged", "handoff_id", "agent_type", "expires_at")
            if key in parsed
        }


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


def _latest_tool_call_outputs(value: Any) -> list[JSON]:
    if not isinstance(value, list):
        return []
    output_types = {"function_call_output", "custom_tool_call_output"}
    call_types = {"function_call", "custom_tool_call"}
    outputs = [
        item
        for item in value
        if isinstance(item, dict) and item.get("type") in output_types
    ]
    function_call_indexes = [
        index
        for index, item in enumerate(value)
        if isinstance(item, dict) and item.get("type") in call_types
    ]
    if not function_call_indexes:
        return outputs
    last_call_index = function_call_indexes[-1]
    first_call_index = last_call_index
    while first_call_index > 0:
        previous_item = value[first_call_index - 1]
        if not isinstance(previous_item, dict) or previous_item.get("type") not in call_types:
            break
        first_call_index -= 1
    latest_call_ids = {
        str(item.get("call_id"))
        for item in value[first_call_index : last_call_index + 1]
        if isinstance(item, dict) and item.get("call_id")
    }
    latest_outputs = [
        item
        for item in value[last_call_index + 1 :]
        if isinstance(item, dict)
        and item.get("type") in output_types
        and str(item.get("call_id")) in latest_call_ids
    ]
    return latest_outputs


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
    def __init__(
        self,
        config: BridgeConfig,
        store: SQLiteStateStore,
        upstream: Upstream,
        *,
        handoff_stager: HandoffStager | None = None,
    ):
        self.config = config
        self.store = store
        self.upstream = upstream
        self.handoff_stager = handoff_stager

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
            continuation_items = _latest_tool_call_outputs(body.get("input"))
            if previous is None:
                call_ids = [
                    str(item.get("call_id"))
                    for item in continuation_items
                    if item.get("call_id")
                ]
                if call_ids:
                    previous = self.store.find_by_call_ids(call_ids)
            request_body = body
            if previous is not None and continuation_items:
                request_body = {**body, "input": continuation_items}
            payload, context = build_chat_request(request_body, previous=previous)
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

    def stage_handoff(self, body: JSON, authorization: str | None) -> tuple[int, str, bytes]:
        if not self._authorized(authorization):
            return _json_error(401, "authentication_error", "invalid local bridge token")
        assignment = body.get("assignment")
        if not isinstance(assignment, str) or not assignment.strip():
            return _json_error(
                400,
                "invalid_request_error",
                "handoff assignment must be a non-empty string",
            )
        if self.handoff_stager is None:
            return _json_error(503, "handoff_error", "managed handoff staging is unavailable")
        try:
            result = self.handoff_stager.stage(assignment)
        except HandoffStageError as error:
            return _json_error(409, "handoff_error", str(error))
        except Exception as error:
            print(f"managed handoff internal error: {type(error).__name__}", file=sys.stderr)
            return _json_error(500, "handoff_error", "managed handoff staging failed")
        data = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        return 200, "application/json", data


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
        path = self.path.rstrip("/")
        if path not in {
            "/v1/responses",
            "/internal/handoffs/v4_flash_worker/stage",
        }:
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
        if path == "/internal/handoffs/v4_flash_worker/stage":
            result = self.service.stage_handoff(body, self.headers.get("Authorization"))
        else:
            result = self.service.respond(body, self.headers.get("Authorization"))
        self._send(*result)

    def log_message(self, format: str, *args: Any) -> None:
        if os.getenv("CODEX_OPENCODE_BRIDGE_ACCESS_LOG") == "1":
            super().log_message(format, *args)


def make_server(config: BridgeConfig, service: BridgeService | None = None) -> ThreadingHTTPServer:
    if service is None:
        store = SQLiteStateStore(config.state_path)
        service = BridgeService(
            config,
            store,
            OpenCodeGoClient(config),
            handoff_stager=SubprocessHandoffStager(
                config.handoff_script_path,
                redactions=(config.upstream_api_key, config.local_token),
            ),
        )
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
