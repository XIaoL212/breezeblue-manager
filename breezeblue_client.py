"""Small, testable client for the BreezeBlue Developer API."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://api.breeze.blue"


class BreezeBlueError(RuntimeError):
    """An upstream error with the stable fields needed by the admin UI."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        code: str = "",
        retry_after: int = 0,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code or 0)
        self.code = str(code or "")
        self.retry_after = max(0, int(retry_after or 0))
        self.payload = payload


def _error_from_response(response: httpx.Response) -> BreezeBlueError:
    try:
        payload = response.json()
    except Exception:
        payload = {"detail": response.text[:800]}
    detail = payload.get("detail") if isinstance(payload, dict) else ""
    message = payload.get("error") if isinstance(payload, dict) else ""
    message = str(message or detail or f"BreezeBlue HTTP {response.status_code}")
    code = str(payload.get("code") or "") if isinstance(payload, dict) else ""
    retry_after = response.headers.get("retry-after", "0")
    try:
        retry_seconds = int(float(retry_after or 0))
    except (TypeError, ValueError):
        retry_seconds = 0
    return BreezeBlueError(
        message[:800],
        status_code=response.status_code,
        code=code,
        retry_after=retry_seconds,
        payload=payload,
    )


async def request(
    api_key: str,
    method: str,
    path: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    json_body: dict | None = None,
    params: dict | None = None,
    data: dict | None = None,
    files: list | None = None,
    headers: dict | None = None,
    timeout: float = 90,
) -> httpx.Response:
    key = str(api_key or "").strip()
    if not key:
        raise BreezeBlueError("BreezeBlue API Key 不能为空", code="AUTH_REQUIRED")
    merged_headers = {"xi-api-key": key, "Authorization": f"Bearer {key}"}
    if headers:
        merged_headers.update(headers)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.request(
                method.upper(),
                str(base_url or DEFAULT_BASE_URL).rstrip("/") + path,
                headers=merged_headers,
                json=json_body,
                params=params,
                data=data,
                files=files,
            )
    except httpx.TimeoutException as exc:
        raise BreezeBlueError("BreezeBlue 请求超时，请稍后重试", status_code=504) from exc
    except httpx.HTTPError as exc:
        raise BreezeBlueError(f"无法连接 BreezeBlue：{exc}") from exc
    if response.status_code >= 400:
        raise _error_from_response(response)
    return response


async def _json_or_empty(api_key: str, path: str, params: dict | None = None):
    """Return the decoded JSON body (dict, list, or scalar) without reshaping."""
    response = await request(api_key, "GET", path, params=params)
    try:
        return response.json()
    except Exception:
        return {}


def normalize_voice(raw: dict) -> dict | None:
    if not isinstance(raw, dict) or not raw.get("voice_id"):
        return None
    origin = str(raw.get("origin") or "").strip()
    voice_type = str(raw.get("voice_type") or "").strip()
    return {
        "voice_id": str(raw.get("voice_id") or "")[:160],
        "name": str(raw.get("name") or raw.get("voice_id") or "未命名音色")[:160],
        "origin": origin[:80],
        "voice_type": voice_type[:80],
        "language_code": str(raw.get("language_code") or "")[:16],
        "gender": str(raw.get("gender") or ""),
        "age": str(raw.get("age") or ""),
        "accent": str(raw.get("accent") or ""),
        "tone": [str(item)[:40] for item in (raw.get("tone") or [])][:3],
        "description": str(raw.get("description") or "")[:800],
        "preview_url": str(raw.get("preview_url") or "")[:1200],
        "is_favorited": bool(raw.get("is_favorited")),
        "is_private": voice_type not in ("default", "public") and origin not in (
            "default", "official", "public",
        ),
    }


def normalize_model(raw: dict) -> dict | None:
    if not isinstance(raw, dict) or not raw.get("model_id"):
        return None
    languages = raw.get("languages")
    language_codes: list[str] = []
    if isinstance(languages, list):
        for item in languages:
            if isinstance(item, dict):
                language_codes.append(
                    str(item.get("language_id") or item.get("name") or "")[:16]
                )
            elif isinstance(item, str):
                language_codes.append(item[:16])
    return {
        "model_id": str(raw.get("model_id") or "")[:160],
        "name": str(raw.get("name") or raw.get("model_id") or "语音模型")[:160],
        "description": str(raw.get("description") or "")[:800],
        "languages": language_codes,
    }


