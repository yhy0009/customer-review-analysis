# JS 웹 대시보드

`highslow1536`은 JS 화면과 로컬 조회 어댑터를 담당하고, `sayknow`는 Python 시각화
자료를 담당한다. 화면은 기존 `DashboardVisualizer`의 통합 PNG를 표시한다.
`main.py dashboard`의 CLI 연결은 별도 담당 작업이며 이 서버와 독립적이다.

## 실행

저장소 루트에서 기존 Python 의존성을 설치한 환경으로 실행한다.

```bash
python scripts/serve_dashboard.py --demo
```

`http://127.0.0.1:8765`에서 분석 개요·리뷰 탐색·AI 인사이트 화면을 연다.
Node, npm 설치나 프론트엔드 빌드는 필요 없다. `--port 8766`으로 포트를 바꿀 수 있다.
종료는 Ctrl-C다. PNG의 한국어 표시는 기존 시각화 모듈의 시스템 글꼴 탐색을 따른다.

데모는 평가에 사용한 **합성 리뷰 18건**과 저장된 분석·v5 인사이트를 임시 SQLite에
재구성한다. 페이지에 데모 표시가 나오며 AI 호출이나 실제 DB 수정은 없다.
`evaluation/results/2026-09-13/baseline-v1.json` 및
`evaluation/results/2026-09-16/v5-batch4-success.json`이 필요하다.
날짜는 2026-09-01로 통일하므로 데모의 일별 추이는 하루만 표시된다.

```bash
python scripts/serve_dashboard.py --database data/app_database.db
```

실제 모드는 기존 표준 스키마 DB를 읽기 전용으로 연다. 상대 경로는 저장소 루트 기준이다.
DB를 지정하지 않으면 `data/app_database.db`를 사용한다. 조회 전용 실행은 `.env`나 config를 읽지 않는다.
파일이 없거나 구형 스키마면 시작에 실패한다. 빈 DB 생성·자동 마이그레이션은 하지 않는다.
인사이트 파일 없이도 통계·차트·리뷰·통계 리포트는 이용할 수 있다.

## 리뷰 파일 다운로드

1. 제품·기간·감정·별점을 선택하고 **조회**한다.
2. **리뷰 탐색**에서 “현재 조건 전체 N건”을 확인한다. 다운로드 대상은 현재 페이지의
   10건이 아니라 조건에 맞는 전체 리뷰다.
3. **CSV 다운로드** 또는 **JSONL 다운로드**를 선택한다. CSV는 스프레드시트 열람용,
   JSONL은 한 줄에 리뷰 객체 하나를 담아 프로그램에서 처리하기 위한 형식이다.

파일은 조회 당시의 리뷰·저장된 분석 결과를 ID 오름차순으로 담는다. 다운로드 전에 DB가
바뀌어도 기존 조회 결과는 유지되므로 최신 내용을 받으려면 먼저 **새로고침**한다.
분석이 없는 리뷰도 포함하며 분석 필드는 빈 값이다. 외부 리뷰 ID는 제외한다.
CSV는 UTF-8 BOM과 수식 실행 방어를 적용하고, JSONL은 원래 문자열을 유지한다.

한 번에 최대 2,000건을 지원한다. 빈 결과나 한도 초과 시 버튼을 비활성화하며,
초과분을 조용히 잘라내지 않는다. 한도 초과 시 필터로 범위를 줄여 조회한다.
파일 준비 중에는 같은 형식의 중복 클릭을 막는다. 조회 결과가 만료되면 새로고침 후
다시 다운로드하고, 일시적인 파일 생성 실패는 같은 조회 결과에서 재시도할 수 있다.
리뷰 다운로드는 AI 호출이나 DB 수정을 하지 않는다.

## AI 인사이트 연결

기존 CLI `extract`의 콘솔 출력을 그대로 가져오는 방식이 아니다. 선택한 DB 데이터와
연결된 인사이트 파일을 한 번 준비한 뒤 서버에 지정한다.

```bash
# 이 명령만 AI를 실제 호출한다. 기존 .env/config의 AI 설정을 사용한다.
python scripts/prepare_dashboard_insight.py --live \
  --database data/app_database.db --output output/web-insight.json --limit 50

python scripts/serve_dashboard.py --database data/app_database.db \
  --insight-file output/web-insight.json
```

