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
DB를 지정하지 않으면 `data/app_database.db`를 사용하며 서버는 `.env`나 config를 읽지 않는다.
파일이 없거나 구형 스키마면 시작에 실패한다. 빈 DB 생성·자동 마이그레이션은 하지 않는다.
인사이트 파일 없이도 통계·차트·리뷰·통계 리포트는 이용할 수 있다.

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
하나의 서버에 인사이트 파일 하나를 연결하며 다음 상태를 구분한다.

| 상태 | 의미 |
|---|---|
| `available` | 필터·선택 리뷰·원문 및 분석 해시가 같고 모든 인용이 원문에 존재 |
| `missing` | 연결한 인사이트 파일 없음 |
| `scope_mismatch` | 화면 필터와 인사이트 생성 필터가 다름 |
| `stale` | 원문·분석·선택 대상 변경 또는 인용 검증 실패 |

`available`만 화면과 다운로드 리포트에 포함한다. 통계는 필터 전체 대상, 인사이트는
선택 한도 이내 분석 완료 리뷰 대상이며 화면에 두 수치를 구분한다. 한도 밖 데이터가
추가되어도 선택 대상이 같으면 기존 인사이트를 사용할 수 있다. 원문 검증은 AI 해석의
의미적 정확성 검증과 다르며 기존 품질 평가 체계는 그대로 유지한다.

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

snapshot 쿼리는 `product_name`(부분 일치), `date_from`, `date_to`(YYYY-MM-DD),
`sentiment`(positive/neutral/negative), `rating`, `rating_min`, `page`를 지원한다.
UI는 제품·기간·감정 필터를 제공한다. 중복 또는 알 수 없는 조건은 400이다.
리뷰 페이지 크기는 10, 정렬은 ID 내림차순이다.

snapshot JSON의 최상위 필드는 다음과 같다.

- `schema_version: 1`, `snapshot_id`, `generated_at`(UTC ISO), `demo`
- `filters`: 공통 `ReviewFilter`의 JSON 표현
- `statistics`: 공통 `ReviewStatistics`의 JSON 표현. enum 키는 문자열, 날짜는 ISO
- `page`: `number`, `size`, `total_items`, `total_pages`, `items`
- `insight_status`, `insight`: 사용 가능한 경우 공통 `InsightResult`, 그 외 null
- `chart_url`, `report_urls: {md, txt}`: 동일 원점의 상대 경로

리뷰 JSON은 `id`, `product_name`, `review_date`, `rating`, `review_text`, `analysis`다.
analysis는 null 또는 `sentiment`, `confidence`, `summary`, `keywords`, `model`,
`analyzed_at`이다. 별도의 분석 실패 원인이나 원본 파일 정보는 노출하지 않는다.

생성된 결과는 15분, 최대 16개까지 메모리에 보관한다. 이 범위에서 DB가 변경되어도
차트·리포트·원문 상세는 조회 당시 데이터로 일치한다. 만료·퇴출은 410이므로 화면에서
새로고침해야 한다. 미포함 리뷰/없는 경로는 404, 로컬 주소 위반은 403, 요청 오류는 400,
자료·의존성 오류는 503이다. 상세 경로나 예외 본문은 HTTP 응답에 노출하지 않는다.

인사이트 파일 v1은 `schema_version`, `source_sha256`, `selection_limit`, `review_ids`,
`insight` 필드를 갖는다. 해시는 ID 오름차순 `ReviewDetail` 목록의 날짜 ISO 변환 후
JSON UTF-8 직렬화 결과를 대상으로 한다. `make_insight_artifact()`를 사용해 생성한다.
기존 DTO의 추가 필드나 직렬화 계약이 바뀌면 파일을 다시 준비해야 한다.

## 검증

```bash
python -m unittest discover -s tests -v
npm test --prefix web
```

Python 테스트는 임시 DB와 루프백 HTTP 포트를 사용한다. DB 무변경·쓰기 거부,
필터/페이지, 인사이트의 출처·범위, 만료, PNG/리포트/근거 연결, 정적 파일 공개 범위를
확인한다. JS 테스트는 URL 인코딩, 범위 표시, 빈 값과 상태 메시지를 확인한다.
실제 브라우저에서는 필터·리뷰 페이지/상세·원문 근거·다운로드와 좁은 화면을 검증한다.
