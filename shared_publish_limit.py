"""Cross-device Tistory publish quota backed by the existing remote gateway."""

from __future__ import annotations

import hashlib
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from app_updater import APP_VERSION
from remote_control import RemoteAgentConfig

SHARED_TISTORY_HOST = "tip.lhksoul.com"
SHARED_TISTORY_DAILY_LIMIT = 15

try:
    import certifi
except ImportError:  # pragma: no cover - bundled by requests in packaged builds
    certifi = None


def tistory_shared_blog_key(blog_url: str) -> str:
    """Use the public blog host, not a profile name or editor URL."""
    value = str(blog_url or "").strip()
    if not value:
        raise RuntimeError("공유 발행 횟수를 사용하려면 티스토리 블로그 주소를 먼저 저장해 주세요.")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host in {"tistory.com", "www.tistory.com", "accounts.kakao.com"}:
        raise RuntimeError("공유 발행 횟수에 사용할 티스토리 공개 블로그 주소가 올바르지 않습니다.")
    return hashlib.sha256(f"tistory\0{host}".encode("utf-8")).hexdigest()


def is_shared_tistory_blog(blog_url: str) -> bool:
    """Only the family's explicitly selected blog uses a cross-device quota."""
    value = str(blog_url or "").strip()
    if not value:
        return False
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return (parsed.hostname or "").lower().rstrip(".") == SHARED_TISTORY_HOST


class SharedTistoryPublishLimitClient:
    def __init__(self, config: RemoteAgentConfig, blog_url: str, limit: int, local_count: int) -> None:
        self.config = config
        self.key = tistory_shared_blog_key(blog_url)
        self.limit = int(limit)
        self.local_count = int(local_count)
        if self.limit < 1:
            raise RuntimeError("공유 발행 횟수를 사용하려면 티스토리 하루 발행 한도를 1 이상으로 설정해 주세요.")
        if not config.agent_token or not config.device_id:
            raise RuntimeError(
                "tip.lhksoul.com 공유 발행 횟수 확인을 위해 원격 서버 등록이 필요합니다. "
                "환경설정 > 원격 제어에서 두 PC를 같은 서버에 등록해 주세요."
            )
        parsed = urlparse(config.gateway_url)
        if parsed.scheme != "https":
            raise RuntimeError("공유 발행 횟수 서버는 HTTPS 주소여야 합니다.")

    def _request(self, action: str, reservation_id: str = "", reservation_date: str = "") -> dict:
        endpoint = (
            f"{self.config.gateway_url.rstrip('/')}/api/publish-limit?"
            + urlencode({"deviceId": self.config.device_id})
        )
        payload = json.dumps(
            {
                "action": action,
                "key": self.key,
                "limit": self.limit,
                "localCount": self.local_count,
                "reservationId": reservation_id,
                "reservationDate": reservation_date,
            }
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.config.agent_token}",
                "Content-Type": "application/json",
                "User-Agent": f"BlogHelper/{APP_VERSION}",
            },
            method="POST",
        )
        context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
        try:
            with urlopen(request, timeout=12, context=context) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except (ValueError, UnicodeDecodeError):
                detail = ""
            if exc.code == 409 and detail:
                raise RuntimeError(detail) from exc
            raise RuntimeError(
                f"공유 발행 횟수 서버 오류({exc.code}): {detail or '잠시 후 다시 시도해 주세요.'}"
            ) from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise RuntimeError(
                "공유 발행 횟수 서버에 연결할 수 없어 안전을 위해 티스토리 발행을 중단했습니다."
            ) from exc
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError("공유 발행 횟수 서버의 응답을 확인할 수 없습니다.")
        return result

    def status(self) -> dict:
        return self._request("status")

    def reserve(self) -> dict:
        result = self._request("reserve")
        if not result.get("reservationId"):
            raise RuntimeError("공유 발행 자리를 예약하지 못해 티스토리 발행을 중단했습니다.")
        return result

    def commit(self, reservation_id: str, reservation_date: str) -> dict:
        return self._request("commit", reservation_id, reservation_date)

    def cancel(self, reservation_id: str, reservation_date: str) -> dict:
        return self._request("cancel", reservation_id, reservation_date)