`--live`는 필수다. `--product-name`, `--date-from`, `--date-to`, `--sentiment`로
범위를 지정할 수 있으며 분석 완료 리뷰를 ID 오름차순으로 최대 `--limit`건 선택한다.
기본 50, 최대 2000건이다. 기존 출력 파일은 덮어쓰지 않으므로 다시 생성할 때 새 경로를 쓴다.
대상 데이터는 한 DB 읽기 트랜잭션에서 복사하고, 연결을 닫은 뒤 AI에 전달한다.

페이지 조회·필터·새로고침·리포트 다운로드는 AI를 호출하지 않는다.
`--insight-file`은 기존처럼 파일 하나를 연결한다. 조건별 저장 폴더도 함께 사용할 수 있으며
같은 조건의 캐시 파일이 있으면 우선 사용한다. 다음 상태를 구분한다.

| 상태 | 의미 |
|---|---|
| `available` | 필터·선택 리뷰·원문 및 분석 해시가 같고 모든 인용이 원문에 존재 |
| `missing` | 연결한 인사이트 파일 없음 |
| `scope_mismatch` | 화면 필터와 인사이트 생성 필터가 다름 |
| `stale` | 원문·분석·선택 대상 변경 또는 인용 검증 실패 |
| `invalid` | 조건별 캐시 파일이 손상되었거나 저장 조건이 다름. 다시 생성 가능 |
| `config_mismatch` | 원문은 일치하지만 현재 AI 생성 설정과 다르거나 과거 파일에 생성 정보가 없음 |

`available`만 화면과 다운로드 리포트에 포함한다. 통계는 필터 전체 대상, 인사이트는
선택 한도 이내 분석 완료 리뷰 대상이며 화면에 두 수치를 구분한다. 한도 밖 데이터가
추가되어도 선택 대상이 같으면 기존 인사이트를 사용할 수 있다. 원문 검증은 AI 해석의
의미적 정확성 검증과 다르며 기존 품질 평가 체계는 그대로 유지한다.

## 화면에서 조건별 생성·저장

```bash
python scripts/serve_dashboard.py --database data/app_database.db --enable-insights

# 저장된 결과만 다시 조회할 때 (AI 생성 버튼 비활성)
python scripts/serve_dashboard.py --database data/app_database.db \
  --insight-cache output/dashboard-insights
```

`--enable-insights`로 시작하면 현재 조건의 대상 건수와 **인사이트 생성** 버튼을 표시한다.
이 모드에서는 시작 시 `.env`/config의 AI 설정과 프롬프트 버전을 함께 고정하고, 버튼을
클릭할 때만 분석 완료 리뷰를 전송한다. 설정 변경 후에는 서버를 다시 시작한다. 필터 변경,
새로고침, 작업 상태 조회, 리포트 다운로드에는 AI 호출이 없다. 데모 모드는 생성 활성화와
함께 사용할 수 없다. 브라우저로 API 키나 공급자의 상세 오류를 보내지 않는다.

- `--insight-limit`은 기본 50, 범위 1~2000이며 ID 오름차순으로 선택한다.
- `--insight-cache` 기본값은 생성 활성화 시 `output/dashboard-insights`다. 이 기본 경로는
  Git에서 제외한다. 사용자 지정 폴더에는 원문 인용이 포함될 수 있으므로 별도로 관리한다.
- DB 절대 경로·필터·선택 한도별 최신 성공 결과를 파일 하나로 저장한다. 임시 파일을 검증한
  뒤 원자적으로 교체한다. 서버를 다시 실행해도 원문·분석이 일치하면 재사용한다.
- 필터 문자열이 다르면 별도 조건으로 취급한다. 모델·공급자·프롬프트·추론 설정·서버 주소가
  달라지면 이전 결과를 `config_mismatch`로 제외한다. 새 폴더 없이 같은 조건으로 다시 생성할
  수 있다. API 키 교체, 타임아웃·재시도 횟수 변경은 캐시를 무효화하지 않는다.
- 한 서버에서 동시에 한 작업만 실행한다. 같은 조건·원문의 중복 클릭은 같은 작업을 반환하고,
  다른 조건의 생성은 409로 거절한다. 대기열이나 자동 재시도는 없다. 추출기 내부의 기존
  일시적 API 오류 재시도 정책은 유지한다.
