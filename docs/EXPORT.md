# 리뷰 내보내기

`export`는 조건에 맞는 모든 정제 리뷰와 최신 분석 결과를 CSV, JSONL 또는 Excel로
저장한다. 기본 정렬은 내부 ID 오름차순이며 화면 조회의 페이지 크기에 제한되지 않는다.
분석되지 않았거나 분석에 실패한 리뷰도 감정 필터를 지정하지 않으면 포함한다.

## 실행

```bash
python main.py export --format csv --output output/reviews.csv
python main.py export --format jsonl --output output/negative.jsonl --sentiment negative
python main.py export --format excel --output output/reviews.xlsx --rating-min 3
python main.py export --format csv --output output/period.csv --product 이어폰 --date-from 2026-09-01 --date-to 2026-09-30
python main.py export --format csv --output output/reviews.csv --force
```

`--format`과 `--output`은 필수다. 파일명에 확장자가 있으면 각각 `.csv`, `.jsonl`, `.xlsx`와
일치해야 한다. 확장자가 없는 파일명도 허용하며 지정한 경로 그대로 저장한다.
상대 경로는 프로젝트 루트 기준이며 상위 폴더는 자동 생성한다.
Excel에 필요한 `openpyxl`은 `requirements.txt`에 포함돼 있다. CSV/JSONL은 표준
라이브러리만 사용하며 내보내기는 AI API를 호출하지 않는다.

## 공통 필드

세 포맷은 다음 순서의 동일한 필드를 사용한다.

```text
id, source_review_id, product_name, review_date, rating, review_text, cleaned_at,
sentiment, confidence, summary, keywords, analyzed_at, provider, model, prompt_version
```

`review_text`는 저장된 정제 본문이다. 날짜는 `YYYY-MM-DD`, 정제·분석 시각은 UTC의
ISO 8601 `Z` 문자열로 표현한다. 한글·이모지·쉼표·줄바꿈을 보존한다.

| 구분 | CSV | JSONL | Excel |
|---|---|---|---|
| 형식 | UTF-8 BOM, 헤더 행 | UTF-8, 한 줄에 JSON 객체 | `.xlsx`, Reviews 시트 |
| 값 없음 | 빈 필드 | `null` | 빈 셀 |
| 키워드 | JSON 배열 문자열 | JSON 배열 | JSON 배열 문자열 |
| 분석 결과 없음 | 분석 필드 모두 빈 값 | 분석 필드 모두 `null` | 분석 필드 모두 빈 셀 |
| 조회 0건 | 헤더만 있는 파일 | 빈 파일 | 헤더만 있는 시트 |

CSV는 Excel에서 수식으로 해석될 수 있는 문자열(앞쪽 공백을 제외한 첫 문자가
`=`, `+`, `-`, `@`)에 작은따옴표를 붙인다. Excel은 문자열 셀 타입을 명시해 원래 문자열을
보존하고, JSONL은 원래 문자열을 그대로 저장한다. 숫자 필드는 숫자로 출력한다.
Excel의 셀 문자열·행 수 제한을 넘으면 데이터를 잘라 저장하지 않고 오류를 반환한다.

## 파일과 저장소 보호

- 기존 파일은 `--force` 없이 덮어쓰지 않는다. 동시 실행 중 먼저 생성된 파일도 보존한다.
- 같은 폴더의 임시 파일에 모두 기록한 뒤 최종 파일로 교체한다. 생성 실패 시 이전 파일은
  유지하고 임시 파일을 정리한다. `--force`도 완성된 결과만 교체한다.
- 출력 파일 자체가 심볼릭 링크인 경우 거부한다. 실제 출력 파일 경로를 지정한다.
- 사용 중인 SQLite DB와 journal/WAL/SHM 파일에는 내보낼 수 없다.
- CLI는 하나의 SQLite 읽기 트랜잭션에서 모든 페이지를 읽는다. 다른 작업이 리뷰를
  수정해도 한 파일 안에 서로 다른 시점의 페이지가 섞이지 않는다. 기본 journal 모드에서는
  내보내기가 끝날 때까지 다른 연결의 쓰기 완료가 대기할 수 있다.
- 현재 서비스는 선택된 모든 리뷰를 메모리에 모아 기존 `ReviewExporter` 계약에 전달한다.

## 종료 코드와 구성

성공하면 내보낸 건수·포맷·절대 경로를 stdout으로 출력하고 종료 코드 0을 반환한다.
빈 결과도 성공이다. 잘못된 날짜·포맷·확장자는 코드 2, 저장소 또는 파일 생성 오류는
코드 3으로 처리한다. 오류는 stderr로 안내하며 리뷰 본문을 진단 로그에 출력하지 않는다.

`ExportService`는 공통 Repository의 페이지 조회를 재사용하고 `FileReviewExporter`에
결과를 전달한다. 필터는 서비스가, 인코딩·파일 생성은 exporter가 담당한다. 기존
`ExportRequest`, `ExportResult`, `ReviewExporter` 계약은 유지한다.

```bash
python -m unittest discover -s tests -p 'test_export*.py' -v
python -m unittest discover -s tests -q
```

테스트는 파일을 다시 읽어 값과 타입을 확인하고, 덮어쓰기 거부·강제 교체·실패 복구,
여러 페이지의 전체 내보내기, 실제 CLI 실행과 동시 DB 갱신 시 일관성을 검증한다.
