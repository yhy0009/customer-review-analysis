# AI 감정 분석

단건 함수 `analyze_review(CleanReview, AnalysisOptions) -> AnalysisResult`와
저장소를 주입받는 `BatchReviewAnalyzer`를 제공한다.
기본 모델은 `gpt-5-mini`다. 공식 OpenAI 연결은 strict JSON Schema,
`reasoning_effort=minimal`, 출력 한도 1,024 토큰을 적용한다.
COPA 호환 연결은 `model/messages`만 전송하고 JSON 스키마를 프롬프트에 포함한다.
두 경로 모두 응답을 로컬에서 같은 규칙으로 검증한다.

## 단건 실행

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
    base_url=ai.get("base_url"),
    reasoning_effort=ai.get("reasoning_effort"),
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
  배치 분석에 적용하고 단건 함수에는 적용하지 않는다.
- `SingleReviewAnalyzer`는 단건 전용이고 `BatchReviewAnalyzer`는 전체
  `ReviewAnalyzer` Protocol을 구현한다.

## 배치 분석과 저장소 연결

`BatchReviewAnalyzer(repository, provider=None)`는 저장소의 현재 분석 결과를 조회하고,
기본적으로 기존 분석을 건너뛴다. `force=True`는 선택된 리뷰를 다시 분석한다.
동일 ID가 입력 목록에 반복되면 force 여부와 무관하게 첫 항목만 처리하고 나머지는
skipped로 집계한다. 저장된 Clean 리뷰가 없거나 분석 입력(제품명·별점·본문)이 달라졌으면
각각 `REVIEW_NOT_FOUND`, `STALE_REVIEW` 행 오류를 반환하며 AI 호출·상태 변경은 하지 않는다.

- 최초 호출 + `max_retries`회까지 시도한다. 0이면 한 번만 호출한다.
- 재시도 간격은 1, 2, 4, 8, 16초 순서이며 이후 16초로 제한한다.
- 연결·시간 초과, HTTP 408/409/429/5xx, JSON 파싱·검증 실패는 재시도한다.
- 다른 HTTP 요청 오류, 할당량 소진(`insufficient_quota`), 응답 거부·출력 중단은
  `NonRetryableAIError(AIProviderError)`로 분류하여 같은 요청을 반복하지 않는다.
- AI 실패가 확정되면 고정된 메시지로 `mark_analysis_failed()`를 호출하고 다음 리뷰로
  진행한다. `ItemError.retryable`에 재시도 가능 여부를 기록한다.
- 성공한 결과는 `save_analysis()`를 호출한 뒤 성공 건수와 결과 목록에 포함한다.
  기존 실패 상태 해제 및 이전 분석 교체는 저장소 계약을 따른다.
- 재분석 실패 시에는 저장소 계약에 따라 이전 결과가 제거되고 `ANALYSIS_FAILED`가 된다.
- 저장소·설정 오류나 사용자 중단은 배치를 즉시 중단한다. 이미 커밋된 성공 건은 보존한다.
  저장소 오류를 AI 오류로 처리하거나 분석 API를 다시 호출하지 않는다.
- 빈 목록은 0건 결과를 반환한다. 결과는 처리 항목 수 기준이며 API 시도 횟수가 아니다.

```python
from src.analyzer import BatchReviewAnalyzer
from src.models import AnalysisOptions, AnalysisBatchResult
from src.storage import ReviewRepository

def analyze_pending(repository: ReviewRepository, options: AnalysisOptions) -> AnalysisBatchResult:
    analyzer = BatchReviewAnalyzer(repository)
    reviews = repository.fetch_unanalyzed_reviews(limit=20)
    return analyzer.analyze_reviews(reviews, options)
```

저장소를 생성하고 닫는 책임은 호출자에게 있다. 배치 분석에는 저장소에서 조회한 리뷰를
전달한다. 분석 도중 다른 프로세스가 리뷰를 수정하는 동시 실행은 검증 범위에 포함하지 않는다.

## 요청 서비스와 CLI 어댑터

`AnalysisService(repository, analyzer, options).analyze_reviews(AnalyzeRequest)`는
`ALL`, `UNANALYZED`, `REVIEW_ID` 대상을 선택한다. `UNANALYZED`는 이전 실패도 포함하며,
`force=True`를 지정해도 분석 완료 리뷰까지 대상 범위를 넓히지 않는다. `limit`은 대상
조회에 적용한다. `ALL`에서는 이미 분석된 리뷰도 조회 개수에 포함된다.

`build_analyze_handler()`는 분석 기능만 독립적으로 CLI에 연결할 수 있다.
호출 예시는 다음과 같다. `options`는 위 단건 예시처럼 공통 설정에서 구성한다.

```python
from src.analysis_service import AnalysisService
from src.analyzer import BatchReviewAnalyzer
from src.cli import main
from src.handlers import build_analyze_handler

def run_analysis_cli(repository, options, argv):
    service = AnalysisService(repository, BatchReviewAnalyzer(repository), options)
    return main(argv, handlers={"analyze": build_analyze_handler(service.analyze_reviews)})

# run_analysis_cli(repository, options, ["analyze", "--unanalyzed", "--limit", "20"])
```

