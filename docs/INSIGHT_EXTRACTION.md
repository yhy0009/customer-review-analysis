# 조건별 인사이트 추출

`AIInsightExtractor`는 기존 `InsightExtractor` Protocol을 구현한다.
`InsightService.extract_insights(ExtractRequest)`는 저장소에서 대상을 선택해
`InsightResult`를 반환한다. 기본 `main.py extract` 등록과 콘솔 출력도 연결됐으며
실행 방법은 [AI CLI 실행 안내](AI_CLI.md)를 따른다.

## CLI 저장과 리포트 연결

`python main.py extract`는 성공한 결과를 설정의
`paths.output_dir/insights/<DB 경로 지문>/<필터·선택 한도 지문>.json`에 저장하고
**인사이트 파일의 절대 경로**를 출력한다. 같은 DB·조건·한도의 재추출은 완성된 JSON으로
기존 결과를 교체한다. AI 실패·검증 실패·게시 실패 시 기존 파일은 유지한다.
저장 위치의 기본 권한과 파일 충돌은 AI 요청 전에 확인한다.

파일에는 필터·실제 대상 건수·생성 시각·키워드·요약·이슈·개선 제안·전체 근거와
선택 한도·리뷰 ID·원문/분석 해시·허용된 생성 설정을 저장한다. API 키와 서버 URL 원문은
저장하지 않는다. AI 호출 전 읽은 리뷰를 기준으로 저장하므로 호출 중 데이터가 바뀌면
다음 대시보드에서 오래된 결과로 판정된다.

