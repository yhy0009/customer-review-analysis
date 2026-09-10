"""Provider adapters for single, structured AI requests (no database access)."""

from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Protocol

from openai import OpenAI, OpenAIError

from src.errors import AIProviderError, ConfigError
from src.models import AnalysisOptions


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    model: str


class AnalysisProvider(Protocol):
    """An adapter returns complete JSON text and the actual response model.

    Adapters must raise AIProviderError with a sanitized message on failure.
    A future llama-server adapter can implement this without changing DTOs.
    """

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        options: AnalysisOptions,
    ) -> ProviderResponse: ...


class OpenAIProvider:
    """GPT-5.6 Luna via Chat Completions and strict structured outputs."""

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        options: AnalysisOptions,
    ) -> ProviderResponse:
        if options.provider != "openai":
            raise ConfigError("OpenAI 어댑터는 provider=openai만 지원합니다.")
        if not isinstance(options.api_key, str) or not options.api_key.strip():
            raise ConfigError("AI_API_KEY 설정이 필요합니다.")

        try:
            # Explicit endpoint/key: configuration is owned by src.config.
            # Disable SDK retries; the future batch analyzer owns retry policy.
            with OpenAI(
                api_key=options.api_key,
                base_url="https://api.openai.com/v1",
                timeout=options.timeout_seconds,
                max_retries=0,
            ) as client:
                response = client.chat.completions.create(
                    model=options.model,
                    messages=messages,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "review_analysis",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                    reasoning_effort="none",
                    max_completion_tokens=1024,
                    store=False,
                )
        except (OpenAIError, JSONDecodeError):
            # SDK exceptions can include request/response bodies or credentials.
            raise AIProviderError("OpenAI 분석 요청에 실패했습니다.") from None

        try:
            if len(response.choices) != 1:
                raise ValueError
            choice = response.choices[0]
            if choice.finish_reason != "stop" or choice.message.refusal:
                raise ValueError
            content = choice.message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError
            if not isinstance(response.model, str) or not response.model.strip():
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise AIProviderError("OpenAI가 완전한 분석 응답을 반환하지 않았습니다.") from None
        return ProviderResponse(content=content, model=response.model)
