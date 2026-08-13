"""Managed macOS lifecycle for the localhost bridge."""

from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
import plistlib
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, MutableMapping, TextIO


KEYCHAIN_SERVICE = "com.shiauweizhao.codex-opencode-go-subagent"
LAUNCH_AGENT_LABEL = KEYCHAIN_SERVICE
UPSTREAM_ACCOUNT = "upstream-api-key"
BRIDGE_ACCOUNT = "bridge-token"
_MISSING = object()
HANDOFF_STAGE_URL = "http://127.0.0.1:4141/internal/handoffs/v4_flash_worker/stage"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _local_only_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )


def _default_runner(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


class _MacOSKeychainBackend:
    NOT_FOUND = -25300

    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("macOS Keychain is available only on macOS")
        self.security = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )
        self.core_foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        self.security.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainAddGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self.security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.security.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
        self.security.SecKeychainItemDelete.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        self.core_foundation.CFRelease.restype = None

    def _find(self, account: str) -> tuple[int, bytes | None, ctypes.c_void_p]:
        service = KEYCHAIN_SERVICE.encode("utf-8")
        account_bytes = account.encode("utf-8")
        password_length = ctypes.c_uint32()
        password_data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        status = self.security.SecKeychainFindGenericPassword(
            None,
            len(service),
            service,
            len(account_bytes),
            account_bytes,
            ctypes.byref(password_length),
            ctypes.byref(password_data),
            ctypes.byref(item),
        )
        value = None
        if status == 0:
            value = ctypes.string_at(password_data, password_length.value)
        if password_data:
            self.security.SecKeychainItemFreeContent(None, password_data)
        return status, value, item

    def get(self, account: str) -> str | None:
        status, value, item = self._find(account)
        try:
            if status == self.NOT_FOUND:
                return None
            if status != 0 or value is None:
                raise RuntimeError(f"Keychain read failed with OSStatus {status}")
            return value.decode("utf-8")
        finally:
            if item:
                self.core_foundation.CFRelease(item)

    def put(self, account: str, secret: str) -> None:
        status, _value, item = self._find(account)
        secret_bytes = secret.encode("utf-8")
        secret_buffer = ctypes.create_string_buffer(secret_bytes)
        try:
            if status == 0:
                update_status = self.security.SecKeychainItemModifyAttributesAndData(
                    item,
                    None,
                    len(secret_bytes),
                    ctypes.cast(secret_buffer, ctypes.c_void_p),
                )
            elif status == self.NOT_FOUND:
                service = KEYCHAIN_SERVICE.encode("utf-8")
                account_bytes = account.encode("utf-8")
                created_item = ctypes.c_void_p()
                update_status = self.security.SecKeychainAddGenericPassword(
                    None,
                    len(service),
                    service,
                    len(account_bytes),
                    account_bytes,
                    len(secret_bytes),
                    ctypes.cast(secret_buffer, ctypes.c_void_p),
                    ctypes.byref(created_item),
                )
                if created_item:
                    self.core_foundation.CFRelease(created_item)
            else:
                raise RuntimeError(f"Keychain lookup failed with OSStatus {status}")
            if update_status != 0:
                raise RuntimeError(f"Keychain update failed with OSStatus {update_status}")
        finally:
            ctypes.memset(secret_buffer, 0, len(secret_buffer))
            if item:
                self.core_foundation.CFRelease(item)

    def delete(self, account: str) -> None:
        status, _value, item = self._find(account)
        try:
            if status == self.NOT_FOUND:
                return
            if status != 0:
                raise RuntimeError(f"Keychain lookup failed with OSStatus {status}")
            delete_status = self.security.SecKeychainItemDelete(item)
            if delete_status != 0:
                raise RuntimeError(f"Keychain delete failed with OSStatus {delete_status}")
        finally:
            if item:
                self.core_foundation.CFRelease(item)


