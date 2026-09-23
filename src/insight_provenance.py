"""Allowlisted generation settings; never persist credentials or endpoint URLs."""

import hashlib

from src.errors import ValidationError

INSIGHT_PROMPT_VERSION = "review-insights-v5"


def validate_profile(profile):
    fields = {"provider", "model", "prompt_version", "reasoning_effort", "endpoint_sha256"}
    if not isinstance(profile, dict) or set(profile) != fields:
        raise ValidationError("인사이트 생성 정보 형식이 올바르지 않습니다.")
    for name in ("provider", "model", "prompt_version"):
        if not isinstance(profile[name], str) or not profile[name].strip() or len(profile[name]) > 200:
            raise ValidationError("인사이트 생성 정보 필드를 확인하세요.")
    if profile["reasoning_effort"] not in (None, "none", "minimal", "low", "medium", "high", "xhigh", "max"):
        raise ValidationError("인사이트 추론 설정을 확인하세요.")
    digest = profile["endpoint_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValidationError("인사이트 서버 식별 정보를 확인하세요.")
    return dict(profile)


def generation_profile(options, prompt_version):
    endpoint = (options.base_url or "https://api.openai.com/v1").rstrip("/")
    return validate_profile({"provider": options.provider, "model": options.model,
                             "prompt_version": prompt_version, "reasoning_effort": options.reasoning_effort,
                             "endpoint_sha256": hashlib.sha256(endpoint.encode()).hexdigest()})


def public_profile(profile):
    if profile is None:
        return None
    return {name: profile[name] for name in ("provider", "model", "prompt_version", "reasoning_effort")}
