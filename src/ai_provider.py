"""Provider adapters for single, structured AI requests (no database access)."""

from dataclasses import dataclass
from json import JSONDecodeError, dumps
from typing import Any, Protocol
from urllib.parse import urlsplit

from openai import APIStatusError, OpenAI, OpenAIError

from src.errors import AIProviderError, ConfigError
from src.models import AnalysisOptions


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    model: str


class NonRetryableAIError(AIProviderError):
    """A provider failure that repeating the same request will not resolve."""


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
    """OpenAI Chat Completions with strict structured outputs."""

    def _base_url(self, options: AnalysisOptions) -> str:
        if options.provider != "openai" or options.base_url is not None:
            raise ConfigError("별도 서버는 provider=openai-compatible과 base_url을 설정하세요.")
        return "https://api.openai.com/v1"

    def _request_parameters(self, messages, schema, options) -> dict[str, Any]:
        parameters = {
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "review_analysis", "strict": True, "schema": schema,
            }},
            "max_completion_tokens": 1024,
            "store": False,
        }
        if options.reasoning_effort is not None:
            parameters["reasoning_effort"] = options.reasoning_effort
        return parameters

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        options: AnalysisOptions,
    ) -> ProviderResponse:
        base_url = self._base_url(options)
        if not isinstance(options.api_key, str) or not options.api_key.strip():
            raise ConfigError("AI_API_KEY 설정이 필요합니다.")

        try:
            # Explicit endpoint/key: configuration is owned by src.config.
            # Disable SDK retries; the batch analyzer owns retry policy.
            with OpenAI(
                api_key=options.api_key,
                base_url=base_url,
                timeout=options.timeout_seconds,
                max_retries=0,
            ) as client:
                response = client.chat.completions.create(
                    model=options.model,
                    **self._request_parameters(messages, schema, options),
                )
        except APIStatusError as exc:
            transient = exc.status_code in (408, 409, 429) or exc.status_code >= 500
            if not transient or exc.code == "insufficient_quota":
                raise NonRetryableAIError(f"AI 요청 설정·모델 지원·권한·할당량을 확인하세요. (HTTP {exc.status_code})") from None
            raise AIProviderError(f"AI 분석 요청에 일시적인 오류가 발생했습니다. (HTTP {exc.status_code})") from None
        except (OpenAIError, JSONDecodeError):
            # SDK exceptions can include request/response bodies or credentials.
            raise AIProviderError("OpenAI 분석 요청에 실패했습니다.") from None

        try:
            if len(response.choices) != 1:
                raise ValueError
            choice = response.choices[0]
            if choice.finish_reason != "stop" or choice.message.refusal:
                raise NonRetryableAIError("OpenAI가 분석을 거부하거나 출력을 중단했습니다.")
            content = choice.message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError
            if not isinstance(response.model, str) or not response.model.strip():
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise AIProviderError("OpenAI가 완전한 분석 응답을 반환하지 않았습니다.") from None
        return ProviderResponse(content=content, model=response.model)


class OpenAICompatibleProvider(OpenAIProvider):
    """Minimal Chat Completions routing; JSON is enforced by local validation."""

    def _request_parameters(self, messages, schema, options) -> dict[str, Any]:
        # COPA accepts model/messages, but rejects the tested advanced options.
        # Put the output contract in trusted instructions, without mutating input.
        routed_messages = [dict(message) for message in messages]
        routed_messages[0]["content"] += (
            "\n설명이나 코드 블록 없이 다음 스키마의 JSON 객체만 반환하세요: "
            + dumps(schema, ensure_ascii=False)
        )
        return {"messages": routed_messages}

    def _base_url(self, options: AnalysisOptions) -> str:
        if options.provider != "openai-compatible" or not options.base_url:
            raise ConfigError("호환 서버에는 provider=openai-compatible과 base_url이 필요합니다.")
        try:
            url = urlsplit(options.base_url)
            if (url.scheme not in ("http", "https") or not url.hostname
                    or url.username is not None or url.password is not None
                    or url.query or url.fragment):
                raise ValueError
            if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError
            _ = url.port
        except ValueError:
            raise ConfigError("base_url은 인증정보·쿼리 없는 HTTPS 주소여야 합니다. 로컬 HTTP는 허용합니다.") from None
        return options.base_url.rstrip("/")


def provider_for(options: AnalysisOptions) -> AnalysisProvider:
    if options.provider == "openai":
        return OpenAIProvider()
    if options.provider == "openai-compatible":
        return OpenAICompatibleProvider()
    raise ConfigError("지원하지 않는 AI 제공자입니다.")
