# 단건 AI 감정 분석

현재 단계는 `analyze_review(CleanReview, AnalysisOptions) -> AnalysisResult` 구현이다.
기본 모델은 `gpt-5.6-luna`이며, OpenAI Chat Completions의 strict JSON Schema를 사용한다.
분류·요약 작업에는 `reasoning_effort=none`, 출력 한도 1,024 토큰을 적용한다.

## 실행

프로젝트 루트에서 의존성을 설치하고 `.env.example`을 참고하여 `.env`에
`AI_API_KEY`를 설정한다. 키를 소스 코드에 넣거나 터미널 출력으로 공유하지 않는다.
아래 예시는 실제 API 호출이며 비용이 발생한다. SQLite 없이 실행할 수 있다.

```python
from datetime import date, datetime, timezone

from src.analyzer import analyze_review
from src.config import load_config, load_env_file
from src.models import AnalysisOptions, CleanReview

load_env_file()
ai = load_config("config/config.json")["ai"]
options = AnalysisOptions(
    provider=ai["provider"],
    model=ai["model"],
    api_key=ai["api_key"],
    timeout_seconds=ai["timeout_seconds"],
    max_retries=ai["max_retries"],
)
# 독립 실행 예시의 ID다. 실제 파이프라인에서는 저장소가 부여한 ID를 사용한다.
review = CleanReview(
    id=1, source_review_id=None, product_name="이어폰",
    review_date=date(2026, 9, 1), rating=5,
    review_text="배송이 빠르고 음질도 좋아요.",
    cleaned_at=datetime.now(timezone.utc),
)
result = analyze_review(review, options)
print(result.sentiment.value, result.confidence)
```

## 동작과 경계

- 감정, 신뢰도, 요약, 키워드의 타입과 범위를 로컬에서 재검증한다.
  알 수 없는 필드·누락 필드·중복 JSON 키·비정상 숫자·빈 키워드는 거부한다.
- 키워드는 앞뒤 공백을 제거하고 순서를 유지하여 중복을 제거한다.
- 내부 리뷰 ID, UTC 분석 시각, 제공자, 응답에 포함된 실제 모델 이름,
  `review-sentiment-v1` 프롬프트 버전을 결과에 기록한다.
- `confidence`는 모델의 자기평가다. 정확도로 검증된 확률이 아니므로
  모델 간 비교나 자동 의사결정 기준으로 사용하기 전에 평가가 필요하다.
- 리뷰는 사용자 메시지의 JSON 데이터로 전달하고 시스템 지시와 분리한다.
  원본 ID·파일 경로·리뷰 날짜는 모델에 보내지 않는다.
- API 호출 오류, 거부, 불완전 응답, 파싱·검증 오류는 `AIProviderError`다.
  누락된 키·미지원 제공자·미지원 프롬프트 버전은 `ConfigError`다.
- 단건 함수는 요청을 한 번 수행하며 SDK 재시도를 끈다. `max_retries`는
  후속 배치 분석에서 사용할 설정이다. 현재 단건 함수에는 적용하지 않는다.
- 배치 분석, 결과 저장, 실패 상태 변경, CLI `analyze` 연결은 후속 작업이다.
  `SingleReviewAnalyzer`는 아직 전체 `ReviewAnalyzer` Protocol 구현이 아니다.

## llama-server 확장

`src/ai_provider.py`의 `AnalysisProvider.complete()`가 API 경계다.
`SingleReviewAnalyzer(provider=...)`에 다른 어댑터를 주입하면 공통 응답 검증을
재사용할 수 있다. 현재 기본 어댑터는 `provider=openai`만 허용한다.

Gemma용 어댑터, `base_url` 설정, 서버 인증, GGUF·채팅 템플릿 및 JSON Schema
호환성 검증은 서버 연결 단계에서 추가한다. OpenAI 키를 자체 서버로 전달하지 않도록
제공자별 자격 증명을 분리한다. 모델 교체 전 같은 한국어 평가셋으로 감정 분류 정확도,
JSON 유효 비율, 응답 시간, 비용을 비교한다.

## 테스트

```bash
python -m unittest discover -s tests -p 'test_analyzer.py' -v
```

테스트는 fake 제공자와 실제 OpenAI SDK + HTTP mock을 사용한다. 외부 요청이나
실제 API 키가 필요하지 않다. 실제 모델의 접근 권한·한국어 정확도는 별도 검증 대상이다.

## 참고

- [OpenAI GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [공통 인터페이스 명세](INTERFACE_BOUNDARY_SPEC.md)