완료 시 `processed/succeeded/skipped/failed`를 출력한다. 정상은 종료 코드 0,
일부 또는 전체 행 실패는 1, 잘못된 대상·설정은 2, 저장소 오류는 3이다.
`AnalysisService`는 분석 유스케이스만 제공하며 전체 `ApplicationServices` 구현이 아니다.
`main.py`의 기본 저장소·서비스 생성 연결은 SQLite 통합 정리 후 진행한다.
fake 저장소 단위 테스트와 실제 SQLite를 사용하는 분석 서비스 통합 테스트를 제공한다.
SQLite 테스트는 AI 제공자만 mock으로 대체하며, 두 저장소 import 경로에서 저장·실패·재시도·
강제 재분석·통계와 트랜잭션 롤백을 검증한다. 기본 CLI 진입점의 전체 실행 연결은 남아 있다.

## llama-server 확장

`src/ai_provider.py`의 `AnalysisProvider.complete()`가 API 경계다.
`SingleReviewAnalyzer(provider=...)`에 다른 어댑터를 주입하면 공통 응답 검증을
재사용할 수 있다. `provider=openai`는 OpenAI 공식 주소만 사용하며,
`provider=openai-compatible`은 명시적으로 설정한 호환 서버 주소를 사용한다.

Gemma의 GGUF·채팅 템플릿 및 JSON Schema 호환성 검증은 서버 연결 단계에서 진행한다.
OpenAI 키를 자체 서버로 전달하지 않도록
제공자별 자격 증명을 분리한다. 모델 교체 전 같은 한국어 평가셋으로 감정 분류 정확도,
JSON 유효 비율, 응답 시간, 비용을 비교한다.

## OpenAI 호환 라우팅 서비스

Git에서 제외되는 `.env`에 서버별 키와 주소를 설정한다. 기본 OpenAI 제공자에
주소만 바꾸는 것은 거부하며, 아래처럼 제공자도 함께 지정해야 한다.

```dotenv
AI_PROVIDER=openai-compatible
AI_BASE_URL=https://copa.codyssey.kr/v1
AI_MODEL=gpt-5-mini
AI_API_KEY=<해당 라우팅 서비스에서 발급한 키>
```

서버의 모델 목록에 선택한 모델이 있는지 확인해야 한다. 공식 OpenAI에서 제공하는
모델이라도 라우팅 서비스가 지원하지 않을 수 있다.

COPA에서 고급 옵션이 포함된 요청은 HTTP 400으로 거절되고, `model/messages`만 있는
요청은 성공하는 것을 확인했다. 따라서 호환 어댑터는 최소 요청 형식을 사용한다.
`response_format`, `reasoning_effort`, `max_completion_tokens`, `store`를 전송하지 않으며
서버가 JSON 형식을 강제한다고 가정하지 않는다. 스키마를 시스템 프롬프트에 전달하고
응답을 로컬에서 검증한다. 추론·출력 한도·보관 정책은 이 경로에서 서버 설정을 따른다.
`ai.reasoning_effort`/`AI_REASONING_EFFORT`는 공식 OpenAI 어댑터에만 적용한다.

키는 선택한 서버에만 전달하며 다른 서버로 자동 대체하지 않는다. `base_url`은
인증정보·쿼리·프래그먼트를 포함하지 않는 HTTPS 주소여야 한다. 로컬 개발에서는
localhost/127.0.0.1/::1의 HTTP 주소도 허용한다.

## 실제 연결 점검

```bash
python scripts/smoke_ai.py --live
```

`--live`를 명시한 경우에만 합성 한국어 리뷰 3건을 실제 API로 분석한다. 호출 비용이
발생한다. 재시도하지 않으며 첫 오류에서 중단한다. DB에는 기록하지 않는다.
감정 분류, 응답 스키마, 실제 응답 모델과 요청 시간을 확인한다. 소수 샘플의 결과는
연결 검증이며 일반적인 정확도 평가가 아니다. `--output /tmp/ai-smoke.json`으로
키가 제외된 결과를 JSON 파일에 저장할 수 있다.

### 2026-09-10 실제 연결 검증

COPA 서버는 인증과 모델 목록 조회에 성공했지만 `gpt-5.6-luna`를 제공하지 않았다.
기본 모델을 지원 목록에 있는 `gpt-5-mini`로 변경하여 실제 어댑터로 아래 결과를 확인했다.

| 합성 리뷰 | 예상 감정 | 실제 감정 | 응답 시간 |
|---|---|---|---|
| 빠른 배송·좋은 음질 | positive | positive | 3.67초 |
| 제품 색상·구성품 설명 | neutral | neutral | 6.38초 |
| 고장·고객센터 무응답 | negative | negative | 3.94초 |

세 응답 모두 JSON 필드·타입·범위 검증을 통과했다. 응답의 모델 필드는 `gpt-5-mini`였다.
이 결과는 3건의 합성 데이터에 대한 연결 확인이며, 일반적인 분류 정확도를 의미하지 않는다.

## 테스트

```bash
python -m unittest discover -s tests -p 'test_analyzer.py' -v
python -m unittest discover -s tests -p 'test_analysis_batch.py' -v
python -m unittest discover -s tests -p 'test_sqlite_consistency.py' -v
```

테스트는 fake 제공자와 실제 OpenAI SDK + HTTP mock을 사용한다. 외부 요청이나
실제 API 키가 필요하지 않다. 실제 모델의 접근 권한·한국어 정확도는 별도 검증 대상이다.

## 참고

- [OpenAI GPT-5 Mini](https://developers.openai.com/api/docs/models/gpt-5-mini)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI 오류 코드](https://developers.openai.com/api/docs/guides/error-codes)
- [공통 인터페이스 명세](INTERFACE_BOUNDARY_SPEC.md)
