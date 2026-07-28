from __future__ import annotations

import json
import platform
import socket
import ssl
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

try:
    import websocket
except ImportError:  # pragma: no cover - packaged dependency
    websocket = None

try:
    import certifi
except ImportError:  # pragma: no cover - requests already bundles it in packaged builds
    certifi = None


@dataclass
class RemoteAgentConfig:
    schema_version: int = 2
    enabled: bool = True
    gateway_url: str = "https://ai.lhksoul.com"
    device_id: str = ""
    device_name: str = ""
    agent_token: str = ""
    pairing_password: str = ""

    def normalized(self) -> "RemoteAgentConfig":
        self.schema_version = 2
        self.gateway_url = self.gateway_url.strip().rstrip("/") or "https://ai.lhksoul.com"
        self.device_id = self.device_id.strip() or str(uuid.uuid4())
        self.device_name = self.device_name.strip() or socket.gethostname() or platform.node() or "Blog Helper PC"
        self.agent_token = self.agent_token.strip()
        self.pairing_password = self.pairing_password.strip()
        return self


class RemoteAgentConfigStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "remote_agent.json"

    def load(self) -> RemoteAgentConfig:
        if not self.path.exists():
            config = RemoteAgentConfig().normalized()
            self.save(config)
            return config
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        schema_version = int(payload.get("schema_version") or 1)
        legacy_needs_pairing = schema_version < 2 and not str(payload.get("agent_token") or "").strip()
        config = RemoteAgentConfig(
            schema_version=2,
            enabled=True if legacy_needs_pairing else bool(payload.get("enabled", True)),
            gateway_url=str(payload.get("gateway_url") or "https://ai.lhksoul.com"),
            device_id=str(payload.get("device_id") or ""),
            device_name=str(payload.get("device_name") or ""),
            agent_token=str(payload.get("agent_token") or ""),
            pairing_password=str(payload.get("pairing_password") or ""),
        ).normalized()
        self.save(config)
        return config

    def save(self, config: RemoteAgentConfig) -> None:
        config.normalized()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)


