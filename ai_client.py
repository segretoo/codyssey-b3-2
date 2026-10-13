"""AI API 를 REST(HTTP POST + JSON)로 직접 호출하는 얇은 클라이언트. 표준 라이브러리만 쓴다.

지원 provider
- openai    : OpenAI 호환 Chat Completions (`--base-url` 로 다른 호환 서버도 사용 가능)
- anthropic : Anthropic Messages API
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from safety import mask_text

DEFAULT_MODELS = {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-5-5"}
DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}
PROVIDERS = tuple(DEFAULT_MODELS)


class AIError(Exception):
    """AI API 호출 실패. '오류 원인'을 사람이 읽는 문장으로 담는다."""


@dataclass
class AIConfig:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    api_key: str
    base_url: str
    timeout: float = 60.0


_HTTP_HINTS = {
    400: "요청 형식 또는 파라미터 오류 (모델명/temperature/max_tokens 확인)",
    401: "인증 실패 (API Key 가 잘못되었거나 만료됨)",
    403: "권한 없음 (해당 모델/리전 접근 불가 또는 키 권한 부족)",
    404: "엔드포인트 또는 모델을 찾을 수 없음 (--model, --base-url 확인)",
    408: "요청 시간 초과",
    429: "요청 한도 초과 또는 크레딧/쿼터 부족 (잠시 후 재시도하거나 결제 상태 확인)",
}


class AIClient:
    def __init__(self, cfg: AIConfig):
        if cfg.provider not in PROVIDERS:
            raise AIError(f"지원하지 않는 provider: {cfg.provider} (지원: {', '.join(PROVIDERS)})")
        self.cfg = cfg
        self.calls = 0  # 실제로 전송한 요청 횟수 (비용 관리용 로그)

    # ---- public -------------------------------------------------------
    def generate(self, system: str, user: str) -> str:
        url, headers, payload = self._build_request(system, user)
        data = self._post(url, headers, payload)
        text = self._extract_text(data)
        if not text.strip():
            raise AIError("AI 응답이 비어 있습니다. (max_tokens 가 너무 작거나 모델이 내용을 생성하지 못함)")
        return text

    # ---- request building --------------------------------------------
    def _build_request(self, system: str, user: str) -> tuple[str, dict, dict]:
        c = self.cfg
        base = c.base_url.rstrip("/")
        if c.provider == "anthropic":
            return (
                f"{base}/messages",
                {
                    "x-api-key": c.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                {
                    "model": c.model,
                    "max_tokens": c.max_tokens,  # Anthropic 은 필수 파라미터
                    "temperature": c.temperature,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
        return (
            f"{base}/chat/completions",
            {"Authorization": f"Bearer {c.api_key}", "Content-Type": "application/json"},
            {
                "model": c.model,
                "temperature": c.temperature,
                "max_tokens": c.max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )

    # ---- transport ----------------------------------------------------
    def _post(self, url: str, headers: dict, payload: dict) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raise AIError(self._http_message(e)) from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise AIError(f"응답 시간 초과 ({self.cfg.timeout:.0f}초)") from e
            raise AIError(f"네트워크 오류: {e.reason}") from e
        except (socket.timeout, TimeoutError) as e:
            raise AIError(f"응답 시간 초과 ({self.cfg.timeout:.0f}초)") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise AIError("응답을 JSON 으로 해석하지 못했습니다. (--base-url 이 올바른지 확인)") from e

    def _http_message(self, e: urllib.error.HTTPError) -> str:
        hint = _HTTP_HINTS.get(e.code) or ("AI 서버 오류 (잠시 후 재시도)" if e.code >= 500 else "요청 실패")
        detail = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                err = json.loads(raw).get("error")
                detail = err.get("message", "") if isinstance(err, dict) else str(err or "")
            except (json.JSONDecodeError, AttributeError):
                detail = raw
        except Exception:  # 본문을 못 읽어도 상태 코드 안내는 해야 한다
            pass
        finally:
            e.close()  # HTTPError 가 들고 있는 임시 파일 정리
        detail = mask_text(detail.strip())[0][:200]  # 에러 본문에 키 일부가 echo 되는 경우 대비
        return f"HTTP {e.code} - {hint}" + (f" / 서버 메시지: {detail}" if detail else "")

    # ---- response parsing --------------------------------------------
    def _extract_text(self, data: dict) -> str:
        try:
            if self.cfg.provider == "anthropic":
                return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            content = data["choices"][0]["message"]["content"]
            return content or ""
        except (KeyError, IndexError, TypeError) as e:
            raise AIError("예상한 형식이 아닌 응답입니다. (--provider / --base-url 확인)") from e