async def account_snapshot(api_key: str, base_url: str = DEFAULT_BASE_URL) -> dict:
    """Fetch the account data the voice console can verify with one API key."""

    async def safe_json(path: str, params: dict | None = None):
        try:
            return await _json_or_empty(api_key, path, params), ""
        except BreezeBlueError as exc:
            return {}, str(exc)

    calls = await asyncio.gather(
        safe_json("/v1/balance"),
        safe_json("/v1/entitlements"),
        safe_json("/v1/usage", {"days": 30, "page_size": 20}),
        safe_json("/v1/models"),
        safe_json("/v1/voices", {"page_size": 100}),
        safe_json("/v1/api-keys/current"),
    )
    balance, entitlements, usage, models, voices, current_key = (
        value[0] for value in calls
    )
    errors = [value[1] for value in calls if value[1]]
    if len(errors) == len(calls):
        raise BreezeBlueError(errors[0] or "BreezeBlue 账户验证失败", status_code=401)
    # /v1/models returns a bare JSON array; /v1/voices returns {"voices": [...]}.
    # Accept both shapes so model/voice counts never silently collapse to zero.
    raw_models = (
        models
        if isinstance(models, list)
        else (
            models.get("models")
            if isinstance(models, dict) and isinstance(models.get("models"), list)
            else []
        )
    )
    raw_voices = (
        voices
        if isinstance(voices, list)
        else (
            voices.get("voices")
            if isinstance(voices, dict) and isinstance(voices.get("voices"), list)
            else []
        )
    )
    normalized_models = [item for item in (normalize_model(v) for v in raw_models) if item]
    normalized_voices = [item for item in (normalize_voice(v) for v in raw_voices) if item]
    return {
        "status": "connected" if not errors else "partial",
        "plan_code": str(entitlements.get("plan_code") or ""),
        "subscription_status": str(entitlements.get("status") or ""),
        "credits": float(balance.get("balance") or 0),
        "monthly_credits": int(entitlements.get("monthly_creative_credits") or 0),
        "max_concurrent_generations": int(
            entitlements.get("max_concurrent_generations") or 0
        ),
        "saved_voice_limit": int(entitlements.get("saved_voice_limit") or 0),
        "commercial_use_allowed": bool(entitlements.get("commercial_use_allowed")),
        "balance": balance,
        "entitlements": entitlements,
        "usage": usage,
        "models": normalized_models,
        "voices": normalized_voices,
        "current_key": current_key.get("api_key") or {},
        "errors": errors,
    }