`dashboard`는 현재 DB·조건·생성 설정에 맞는 결과를 자동으로 찾아 TXT/MD/HTML에 포함한다.
검색·검증 및 파일 직접 지정은 [대시보드 CLI 안내](DASHBOARD.md#ai-추출-결과-연결)를 따른다.
웹과 파일 형식을 공유하며, 생성 정보가 있는 유한 한도 1~2000은 artifact v2,
`limit=None`(전체 선택) 또는 2000 초과는 v3를 사용한다. v1/v2 읽기 호환성은 유지한다.
v3는 전체 선택의 의미를 보존하기 위한 형식 확장이고 AI 배치 한도를 늘리지는 않는다.
인사이트 파일은 기존과 같이 최대 10 MB까지만 읽고 저장 전에 검증한다.

단독 `InsightService`는 파일 저장을 강제하지 않는다. 기본 CLI는 `save_result` 콜백을
주입해 결과를 저장하며, 콜백에는 추출 전 스냅샷의 리뷰와 결과·선택 한도가 전달된다.

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

AI는 이슈·개선 제안 각 최대 3개(항목당 80자), 전체 요약 160자 이내를 반환한다.
실제 입력에는 제품명·별점·리뷰 본문·감정과 코드에서 계산한 키워드 집계를 보낸다.
리뷰 ID, 원본 파일, 외부 리뷰 ID와 기존 분석 메타데이터는 보내지 않는다.
원문과 제품명은 신뢰할 수 없는 데이터로 구분하도록 프롬프트에 명시한다.

1. 먼저 모든 선택 리뷰의 불편·장점 label과 원문 인용 구절을 추출한다. 리뷰별 순번은
   요청 안에서의 1부터 시작하는 위치이며 실제 DB ID가 아니다. 누락·중복 순번, 다른 리뷰의
   인용이나 원문에 없는 인용을 거부한다. 부정 분석에 불편이 하나도 없으면 불일치로 실패한다.
2. 원문과 검증한 근거로 이슈·요약을 작성한다. 모든 불편 label이 이슈에 그대로 남아야 하며,
   장점 근거가 있으면 그중 하나 이상이 요약에 포함되어야 한다. 불편 근거가 없으면 이슈와
   개선 제안을 만들 수 없다. 근거 label 자체의 의미가 정확한지는 별도 품질 평가 대상이다.
3. 개선 제안은 불편 label에 ‘발생 조건 확인·재현 점검’ 또는 ‘처리 절차·안내 개선’을 붙인
   후보 중 최대 3개를 선택한다. 후보를 코드에서 구성하고 스키마와 로컬 검사에서 제한하므로
   원문에 없는 앱·펌웨어·부품 등의 구체적인 해결책을 자유롭게 추가할 수 없다.

출력은 JSON 키, 자료형, 길이, 빈 문자열과 중복 JSON 키를 로컬에서 검증한다.
형식 오류와 일시적 제공자 오류는 `max_retries`만큼 추가 시도하며 대기 시간은
1·2·4·8·16초(이후 16초)다. 인증·권한 등 재시도 불가 오류와 설정 오류는 즉시 종료한다.
단계별 재시도이며 성공한 근거 추출을 최종 요약 실패 때문에 다시 호출하지 않는다.
실패한 결과로 대체하거나 누락을 숨겨 반환하지 않는다. 추출 실패 시 기존 개별 분석이나
리뷰 상태를 변경하지 않는다. 키워드 집계와 반환 DTO도 기존과 같다.

프롬프트 버전은 `review-insights-v5`이며, `AnalysisOptions.prompt_version`은
`None` 또는 이 버전을 사용한다. 감정 분석 전용 버전은 적용할 수 없다.
이전 인사이트 버전을 명시했던 호출자는 v5 또는 `None`으로 변경해야 한다. 기존 개별 감정
분석 결과와 DB 스키마는 변경하지 않는다. 이전 프롬프트는 `evaluation/prompts/`에 보존한다.

## 대량 입력과 근거 보존

v5는 선택한 리뷰를 ID 순으로 최대 20건씩 분할한다. 각 근거 요청의 사용자 JSON은
24,000자 이내이며, 한 리뷰를 중간에서 자르지 않는다. 단일 리뷰가 한도를 넘거나
배치가 기본 100개를 넘으면 API 호출 전에 실패한다. 생성자의 `batch_size`, `max_batches`,
`max_input_chars`로 조정할 수 있다. 문자 수는 모델 토큰 수 보장이 아니다.

각 배치 안에서 순번·인용·부정 근거를 검증한 뒤 전체 순번으로 합친다. 성공한 배치는
현재 실행 중에 재사용하며 실패한 배치만 재시도한다. 최종 실패하면 부분 InsightResult를
반환하지 않는다. 실패한 배치부터 재개하는 기능은 없다. CLI는 성공한 완성본만 저장하며,
다시 `extract`를 실행하면 AI를 새로 호출한다. 저장 결과 재사용은 `dashboard`에서 수행한다.

`InsightResult.evidence_groups`에는 모든 추출 근거가 제품·종류·주제별로 남는다.
각 인용의 `review_id`, 원래 `label`, `quote`로 원문을 확인할 수 있고, 주제의 `review_count`는
서로 다른 리뷰 ID 수다. 제품명은 정확히 구분한다. label의 NFKC·공백 정규화와 다음
정확한 표현만 병합한다. 키워드 통계는 변경하지 않는다.

- 배송 늦음 / 배송이 늦음 → 배송 지연
- 고객센터 답변 없음 / 고객센터 응답 없음 → 고객센터 무응답

그 외 유사 표현은 별도 주제로 남긴다. 의미나 강도가 다른 표현을 부분 일치로 합치지 않는다.
동의어 사전은 `src/insight_batching.py`에 있으며, 원래 label은 병합 후에도 보존한다.

한 배치의 작은 입력은 기존처럼 모든 불편 label을 최종 이슈에 포함한다.
여러 배치이거나 최종 입력·출력 예산을 넘으면 `summary_scope=top_complaints`로 전환한다.
전체 근거에서 리뷰 수 내림차순, 제품명·종류·label 순으로 정렬하여 불편 최대 3개와 장점
최대 1개를 요약에 제공한다. 심각도 순위는 아니다. 이슈는 `근거 주제 N: label`의 고정
후보를 모두 포함하고, 제안도 해당 후보에 연결된 점검·절차 개선 문장만 허용한다.
요약에 선택되지 않은 불편도 전체 근거 목록에 남으며 CLI와 TXT/Markdown 리포트에서
요약 범위 및 모든 인용을 표시한다. 대표 인용만 최종 모델에 보내지만 결과의 인용은 자르지 않는다.
제품명 등으로 압축된 최종 요청도 한도를 넘으면 명시적으로 실패한다.

정상 요청 횟수는 **근거 배치 수 B + 최종 요약 1회**이며, 빈 대상은 0회다.
재시도 포함 상한은 `(B + 1) * (max_retries + 1)`, 기본 B 상한은 100이다.
호환 서버에는 `model/messages`만 보내며, 공식 제공자 출력 상한은 8,192토큰이다.
공통 `ai.timeout_seconds` 기본은 요청당 90초이며 명시한 값은 존중한다.
실제 DB ID는 모델 요청에 보내지 않고 코드에서 인용에 연결한다. 근거는 반환 객체에만
있으며 DB 스키마·개별 분석 상태는 바꾸지 않는다. 의미적 정확성은 별도 검토 대상이다.

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

# 설정된 제공자에 합성 리뷰 3건으로 최대 2회 요청. 재시도·DB 쓰기 없음.
python scripts/smoke_insights.py --live
```

자동 테스트는 필터와 limit, 키워드 통계 일치, 빈 입력, 잘못된 응답, 재시도,
공식·호환 SDK 요청, SQLite 읽기 일관성, AI 호출 전 트랜잭션 종료와 상태 보존을 검증한다.
실제 API 스모크 테스트의 성공은 연결·출력 형식 검증이며 요약 품질에 대한 종합 평가는 아니다.

초기 v1의 2026-09-13 검증: 전체 자동 테스트 217개 통과(추출 관련 23개).
설정된 라우팅 서버에 `gpt-5-mini`를 요청한 스모크 테스트는 1회 호출, 21.26초로 성공했다.
합성 리뷰 3건에서 부정 키워드 `배송=2`, `포장=1`을 반환했고, 배송 지연·포장 손상 이슈와
개선 제안을 생성했다. 재현 시 응답 내용과 지연 시간은 달라질 수 있다.