def render_launch_agent(*, launcher: Path, stdout_path: Path, stderr_path: Path) -> bytes:
    payload = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [str(launcher), "run"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "ThrottleInterval": 5,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def _required_secret(keychain, account: str) -> str:
    value = keychain.get(account)
    if not value:
        raise RuntimeError(f"required Keychain item {account!r} is missing")
    return value


def print_bridge_token(keychain, output: TextIO) -> None:
    output.write(f"{_required_secret(keychain, BRIDGE_ACCOUNT)}\n")


def run_bridge(
    keychain,
    environ: MutableMapping[str, str],
    bridge_main: Callable[[], int],
    *,
    codex_home: Path | None = None,
) -> int:
    updates = {
        "OPENCODE_GO_API_KEY": _required_secret(keychain, UPSTREAM_ACCOUNT),
        "CODEX_OPENCODE_BRIDGE_TOKEN": _required_secret(keychain, BRIDGE_ACCOUNT),
    }
    if codex_home is not None:
        updates["CODEX_HOME"] = str(codex_home)
    previous = {name: environ.get(name, _MISSING) for name in updates}
    environ.update(updates)
    try:
        return bridge_main()
    finally:
        for name, value in previous.items():
            if value is _MISSING:
                environ.pop(name, None)
            else:
                environ[name] = value  # type: ignore[assignment]


class KeychainStore:
    def __init__(self, *, backend=None):
        self.backend = _MacOSKeychainBackend() if backend is None else backend

    def put(self, account: str, secret: str) -> None:
        try:
            self.backend.put(account, secret)
        except Exception:
            raise RuntimeError(
                f"could not update Keychain item {account!r}"
            ) from None

    def get(self, account: str) -> str | None:
        try:
            return self.backend.get(account)
        except Exception:
            raise RuntimeError(f"could not read Keychain item {account!r}") from None

    def delete(self, account: str) -> None:
        try:
            self.backend.delete(account)
        except Exception:
            raise RuntimeError(f"could not delete Keychain item {account!r}") from None


class ManagedBridgeService:
    def __init__(
        self,
        *,
        codex_home: Path,
        user_home: Path,
        keychain,
        runner=_default_runner,
        health_checker: Callable[[], bool],
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        uid: int | None = None,
        health_attempts: int = 1,
        health_delay_seconds: float = 0,
        sleeper: Callable[[float], None] = time.sleep,
        handoff_sender=None,
    ):
        self.codex_home = Path(codex_home)
        self.user_home = Path(user_home)
        self.keychain = keychain
        self.runner = runner
        self.health_checker = health_checker
        self.token_factory = token_factory
        self.uid = os.getuid() if uid is None else uid
        self.health_attempts = max(1, health_attempts)
        self.health_delay_seconds = max(0, health_delay_seconds)
        self.sleeper = sleeper
        self.handoff_sender = _send_handoff_to_local_bridge if handoff_sender is None else handoff_sender
        self.domain = f"gui/{self.uid}"
        self.plist_path = (
            self.user_home / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
        )
        self.launcher = (
            self.codex_home / "opencode-go-subagent" / "bin" / "codex-opencode-go-service"
        )
        self.logs_dir = self.codex_home / "opencode-go-subagent" / "logs"

    def _new_local_token(self, upstream_api_key: str) -> str:
        local_token = self.token_factory()
        if not local_token or secrets.compare_digest(local_token, upstream_api_key):
            raise RuntimeError("could not generate a distinct local bridge token")
        return local_token

    def _write_plist(self) -> None:
        self.plist_path.parent.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        payload = render_launch_agent(
            launcher=self.launcher,
            stdout_path=self.logs_dir / "bridge.log",
            stderr_path=self.logs_dir / "bridge.err.log",
        )
        self.plist_path.write_bytes(payload)
        os.chmod(self.plist_path, 0o600)

    def _start(self, *, loaded: bool | None = None) -> None:
        target = f"{self.domain}/{LAUNCH_AGENT_LABEL}"
        if loaded is None:
            loaded = self._loaded()
        command = (
            ["/bin/launchctl", "kickstart", "-k", target]
            if loaded
            else ["/bin/launchctl", "bootstrap", self.domain, str(self.plist_path)]
        )
        result = self.runner(command, input_text=None)
        if result.returncode != 0:
            raise RuntimeError("could not start managed bridge service")

    def _loaded(self) -> bool:
        target = f"{self.domain}/{LAUNCH_AGENT_LABEL}"
        return (
            self.runner(["/bin/launchctl", "print", target], input_text=None).returncode
            == 0
        )

    def configure(self, upstream_api_key: str) -> dict[str, object]:
        upstream_api_key = upstream_api_key.strip()
        if not upstream_api_key:
            raise ValueError("OpenCode Go API key must not be empty")
        loaded = self._loaded()
        if not loaded and self.health_checker():
            raise RuntimeError(
                "localhost bridge port is already occupied by an unmanaged process"
            )
        local_token = self._new_local_token(upstream_api_key)
        self.keychain.put(UPSTREAM_ACCOUNT, upstream_api_key)
        self.keychain.put(BRIDGE_ACCOUNT, local_token)
        self._write_plist()
        self._start(loaded=loaded)
        healthy = self._wait_for_health()
        if not healthy:
            raise RuntimeError("managed bridge service did not become healthy")
        return {
            "status": "configured",
            "healthy": healthy,
            "plist": str(self.plist_path),
        }

    def rotate_local_token(self) -> dict[str, object]:
        upstream_api_key = _required_secret(self.keychain, UPSTREAM_ACCOUNT)
        self.keychain.put(
            BRIDGE_ACCOUNT,
            self._new_local_token(upstream_api_key),
        )
        lifecycle = self.start()
        return {
            "status": "local_token_rotated",
            "healthy": bool(lifecycle.get("healthy")),
        }

    def status(self) -> dict[str, bool]:
        configured = bool(
            self.keychain.get(UPSTREAM_ACCOUNT) and self.keychain.get(BRIDGE_ACCOUNT)
        )
        loaded = self._loaded()
        return {
            "configured": configured,
            "plist_installed": self.plist_path.is_file(),
            "loaded": loaded,
            "healthy": bool(loaded and self.health_checker()),
        }

    def _stop(self) -> None:
        if not self._loaded():
            return
        target = f"{self.domain}/{LAUNCH_AGENT_LABEL}"
        result = self.runner(
            ["/bin/launchctl", "bootout", target], input_text=None
        )
        if result.returncode != 0:
            raise RuntimeError("could not stop managed bridge service")

    def uninstall(self, *, purge_secrets: bool = False) -> dict[str, object]:
        self._stop()
        try:
            self.plist_path.unlink()
        except FileNotFoundError:
            pass
        if purge_secrets:
            self.keychain.delete(UPSTREAM_ACCOUNT)
            self.keychain.delete(BRIDGE_ACCOUNT)
        return {
            "status": "uninstalled",
            "secrets_preserved": not purge_secrets,
        }

    def restart(self) -> dict[str, object]:
        self._start()
        healthy = self._wait_for_health()
        if not healthy:
            raise RuntimeError("managed bridge service did not become healthy")
        return {"status": "restarted", "healthy": True}

    def _wait_for_health(self) -> bool:
        for attempt in range(self.health_attempts):
            if self.health_checker():
                return True
            if attempt + 1 < self.health_attempts:
                self.sleeper(self.health_delay_seconds)
        return False

    def install(self) -> dict[str, object]:
        _required_secret(self.keychain, UPSTREAM_ACCOUNT)
        _required_secret(self.keychain, BRIDGE_ACCOUNT)
        self._write_plist()
        return self.restart() | {"status": "installed"}

    def start(self) -> dict[str, object]:
        if not self.plist_path.is_file():
            return self.install()
        return self.restart() | {"status": "started"}

    def stop(self) -> dict[str, object]:
        self._stop()
        return {"status": "stopped"}

    def doctor(self) -> dict[str, object]:
        report: dict[str, object] = self.status()
        report["ok"] = all(report.values())
        return report

    def stage_handoff(self, assignment: str) -> dict[str, object]:
        if not assignment.strip():
            raise ValueError("Flash handoff assignment must not be empty")
        token = _required_secret(self.keychain, BRIDGE_ACCOUNT)
        result = self.handoff_sender(assignment, token)
        if (
            not isinstance(result, dict)
            or result.get("staged") is not True
            or result.get("agent_type") != "v4_flash_worker"
        ):
            raise RuntimeError("managed handoff staging returned an invalid result")
        return result


def _safe_local_error(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace")[:4096])
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), dict):
        return ""
    return str(payload["error"].get("message") or "")[:1000]