- 화면에서 실패 상태를 확인한 뒤 **다시 생성**할 수 있다. 생성 중 필터 이동이나 페이지
  새로고침에도 작업은 계속되고, 해당 조건으로 돌아오면 상태를 다시 조회한다.
- 실패 안내는 AI 이용 한도·요청 제한·연결 실패·설정 오류·결과 검증·파일 저장 실패를
  구분한다. 설정·저장 폴더·대상 범위 확인이 필요하면 **확인 후 다시 생성**으로 표시한다.
  버튼을 다시 누르면 새 AI 호출로 사용량이 발생할 수 있다. 설정을 바꾼 경우 서버를
  재시작해야 하며, 새로고침만으로 작업을 자동 재시도하지 않는다.
- 클릭 전에 원문이 바뀌면 새로고침을 요구한다. 생성 도중 바뀌면 결과는 저장하되 다음
  조회에서 `stale`로 제외하므로 변경된 DB에 이전 결과가 섞이지 않는다.
- 생성 완료 후 새 조회 결과로 통계·인사이트·리포트·원문 상세를 함께 갱신한다. 이전
  snapshot의 다운로드 결과는 변경하지 않는다.
- 작업 상태는 최근 64개를 메모리에 보관한다. 서버 종료 시 진행 중 작업은 중단되며 자동
  재개되지 않는다. 완료된 파일만 재사용하고, 중단된 작업은 다시 생성한다.

생성 HTTP 계약은 다음과 같다.

| 경로 | 계약 |
|---|---|
| `POST /api/insight-jobs` | JSON `{snapshot_id}`. 진행 중이면 202, 캐시 재사용이면 200 |
| `GET /api/insight-jobs/{id}` | `id`, `status`(running/succeeded/failed), `review_count`, `error`, `reused`, `error_code`, `retry_action` |

실패 작업의 `error`는 서버가 만든 안내문이며 예외 원문은 전달하지 않는다. 선택 필드
`error_code`는 허용된 AI 오류 코드 또는 `GENERATION_CONFIG`, `INSIGHT_STORAGE`,
`INSIGHT_VALIDATION`, `GENERATION_FAILED`다. `retry_action`은 `retry`, `check_settings`,
`check_storage`, `change_scope` 중 하나다. 진행 중·성공·캐시 재사용은 두 필드 모두 null이다.
동일한 필드를 snapshot의 `generation.job`에도 포함한다. HTTP 상태, 공급자 예외 문자열,
리뷰·프롬프트·응답 원문, 키·서버 주소·저장 경로는 진단 필드에 포함하지 않는다.

POST는 정확히 일치하는 로컬 Origin/Host, `Content-Type: application/json`,
snapshot 응답의 `generation.token`을 `X-Dashboard-Token` 헤더로 요구한다. 본문은 최대
1024바이트다. 임의 필터나 모델 설정을 POST로 전달하지 않고 이미 조회한 snapshot을 쓴다.
생성 비활성은 405, 출처·토큰 위반은 403, 빈 대상·형식 오류는 400, 다른 작업 진행 또는
원문 변경은 409, snapshot/작업 만료는 410이다. 토큰은 로컬 요청 위조 방어이며 사용자
인증이나 공개 배포 기능이 아니다.

snapshot v1에 하위 호환 필드 `generation`을 추가한다. `enabled`, `limit`, `review_count`,
`token`(비활성이면 null), 현재 조건의 최신 `job`(없으면 null)을 포함한다. 기존 조회 전용
명령과 `--insight-file`의 v1 읽기 호환성은 유지하고, 리뷰 DB에는 쓰지 않는다.

### 생성 정보와 파일 버전

새로 생성하는 파일은 envelope v2이며 `generation_profile`을 함께 저장한다.
`provider`, `model`(요청한 모델), `prompt_version`(추출기의 실제 내장 버전),
`reasoning_effort`, `endpoint_sha256`(서버 주소 지문)만 허용한다. 자격 증명과 서버 URL
원문은 저장하지 않는다. API 요청 모델과 라우팅 서버의 실제 응답 모델은 다를 수 있으므로
화면에도 **요청 모델**로 표시한다. 추론 설정 역시 요청 설정이며 호환 서버의 적용을 보증하지 않는다.