async def synthesize(
    api_key: str,
    *,
    voice_id: str,
    text: str,
    model_id: str = "",
    language_code: str = "zh",
    instructions: str = "",
    speed: float = 1.0,
    volume: float = 1.0,
    guidance_scale: float = 4.0,
    output_format: str = "mp3",
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    value = str(text or "").strip()
    if not value:
        raise BreezeBlueError("待合成文本不能为空", code="BAD_REQUEST")
    if len(value) > 1000:
        raise BreezeBlueError(
            "单次语音文本超过 1000 字符，请分段后重试",
            status_code=422,
            code="VALIDATION_ERROR",
        )
    body: dict[str, Any] = {
        "text": value,
        "voice_settings": {
            "speed": max(0.5, min(2.0, float(speed or 1.0))),
            "volume": max(0.01, min(2.0, float(volume or 1.0))),
            "guidance_scale": max(1.0, min(10.0, float(guidance_scale or 4.0))),
        },
    }
    if model_id:
        body["model_id"] = model_id
    if language_code and language_code != "auto":
        body["language_code"] = language_code[:2].lower()
    if instructions:
        body["instructions"] = str(instructions)[:4000]
    response = await request(
        api_key,
        "POST",
        f"/v1/text-to-speech/{voice_id}",
        base_url=base_url,
        params={"output_format": output_format or "mp3", "delivery": "sync"},
        json_body=body,
        timeout=180,
    )
    return {
        "audio": response.content,
        "content_type": response.headers.get("content-type", "audio/mpeg").split(";")[0],
        "history_item_id": response.headers.get("history-item-id", ""),
        "api_key_id": response.headers.get("x-breeze-api-key-id", ""),
    }


async def design_preview(
    api_key: str,
    *,
    description: str,
    text: str = "",
    model_id: str = "",
    language_code: str = "zh",
    guidance_scale: float = 4.0,
    preview_count: int = 1,
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    body = {
        "voice_description": str(description or "").strip()[:500],
        "text": str(text or "").strip()[:500] or None,
        "model_id": model_id or None,
        "language_code": language_code[:2].lower() if language_code != "auto" else None,
        "guidance_scale": max(1.0, min(10.0, float(guidance_scale or 4.0))),
        "preview_count": max(1, min(3, int(preview_count or 1))),
    }
    response = await request(
        api_key,
        "POST",
        "/v1/voice-previews/design",
        base_url=base_url,
        params={"output_format": "mp3"},
        json_body=body,
        timeout=240,
    )
    return response.json()


async def clone_preview(
    api_key: str,
    *,
    filename: str,
    raw: bytes,
    mime: str,
    name: str = "",
    description: str = "",
    language_code: str = "",
    remove_background_noise: bool = True,
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise BreezeBlueError(
            "参考音频必须存在且不超过 5 MB",
            status_code=422,
            code="VALIDATION_ERROR",
        )
    data = {
        "name": name[:80],
        "description": description[:1000],
        "reference_language_code": language_code[:2].lower() or None,
        "remove_background_noise": str(bool(remove_background_noise)).lower(),
    }
    response = await request(
        api_key,
        "POST",
        "/v1/voice-previews/clone",
        base_url=base_url,
        params={"output_format": "mp3"},
        data={key: value for key, value in data.items() if value is not None},
        files=[("files", (filename or "reference.mp3", raw, mime or "application/octet-stream"))],
        timeout=300,
    )
    return response.json()


async def localize_preview(
    api_key: str,
    *,
    voice_id: str,
    language_code: str,
    name: str = "",
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    response = await request(
        api_key,
        "POST",
        "/v1/voice-previews/localize",
        base_url=base_url,
        json_body={
            "voice_id": voice_id,
            "language_code": language_code,
            "name": name[:160] or None,
        },
        timeout=300,
    )
    return response.json()


async def save_preview(
    api_key: str,
    *,
    generated_voice_id: str,
    name: str,
    language_code: str,
    description: str = "",
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    response = await request(
        api_key,
        "POST",
        f"/v1/voice-previews/{generated_voice_id}/save",
        base_url=base_url,
        json_body={
            "voice_name": name[:80],
            "voice_description": description[:1000],
            "language_code": language_code,
        },
        timeout=120,
    )
    return response.json()


async def remix_presets(
    api_key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    """List curated Voice Remix presets (age/gender/pitch/pacing/energy/etc.)."""
    response = await request(
        api_key,
        "GET",
        "/v1/voice-previews/remix/presets",
        base_url=base_url,
        timeout=60,
    )
    try:
        value = response.json()
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {"presets": value}


async def remix_prepare(
    api_key: str,
    *,
    voice_id: str,
    prompt: str,
    preset_id: str = "",
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    """Prepare a remix instruction/script once; the token is valid for 24h."""
    body: dict[str, Any] = {
        "voice_id": voice_id,
        "prompt": str(prompt or "").strip()[:1000] or None,
        "preset_id": preset_id[:80] or None,
    }
    body = {key: value for key, value in body.items() if value is not None}
    response = await request(
        api_key,
        "POST",
        "/v1/voice-previews/remix/prepare",
        base_url=base_url,
        json_body=body,
        timeout=180,
    )
    return response.json()


async def create_remix_preview(
    api_key: str,
    *,
    voice_id: str,
    prompt: str = "",
    preset_id: str = "",
    preparation_token: str = "",
    guidance_scale: float = 4.0,
    idempotency_key: str = "",
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    """Submit a Voice Remix candidate. Returns a 202 job with generation_job_id."""
    body: dict[str, Any] = {
        "voice_id": voice_id,
        "prompt": str(prompt or "").strip()[:1000] or None,
        "preset_id": preset_id[:80] or None,
        "preparation_token": preparation_token[:200] or None,
        "guidance_scale": max(1.0, min(10.0, float(guidance_scale or 4.0))),
    }
    body = {key: value for key, value in body.items() if value is not None}
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = str(idempotency_key)[:120]
    response = await request(
        api_key,
        "POST",
        "/v1/voice-previews/remix",
        base_url=base_url,
        json_body=body,
        timeout=180,
        headers=headers,
    )
    return response.json()


async def get_remix_preview(
    api_key: str,
    *,
    generation_job_id: str,
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    """Poll a Voice Remix candidate until status is ready/failed/cancelled."""
    response = await request(
        api_key,
        "GET",
        f"/v1/voice-previews/remix/{generation_job_id}",
        base_url=base_url,
        timeout=60,
    )
    return response.json()


async def get_localize_preview(
    api_key: str,
    *,
    generation_job_id: str,
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    """Poll a localize job (POST /v1/voice-previews/localize returns 202)."""
    response = await request(
        api_key,
        "GET",
        f"/v1/voice-previews/localize/{generation_job_id}",
        base_url=base_url,
        timeout=60,
    )
    return response.json()