def _send_handoff_to_local_bridge(assignment: str, token: str) -> dict[str, object]:
    payload = json.dumps(
        {"assignment": assignment},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        HANDOFF_STAGE_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with _local_only_opener().open(request, timeout=10) as response:
            raw = response.read(64 * 1024 + 1)
    except urllib.error.HTTPError as error:
        try:
            try:
                raw = error.read(64 * 1024)
            except OSError:
                raw = b""
        finally:
            error.close()
        message = _safe_local_error(raw) or f"local handoff service returned HTTP {error.code}"
        for sensitive_value in (assignment, token):
            if sensitive_value:
                message = message.replace(sensitive_value, "[REDACTED]")
        raise RuntimeError(f"managed handoff staging failed: {message}") from None
    except (OSError, urllib.error.URLError):
        raise RuntimeError("managed handoff staging service is unavailable") from None
    if len(raw) > 64 * 1024:
        raise RuntimeError("managed handoff staging response exceeded size limit")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("managed handoff staging returned invalid JSON") from None
    if not isinstance(result, dict):
        raise RuntimeError("managed handoff staging returned a non-object response")
    return result


def _localhost_health() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:4141/healthz", timeout=0.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def service_for_codex_home(codex_home: Path) -> ManagedBridgeService:
    if sys.platform != "darwin":
        raise RuntimeError("managed bridge service currently supports macOS only")
    return ManagedBridgeService(
        codex_home=Path(codex_home).expanduser(),
        user_home=Path.home(),
        keychain=KeychainStore(),
        health_checker=_localhost_health,
        health_attempts=50,
        health_delay_seconds=0.1,
    )


def default_service() -> ManagedBridgeService:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    return service_for_codex_home(codex_home)


def main(
    argv: list[str] | None = None,
    *,
    service_factory=None,
    secret_reader=getpass.getpass,
    input_stream=None,
    output=None,
    error_output=None,
) -> int:
    parser = argparse.ArgumentParser(description="Manage the OpenCode Go bridge service")
    parser.add_argument(
        "action",
        choices=(
            "configure",
            "install",
            "start",
            "stop",
            "restart",
            "rotate-local-token",
            "status",
            "doctor",
            "uninstall",
            "print-bridge-token",
            "stage-handoff",
            "run",
        ),
    )
    parser.add_argument("--purge-secrets", action="store_true")
    args = parser.parse_args(argv)
    service = (default_service if service_factory is None else service_factory)()
    destination = sys.stdout if output is None else output
    error_destination = sys.stderr if error_output is None else error_output
    if args.action == "print-bridge-token":
        print_bridge_token(service.keychain, destination)
        return 0
    if args.action == "run":
        from .server import main as bridge_main

        return run_bridge(
            service.keychain,
            os.environ,
            bridge_main,
            codex_home=service.codex_home,
        )
    if args.action == "stage-handoff":
        source = sys.stdin if input_stream is None else input_stream
        assignment = source.read()
        try:
            report = service.stage_handoff(assignment)
        except (RuntimeError, ValueError) as error:
            message = str(error)
            if assignment:
                message = message.replace(assignment, "[REDACTED]")
            print(f"stage-handoff failed: {message}", file=error_destination)
            return 12
        print(json.dumps(report, ensure_ascii=False, indent=2), file=destination)
        return 0
    if args.action == "configure":
        report = service.configure(secret_reader("OpenCode Go API key: "))
    elif args.action == "uninstall":
        report = service.uninstall(purge_secrets=args.purge_secrets)
    else:
        report = getattr(service, args.action.replace("-", "_"))()
    print(json.dumps(report, ensure_ascii=False, indent=2), file=destination)
    return 1 if args.action == "doctor" and not report.get("ok") else 0


if __name__ == "__main__":
    raise SystemExit(main())