생성 서버의 설정과 파일에 기록된 설정을 비교한다. 선택 조건별 최신 성공 결과 하나를
유지하므로 다른 설정으로 재생성하면 같은 조건의 이전 파일을 교체한다. 시작 후 설정 파일을
수정해도 진행 중 작업의 설정·생성 정보는 바뀌지 않는다. 재시작한 서버의 새 설정과 기존
캐시가 다르면 사용자의 재생성 버튼 클릭을 기다리며 자동으로 AI를 호출하지 않는다.

v1 파일도 계속 읽을 수 있다. 조회 전용 모드에서는 원문 검증 후 표시하되 생성 정보가
없는 이전 결과라고 안내한다. 생성 활성 모드에서는 현재 설정과 같음을 확인할 수 없어
재생성을 요구한다. `--insight-file`로 명시한 파일에도 같은 규칙을 적용한다.
`prepare_dashboard_insight.py`도 v2를 기록한다. v2 파일을 읽으려면 이 변경 이후의 코드가 필요하다.

HTTP snapshot의 버전은 1을 유지한다. 선택 필드 `insight_provenance`는 현재 표시하는 결과의
생성 정보이며, `generation.profile`은 서버에 고정된 생성 설정이다. 둘 다 서버 주소 지문을
제외한 네 필드만 전달한다. 결과가 제외됐거나 v1 파일이면 `insight_provenance`는 null이다.
조회 전용 모드는 현재 AI 설정을 비교하지 않고 파일에 기록된 생성 정보를 표시한다.
기존 TXT/MD 리포트 본문 형식은 유지하며 생성 정보 표시는 웹 화면에서 제공한다.

## HTTP 계약 v1

서버는 로컬 조회용이며 인증·인터넷 배포·다중 사용자 운영을 제공하지 않는다.
Host/Origin을 로컬 주소로 제한하고 정적 파일 5개만 제공한다. API 키, Raw payload,
원본 파일 경로는 응답에 포함하지 않는다. 리뷰 원문은 화면 기능에 필요한 범위에서 반환한다.

| GET 경로 | 결과 |
|---|---|
| `/api/snapshot` | 통계, 리뷰 페이지, 선택적 인사이트, 산출물 URL |
| `/api/chart/{snapshot_id}` | 같은 통계로 생성한 PNG |
| `/api/report/{snapshot_id}/md` 또는 `/txt` | 같은 통계·인사이트의 리포트 첨부 파일 |
| `/api/review/{snapshot_id}/{id}` | 해당 페이지 또는 인사이트에 포함된 리뷰 상세 |
| `/api/export/{snapshot_id}/csv` 또는 `/jsonl` | 같은 조회 시점·필터의 전체 정제 리뷰와 저장된 분석 결과 |

snapshot 쿼리는 `product_name`(부분 일치), `date_from`, `date_to`(YYYY-MM-DD),
`sentiment`(positive/neutral/negative), `rating`, `rating_min`, `page`를 지원한다.
UI는 제품·기간·감정·별점 필터를 제공한다. 별점은 정확한 점수(1~5점) 또는 최소 점수
(1~5점 이상)를 하나만 선택한다. 같은 조건이 통계·차트·리뷰·인사이트·리포트에 적용된다.
API에 중복 또는 알 수 없는 조건을 보내면 400이다.
리뷰 페이지 크기는 10, 정렬은 ID 내림차순이다.

화면에서 조회·페이지 이동·초기화를 하면 적용한 조건과 페이지가 URL 쿼리에 기록된다.
예: `/?rating_min=4&page=1#reviews`. 화면 탭은 기존 해시(`#overview`, `#reviews`,
`#insights`)로 유지한다. 같은 로컬 서버의 주소를 다시 열거나 새로고침·뒤로가기·앞으로가기를
하면 필터 입력값과 조회 결과를 복원한다. 조회 조건을 바꾸거나 초기화하면 1페이지부터 본다.
데이터 감소로 저장된 페이지가 사라지면 마지막 유효 페이지(빈 결과는 1페이지)로 보정한다.

잘못된 날짜·감정·별점, 중복 조건, 정확한 별점과 최소 별점의 동시 지정, 유효하지 않은
페이지 번호가 있는 URL은 오류를 표시하며 전체 데이터로 조용히 대체하지 않는다. 필터를
다시 적용하거나 초기화해 복구할 수 있다. 알려지지 않은 URL 매개변수는 조회 API로 보내지
않는다. URL에는 적용한 제품명·필터·페이지가 들어가며 원문·키·작업 토큰은 저장하지 않는다.
URL 복원과 탐색만으로 AI를 호출하지 않는다.

