import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_LLM_PROVIDER = "google_ai_studio"
DEFAULT_GOOGLE_MODEL = "gemma-3-27b-it"
DEFAULT_GOOGLE_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 1.5
PROJECT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def _load_project_env_file() -> None:
    if not PROJECT_ENV_PATH.exists():
        return

    for raw_line in PROJECT_ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key.startswith("env:"):
            normalized_key = normalized_key[4:]
        normalized_key = normalized_key.strip()
        normalized_value = value.strip().strip('"').strip("'")
        if normalized_key and normalized_key not in os.environ:
            os.environ[normalized_key] = normalized_value


_load_project_env_file()


def _normalize_model_name(model: str) -> str:
    cleaned = str(model or "").strip()
    alias_map = {
        "gemma3": "gemma-3-27b-it",
        "gemma-3": "gemma-3-27b-it",
    }
    return alias_map.get(cleaned, cleaned)


def _read_retry_count() -> int:
    value = os.getenv("TRAFFIC_LLM_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)).strip()
    try:
        return max(0, int(value))
    except ValueError:
        return DEFAULT_MAX_RETRIES


def _read_retry_backoff_seconds() -> float:
    value = os.getenv("TRAFFIC_LLM_RETRY_BACKOFF_SECONDS", str(DEFAULT_RETRY_BACKOFF_SECONDS)).strip()
    try:
        return max(0.1, float(value))
    except ValueError:
        return DEFAULT_RETRY_BACKOFF_SECONDS


def _is_retryable_http_error(status_code: int) -> bool:
    return status_code in {408, 429, 500, 502, 503, 504}


def get_default_model() -> str:
    return _normalize_model_name(os.getenv("TRAFFIC_LLM_MODEL", DEFAULT_GOOGLE_MODEL))


def get_provider_name() -> str:
    return os.getenv("TRAFFIC_LLM_PROVIDER", DEFAULT_LLM_PROVIDER).strip() or DEFAULT_LLM_PROVIDER


def _require_google_api_key() -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未配置 GEMINI_API_KEY 或 GOOGLE_API_KEY，无法调用 Google AI Studio API")
    return api_key


def _extract_candidate_text(response_payload: dict) -> str:
    candidates = response_payload.get("candidates", []) if isinstance(response_payload, dict) else []
    if not candidates:
        return ""

    content = candidates[0].get("content", {}) if isinstance(candidates[0], dict) else {}
    parts = content.get("parts", []) if isinstance(content, dict) else []
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = str(part.get("text", "") or "").strip()
        if text:
            texts.append(text)
    return "\n".join(texts).strip()


def generate_json_response(
    model: str,
    system_prompt: str,
    user_content: str,
    timeout_seconds: float,
    image_base64: str | None = None,
    image_mime_type: str | None = None,
) -> dict:
    provider = get_provider_name()
    if provider != "google_ai_studio":
        raise RuntimeError(f"当前仅支持 google_ai_studio，收到 provider={provider}")

    api_key = _require_google_api_key()
    api_base_url = os.getenv("GOOGLE_API_BASE_URL", DEFAULT_GOOGLE_API_BASE_URL).rstrip("/")
    resolved_model = _normalize_model_name(model or get_default_model())
    encoded_model = urllib.parse.quote(resolved_model, safe="")
    request_url = f"{api_base_url}/models/{encoded_model}:generateContent?key={api_key}"

    user_parts: list[dict] = []
    if image_base64:
        user_parts.append(
            {
                "inlineData": {
                    "mimeType": image_mime_type or os.getenv("TRAFFIC_IMAGE_MIME_TYPE", "image/jpeg"),
                    "data": image_base64,
                }
            }
        )
    user_parts.append({"text": user_content})

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": user_parts,
            }
        ],
        "generationConfig": {
            "temperature": 0,
        },
    }

    supports_system_instruction = not resolved_model.startswith("gemma-")
    supports_json_mode = not resolved_model.startswith("gemma-")
    if supports_system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_prompt}],
        }
    else:
        payload["contents"][0]["parts"] = [{"text": f"{system_prompt}\n\n{user_content}"}]

    if supports_json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    request = urllib.request.Request(
        request_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    max_retries = _read_retry_count()
    retry_backoff_seconds = _read_retry_backoff_seconds()
    last_error: Exception | None = None
    response_payload: dict = {}

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
                response_payload = json.loads(raw_body) if raw_body else {}
                last_error = None
                break
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
            if attempt < max_retries and _is_retryable_http_error(exc.code):
                time.sleep(retry_backoff_seconds * (attempt + 1))
                continue
            raise RuntimeError(f"Google AI Studio 请求失败: HTTP {exc.code} {error_body}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(retry_backoff_seconds * (attempt + 1))
                continue
            raise RuntimeError(f"Google AI Studio 网络请求失败: {exc}") from exc

    if last_error is not None:
        raise RuntimeError(f"Google AI Studio 网络请求失败: {last_error}") from last_error

    return {
        "provider": provider,
        "model": resolved_model,
        "response_type": type(response_payload).__name__,
        "response_payload": response_payload,
        "content": _extract_candidate_text(response_payload),
    }
