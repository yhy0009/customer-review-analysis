# 원본 리뷰 적재

`python main.py import`는 CSV/Excel을 읽어 설정된 SQLite의 Raw 저장소에 적재한다.
기본 CLI에 연결되어 있으며 별도 서비스 주입이나 AI API 키가 필요하지 않다.

## 입력과 실행

`requirements.txt`의 의존성을 설치한다. CSV는 UTF-8(BOM 포함)과 CP949를,
Excel은 `.xlsx`(openpyxl)와 `.xls`(xlrd)를 지원한다.
리뷰 본문 컬럼 `review_text` 또는 별칭(`리뷰 내용`, `리뷰`, `내용` 등)이 필요하다.
상품명·날짜·평점·외부 ID가 없거나 값이 유효하지 않아도 원본에 보존하며, 정제 단계에서 검증한다.
빈 파일·데이터 행 없는 파일·본문 컬럼 없는 파일은 입력 오류다.

예를 들어 다음 내용을 `data/reviews.csv`로 저장할 수 있다.

```csv
review_id,product_name,review_date,rating,review_text,channel
r1,이어폰,2026-09-18,5,배송이 빠르고 음질이 좋아요,웹
r2,이어폰,2026-09-18,2,배터리가 빨리 닳아요,앱
```

```bash
python main.py import --file data/reviews.csv
python main.py import --file /absolute/path/reviews.xlsx --policy skip
python main.py import --file data/reviews.csv --policy upsert
```

상대 입력 경로는 실행한 디렉터리와 관계없이 프로젝트 루트 기준이다.
DB 경로는 공통 설정의 `storage.database_path`를 사용한다.
외부 `review_id`는 `source_review_id`에, 추가 컬럼은 `raw_payload`에,
입력 파일의 절대 경로는 `source_file`에 저장한다. 내부 ID는 저장소가 부여한다.

## 중복 정책

`--policy`가 있으면 해당 값을, 없으면 `cleaning.duplicate_policy`를 사용한다(기본 `skip`).
중복 판정은 저장소의 기존 규칙을 따른다. 외부 ID가 있으면 정규화한 ID를 사용하고,
없으면 상품명·날짜·평점·본문의 정규화된 값으로 키를 만든다.

| 정책 | 중복 원본 처리 | 기존 정제·분석 결과 |
|---|---|---|
| `skip` | 원본 유지, skipped 증가 | 유지 |
| `upsert` | 내부 ID 유지, 원본 갱신, succeeded 증가 | 삭제하고 상태를 RAW로 초기화 |

같은 파일을 `upsert`로 다시 가져와도 해당 중복 리뷰의 정제·분석 결과가 초기화된다.
원본 보존이 목적이라면 `skip`을 사용한다.

## 결과와 오류

```text
processed=2 succeeded=2 skipped=0 failed=0 rejected=0
```

- `0`: 정상 적재 또는 중복 건너뛰기.
- `1`: 일부 행의 저장 실패. 성공 행은 저장되고 실패 행의 `ItemError`가 출력된다.
- `2`: 요청·설정 오류 또는 수집기에 필요한 패키지를 불러올 수 없음.
- `3`: 입력 파일 오류 또는 저장소 오류. DB 오류는 해당 저장 배치 전체를 취소한다.

파일 읽기를 완료한 뒤 저장을 요청하므로 입력 파일 오류로 기존 리뷰가 변경되지는 않는다.
런타임은 수집 전에 DB 연결을 열기 때문에 실패한 실행도 빈 DB 파일을 생성할 수 있다.
서비스는 저장소를 빌려 사용하며, 런타임이 성공·오류·사용자 중단 시 연결을 닫는다.

`import`는 Raw 적재까지만 수행한다. 정제 전에는 `list`, `show`, `stats`, `analyze`의
대상에 포함되지 않으므로 새 DB에 import한 뒤 `stats`가 0건을 표시하는 것은 정상이다.
`clean` 기본 CLI 연결은 후속 작업이다.

## 검증

```bash
python -m unittest tests.test_import_service tests.test_import_cli tests.test_pipeline_runtime -v
```

테스트는 임시 DB와 입력 파일로 CSV(BOM·CP949)·XLSX 적재, 중복 정책 우선순위,
upsert 후 정제·분석 무효화, 파일 오류·부분 저장 실패, 다른 디렉터리에서의 경로 해석을 검증한다.
도움말과 조회 명령은 pandas·AI SDK 없이 실행되며 실제 AI 호출은 발생하지 않는다.