class RemoteControlAgent:
    def __init__(
        self,
        config: RemoteAgentConfig,
        app_version: str,
        on_job: Callable[[dict], None],
        on_status: Callable[[str, str], None],
        on_credentials: Callable[[str], None] | None = None,
        on_command: Callable[[dict], None] | None = None,
    ) -> None:
        self.config = config.normalized()
        self.app_version = app_version
        self.on_job = on_job
        self.on_status = on_status
        self.on_credentials = on_credentials
        self.on_command = on_command
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket = None
        self._send_lock = threading.Lock()
        self._active_job_id = ""
        # A deleted PC stays hidden only for this process session. Restarting the
        # app creates a new session and lets the gateway register it again.
        self._session_id = str(uuid.uuid4())

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop_event.is_set())

    def start(self) -> None:
        if self.is_running:
            return
        if websocket is None:
            self.on_status("error", "원격 연결 모듈(websocket-client)이 설치되지 않았습니다.")
            return
        if not self.config.enabled:
            self.on_status("disabled", "원격 제어가 꺼져 있습니다.")
            return
        if not self.config.agent_token and not self.config.pairing_password:
            self.on_status("error", "관리 비밀번호를 한 번 입력해 이 PC를 등록해 주세요.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="blog-helper-remote-agent")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        active_socket = self._socket
        self._socket = None
        if active_socket is not None:
            try:
                active_socket.close()
            except Exception:
                pass

    def accept_job(self, job_id: str, message: str = "PC에서 작업을 시작합니다.") -> None:
        self._active_job_id = job_id
        self._send_job_event("job.accepted", job_id, progress=0.03, message=message)

    def progress(self, job_id: str, progress: float, message: str) -> None:
        self._send_job_event("job.progress", job_id, progress=max(0.0, min(progress, 0.99)), message=message)

    def complete(self, job_id: str, result: dict | None = None, message: str = "작업을 완료했습니다.") -> None:
        self._send_job_event("job.completed", job_id, progress=1.0, message=message, result=result or {})
        if self._active_job_id == job_id:
            self._active_job_id = ""

    def fail(self, job_id: str, message: str) -> None:
        self._send_job_event("job.failed", job_id, progress=0.0, message=message)
        if self._active_job_id == job_id:
            self._active_job_id = ""

    def send_queue_snapshot(self, items: list[dict]) -> None:
        self._send(
            {
                "type": "queue.snapshot",
                "deviceId": self.config.device_id,
                "items": list(items or []),
                "updatedAt": int(time.time() * 1000),
            }
        )

    def command_result(self, command_id: str, ok: bool, message: str) -> None:
        self._send(
            {
                "type": "command.result",
                "commandId": str(command_id or ""),
                "ok": bool(ok),
                "message": str(message or ""),
                "updatedAt": int(time.time() * 1000),
            }
        )

    def notify_published(
        self,
        *,
        job_id: str = "",
        queue_id: str = "",
        published_url: str,
        title: str = "",
    ) -> None:
        url = str(published_url or "").strip()
        if not url:
            return
        self._send(
            {
                "type": "queue.published",
                "jobId": str(job_id or "").strip(),
                "queueId": str(queue_id or "").strip(),
                "publishedUrl": url,
                "title": str(title or "").strip(),
                "updatedAt": int(time.time() * 1000),
            }
        )

    def _run(self) -> None:
        retry_seconds = 2
        while not self._stop_event.is_set():
            if not self.config.agent_token and not self.config.pairing_password:
                self.on_status("error", "관리 비밀번호를 다시 입력해 이 PC를 등록해 주세요.")
                return
            try:
                if not self.config.agent_token:
                    self._pair_device()
                self.on_status("connecting", "원격 서버에 연결 중입니다...")
                socket_url = self._agent_socket_url()
                headers = [f"Authorization: Bearer {self.config.agent_token}"]
                socket_app = websocket.WebSocketApp(
                    socket_url,
                    header=headers,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._socket = socket_app
                ssl_options = {"ca_certs": certifi.where()} if certifi is not None else None
                socket_app.run_forever(
                    ping_interval=25,
                    ping_timeout=10,
                    sslopt=ssl_options,
                )
            except Exception as exc:
                self.on_status("error", f"원격 연결 오류: {exc}")
            finally:
                self._socket = None
            if self._stop_event.wait(retry_seconds):
                break
            retry_seconds = min(retry_seconds * 2, 30)

    def _pair_device(self) -> None:
        self.on_status("connecting", f"{self.config.device_name} PC를 원격 서버에 등록 중입니다...")
        endpoint = f"{self.config.gateway_url}/api/agent/pair"
        payload = json.dumps(
            {
                "password": self.config.pairing_password,
                "deviceId": self.config.device_id,
                "name": self.config.device_name,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"BlogHelper/{self.app_version}",
            },
            method="POST",
        )
        ssl_context = ssl.create_default_context(cafile=certifi.where()) if certifi is not None else None
        try:
            with urlopen(request, timeout=18, context=ssl_context) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401:
                self.config.pairing_password = ""
                raise RuntimeError("관리 비밀번호가 올바르지 않습니다.") from exc
            raise
        device_token = str(result.get("deviceToken") or "").strip()
        if not device_token:
            raise RuntimeError("PC 전용 인증키를 발급받지 못했습니다.")
        self.config.agent_token = device_token
        self.config.pairing_password = ""
        if self.on_credentials is not None:
            self.on_credentials(device_token)

    def _agent_socket_url(self) -> str:
        parsed = urlparse(self.config.gateway_url)
        scheme = "wss" if parsed.scheme in ("", "https", "wss") else "ws"
        query = urlencode(
            {
                "deviceId": self.config.device_id,
                "name": self.config.device_name,
                "platform": f"{platform.system()} {platform.release()}",
                "version": self.app_version,
                "sessionId": self._session_id,
            }
        )
        return urlunparse((scheme, parsed.netloc, "/api/agent", "", query, ""))

    def _on_open(self, _socket) -> None:
        self.on_status("online", f"{self.config.device_name} 원격 연결 완료")
        self._send(
            {
                "type": "ready",
                "deviceId": self.config.device_id,
                "name": self.config.device_name,
                "version": self.app_version,
                "sessionId": self._session_id,
            }
        )

    def _on_message(self, _socket, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except (TypeError, json.JSONDecodeError):
            return
        if payload.get("type") == "ping":
            self._send({"type": "pong", "timestamp": int(time.time() * 1000)})
            return
        message_type = str(payload.get("type") or "")
        if message_type.startswith("queue."):
            if self.on_command is not None:
                try:
                    self.on_command(payload)
                except Exception as exc:
                    self.command_result(
                        str(payload.get("commandId") or ""),
                        False,
                        f"원격 대기열 명령 전달 실패: {exc}",
                    )
            return
        if message_type != "job":
            return
        job_id = str(payload.get("id") or "").strip()
        keyword = str(payload.get("keyword") or "").strip()
        if not job_id or not keyword:
            if job_id:
                self.fail(job_id, "키워드가 비어 있어 작업을 시작할 수 없습니다.")
            return
        try:
            self.on_job(payload)
        except Exception as exc:
            self.fail(job_id, f"원격 작업 전달 실패: {exc}")

    def _on_error(self, _socket, error) -> None:
        if not self._stop_event.is_set():
            self.on_status("error", f"원격 연결 오류: {error}")

    def _on_close(self, _socket, _status_code, _message) -> None:
        if not self._stop_event.is_set():
            self.on_status("offline", "원격 연결이 끊어져 재연결을 시도합니다.")

    def _send_job_event(
        self,
        event_type: str,
        job_id: str,
        *,
        progress: float,
        message: str,
        result: dict | None = None,
    ) -> None:
        payload = {
            "type": event_type,
            "jobId": job_id,
            "progress": progress,
            "message": message,
        }
        if result is not None:
            payload["result"] = result
        self._send(payload)

    def _send(self, payload: dict) -> None:
        active_socket = self._socket
        if active_socket is None:
            return
        encoded = json.dumps(payload, ensure_ascii=False)
        try:
            with self._send_lock:
                active_socket.send(encoded)
        except Exception:
            pass