snapshot JSON의 최상위 필드는 다음과 같다.

- `schema_version: 1`, `snapshot_id`, `generated_at`(UTC ISO), `demo`
- `filters`: 공통 `ReviewFilter`의 JSON 표현
- `statistics`: 공통 `ReviewStatistics`의 JSON 표현. enum 키는 문자열, 날짜는 ISO
- `page`: `number`, `size`, `total_items`, `total_pages`, `items`
- `insight_status`, `insight`: 사용 가능한 경우 공통 `InsightResult`, 그 외 null
- `chart_url`, `report_urls: {md, txt}`: 동일 원점의 상대 경로
- `export`: 선택 필드. `status`(`available`/`too_large`), `row_count`, `limit`(2000),
  `urls: {csv, jsonl}`. 한도 초과 시 urls는 빈 객체다.

리뷰 내보내기는 화면의 10건이 아닌 현재 조건 전체를 ID 오름차순으로 제공한다. 최대
2000건의 대상을 통계·페이지와 같은 읽기 트랜잭션에서 보관하며 다운로드 시 DB를 다시
읽지 않는다. 한도를 초과하면 데이터를 잘라 내보내지 않고 413을 반환한다. 기존 조회와
리포트는 계속 사용할 수 있다. 빈 결과는 CSV 헤더 또는 빈 JSONL 파일이다.

공통 `FileReviewExporter` 형식을 재사용한다. 외부 식별자인 `source_review_id`는
빈 값으로 제거하며 원본 파일 경로·Raw payload를 포함하지 않는다. CSV는 UTF-8 BOM과
수식으로 해석될 수 있는 문자열의 작은따옴표 접두어를 유지하고, JSONL은 원문을 보존한다.
응답은 첨부 파일이며 임시 파일은 요청 종료 시 정리한다. 필터 추가는 400, 만료는 410,
미지원 형식은 404다. 다운로드는 DB 쓰기나 AI 호출을 발생시키지 않는다.

리뷰 JSON은 `id`, `product_name`, `review_date`, `rating`, `review_text`, `analysis`다.
analysis는 null 또는 `sentiment`, `confidence`, `summary`, `keywords`, `model`,
`analyzed_at`이다. 별도의 분석 실패 원인이나 원본 파일 정보는 노출하지 않는다.

생성된 결과는 15분, 최대 16개까지 메모리에 보관한다. 이 범위에서 DB가 변경되어도
차트·리포트·원문 상세는 조회 당시 데이터로 일치한다. 만료·퇴출은 410이므로 화면에서
새로고침해야 한다. 미포함 리뷰/없는 경로는 404, 로컬 주소 위반은 403, 요청 오류는 400,
자료·의존성 오류는 503이다. 상세 경로나 예외 본문은 HTTP 응답에 노출하지 않는다.

인사이트 파일 v1은 `schema_version`, `source_sha256`, `selection_limit`, `review_ids`,
`insight` 필드를 가지며 v2는 `generation_profile`을 추가한다. 해시는 ID 오름차순 `ReviewDetail` 목록의 날짜 ISO 변환 후
JSON UTF-8 직렬화 결과를 대상으로 한다. `make_insight_artifact()`를 사용해 생성한다.
기존 DTO의 추가 필드나 직렬화 계약이 바뀌면 파일을 다시 준비해야 한다.

## 검증

```bash
python -m unittest discover -s tests -v
npm test --prefix web
```

Python 테스트는 임시 DB와 루프백 HTTP 포트를 사용한다. DB 무변경·쓰기 거부,
필터/페이지, 인사이트의 출처·범위, 만료, PNG/리포트/근거 연결, 정적 파일 공개 범위를
확인한다. 리뷰 내보내기는 페이지를 넘는 전체 대상, 조회 후 DB 변경, CSV 수식 방어,
외부 ID 제거, 한도·만료·Origin 검증, 실패 시 임시 파일 정리와 재시도를 확인한다.
JS 테스트는 URL 인코딩, 범위 표시, 빈 값과 다운로드 가능 상태 메시지를 확인한다.
실제 브라우저에서는 필터·리뷰 페이지/상세·원문 근거·다운로드와 좁은 화면을 검증한다.
