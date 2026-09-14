# 조건별 인사이트 추출

`AIInsightExtractor`는 기존 `InsightExtractor` Protocol을 구현한다.
`InsightService.extract_insights(ExtractRequest)`는 저장소에서 대상을 선택해
`InsightResult`를 반환한다. 기본 `main.py extract` 등록과 콘솔 출력도 연결됐으며
실행 방법은 [AI CLI 실행 안내](AI_CLI.md)를 따른다.

## 대상과 집계 규칙

- 감정, 기간(양 끝 포함), 제품명, 별점 조건에 맞는 **분석 완료 리뷰**만 사용한다.
  제품명은 대소문자를 구분하지 않는 Unicode 부분 일치이며 `%`와 `_`는 문자 그대로다.
- 미분석·실패 리뷰는 제외한다. 중립 리뷰는 요약 대상과 `review_count`에 포함한다.
- ID 오름차순으로 선택하며 `limit`은 필터와 분석 완료 조건 적용 후의 리뷰 수다.
  `limit=None`이면 해당 조건의 전체 분석 완료 리뷰를 선택한다. 최근순·무작위 표본은 아니다.
- 추출기를 직접 호출해도 같은 필터·정렬·limit 규칙을 적용한다. 동일한 ID와 내용의
  중복 입력은 한 번만 세고, 동일한 ID에 서로 다른 내용이 있으면 `ValidationError`다.
- 긍·부정 키워드는 저장된 `AnalysisResult.keywords`에서 집계한다. 같은 리뷰의 동일한
  문자열은 한 번 센다. 빈도 내림차순, 동률이면 문자열 오름차순으로 상위 10개를 반환한다.
  별도 동의어 병합이나 공백 정규화를 하지 않으며, SQLite 통계와 같은 집계 규칙이다.
- `review_count`, 키워드 집계, AI 요약은 모두 동일한 선택 집합을 기준으로 한다.
  분석 결과의 키워드 품질에 영향을 받으며, 빈도 자체를 AI가 추정하지 않는다.
- 대상이 없으면 `review_count=0`, 빈 배열, 안내 요약을 반환하고 API를 호출하지 않는다.

## AI 요청과 실패 처리

기존 `AnalysisOptions`와 제공자 어댑터를 재사용한다. 기본 모델은 `gpt-5-mini`이며
`openai-compatible` 설정에서는 기존 라우팅 서버를 사용할 수 있다. 호환 서버에는
`model`, `messages`만 보내고 시스템 메시지에 스키마를 포함한다.

AI는 이슈·개선 제안 각 최대 3개(항목당 80자), 전체 요약 160자 이내를 생성한다.
실제 입력에는 제품명·별점·리뷰 본문·감정과 코드에서 계산한 키워드 집계를 보낸다.
리뷰 ID, 원본 파일, 외부 리뷰 ID와 기존 분석 메타데이터는 보내지 않는다.
원문과 제품명은 신뢰할 수 없는 데이터로 구분하도록 프롬프트에 명시한다.

출력은 JSON 키, 자료형, 길이, 빈 문자열과 중복 JSON 키를 로컬에서 검증한다.
형식 오류와 일시적 제공자 오류는 `max_retries`만큼 추가 시도하며 대기 시간은
1·2·4·8·16초(이후 16초)다. 인증·권한 등 재시도 불가 오류와 설정 오류는 즉시 종료한다.
추출 실패 시 기존 개별 분석이나 리뷰 상태를 변경하지 않는다.

프롬프트 버전은 `review-insights-v1`이며, `AnalysisOptions.prompt_version`은
`None` 또는 이 버전을 사용한다. 감정 분석 전용 버전은 적용할 수 없다.

초기 버전은 한 요청으로 요약한다. 사용자 JSON 직렬화 결과가 기본 **24,000자**를
넘으면 API 호출 전에 `ValidationError`를 반환한다. 내용을 몰래 자르지 않으므로
필터나 `limit`을 줄여 다시 실행해야 한다. `max_input_chars`로 상한을 조정할 수 있지만
이 값은 토큰 수 보장이 아니며 제공자별 컨텍스트 한도에 맞춰 정해야 한다.
대량 입력의 분할 요약·병합과 인사이트 영구 저장은 아직 지원하지 않는다.

## 서비스 연결 예시

```python
from src.config import load_config, load_env_file, resolve_project_path
from src.insight_extractor import AIInsightExtractor
from src.insight_service import InsightService
from src.models import AnalysisOptions, ExtractRequest, ReviewFilter, Sentiment
from src.storage import SQLiteReviewRepository

load_env_file()
config = load_config("config/config.json")
options = AnalysisOptions(**config["ai"])

with SQLiteReviewRepository(resolve_project_path(config["storage"]["database_path"])) as repository:
    service = InsightService(
        repository,
        AIInsightExtractor(options),
        snapshot=repository.read_snapshot,
    )
    insight = service.extract_insights(ExtractRequest(
        filters=ReviewFilter(sentiment=Sentiment.NEGATIVE),
        limit=50,
    ))
    print(insight.summary)
```

SQLite에서는 반드시 `snapshot=repository.read_snapshot`을 주입해 다중 페이지 조회를
같은 읽기 시점으로 묶는다. 서비스가 **조회 완료 직후 스냅샷을 종료한 뒤 AI를 호출**하므로
호출자가 전체 `extract_insights`를 별도 읽기 트랜잭션으로 감싸지 않는다.
다른 Repository를 주입할 때는 해당 저장소의 일관된 읽기 컨텍스트를 전달한다.
스냅샷을 생략하면 페이지별 건수·순서 검사는 수행하지만, 건수가 같은 동시 수정까지
검출할 수는 없다. 연결 생성·종료는 호출자가 소유하며 공통 Repository Protocol은 유지한다.

`InsightResult`는 CLI 출력 어댑터나 `ReportGenerator`로 전달하면 된다.

## 검증

```bash
python -m unittest discover -s tests -p 'test_insight*.py' -v
python -m unittest discover -s tests -v

# 설정된 제공자에 합성 리뷰 3건으로 1회 요청. 재시도·DB 쓰기 없음.
python scripts/smoke_insights.py --live
```

자동 테스트는 필터와 limit, 키워드 통계 일치, 빈 입력, 잘못된 응답, 재시도,
공식·호환 SDK 요청, SQLite 읽기 일관성, AI 호출 전 트랜잭션 종료와 상태 보존을 검증한다.
실제 API 스모크 테스트의 성공은 연결·출력 형식 검증이며 요약 품질에 대한 종합 평가는 아니다.

2026-09-13 검증: 전체 자동 테스트 217개 통과(추출 관련 23개).
설정된 라우팅 서버에 `gpt-5-mini`를 요청한 스모크 테스트는 1회 호출, 21.26초로 성공했다.
합성 리뷰 3건에서 부정 키워드 `배송=2`, `포장=1`을 반환했고, 배송 지연·포장 손상 이슈와
개선 제안을 생성했다. 재현 시 응답 내용과 지연 시간은 달라질 수 있다.
