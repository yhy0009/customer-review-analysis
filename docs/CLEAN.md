# 리뷰 정제

`python main.py clean`은 저장된 Raw 리뷰를 검증·정규화해 Clean 저장소에 적재한다.
정제된 리뷰는 조회·통계·내보내기와 AI 분석 대상에 포함된다. 정제 자체에는 AI API 키나
pandas·matplotlib·OpenAI SDK가 필요하지 않다.

## 실행

```bash
python main.py import --file data/reviews.csv
python main.py clean
python main.py clean --policy skip --min-length 3
python main.py stats
python main.py list
```

`--policy`, `--min-length`를 생략하면 `cleaning.duplicate_policy`와
`cleaning.min_review_length`를 사용한다(기본 `skip`, 3).
설정·DB 상대 경로는 공통 규칙에 따라 프로젝트 루트 기준이다.

본문은 필수이며 공백 정규화와 최소 길이 검증을 적용한다. 제품명·작성일·별점은 선택 항목이다.
열이 없거나 셀이 비어 있으면 `None`/SQL `NULL`로 보존한다. 값이 있는 날짜는 유효한 날짜여야
하고 별점은 1~5의 정수여야 한다. 누락값을 임의 제품명·실행일·0점으로 채우지 않는다.
원본 값은 그대로 보존하고 원본의 내부 ID를 Clean에서도 사용한다.

```csv
review_text
본문만 있는 리뷰도 저장하고 분석할 수 있습니다
```

예전에 선택 항목 누락으로 REJECTED가 된 원본도 `clean`으로 재시도할 수 있다.
CLI에는 `제품명 없음`·`날짜 없음`·`N/A`, CSV/Excel에는 빈 셀, JSONL에는 `null`로 표시한다.
평균 별점은 별점이 있는 정제 리뷰만, 날짜·별점 차트는 해당 값이 있는 분석 리뷰만 사용한다.
감정 비율과 전체 건수에는 선택 항목이 없는 리뷰도 포함된다. 필터를 지정하면 그 값이 없는
리뷰는 해당 조건에 포함되지 않고, 날짜·별점 정렬 시 누락값은 항상 마지막이다.

기존 v1 DB는 쓰기 연결 시 v2로 원자적으로 이전하며 리뷰 ID·상태·분석 결과를 보존한다.
읽기 전용 연결은 v1도 변경 없이 조회할 수 있다([SQLite 저장소 안내](SQLITE_STORAGE.md)).

## 재실행과 중복 정책

모든 저장 원본을 ID 순서로 확인한다. 이전 정제에서 제외된 REJECTED도 다시 검사한다.

| 정책 | 이미 Clean이 있는 리뷰 | 아직 Clean이 없는 리뷰 |
|---|---|---|
| `skip` | 정제 전에 건너뛰고 기존 정제·분석 결과 유지 | 현재 기준으로 검사·저장 |
| `upsert` | 원본을 현재 기준으로 다시 검사·갱신 | 현재 기준으로 검사·저장 |

`upsert`로 저장한 정제 내용이 같으면 분석 결과를 유지한다. 상품명·날짜·평점·본문이
달라지면 기존 분석을 무효화한다. 더 엄격한 기준으로 다시 검사해 제외되면 기존 Clean과
Analysis를 삭제하고 원본 상태를 REJECTED로 바꾼다.

```bash
# 기존 정제 결과까지 최소 길이 10자로 다시 검증
python main.py clean --policy upsert --min-length 10

# 길이 기준 때문에 제외됐던 원본을 완화된 기준으로 다시 검사
python main.py clean --policy skip --min-length 2
```

잘못된 날짜·평점 등 원본 값을 수정하려면 입력 파일을 수정하고 `import --policy upsert`로
다시 적재한 뒤 정제한다. 외부 ID로 같은 원본을 갱신하면 내부 ID를 유지한다.

## 결과와 상태

```text
processed=3 succeeded=1 skipped=0 failed=0 rejected=2
  [INVALID_REVIEW_DATE] item=1: review_date is invalid
  [INVALID_RATING] item=3: rating must be an integer between 1 and 5
```

- `succeeded`: 실제 저장된 Clean 리뷰 수. 서비스의 `reviews`도 해당 항목만 포함한다.
- `skipped`: 기존 Clean을 보존하며 건너뛴 수.
- `rejected`: 정제 규칙을 통과하지 못해 REJECTED로 기록한 수.
- `failed`: 예기치 않은 정제 오류 또는 행 단위 저장 오류 수. REJECTED로 바꾸지 않는다.
- `item`: CSV 행 번호가 아닌 저장소의 원본 내부 ID.

제외 사유는 명령 결과에 표시한다. 현재 DB에는 제외 상태만 기록하고 사유 이력은 저장하지 않는다.
실패·제외가 있으면 종료 코드 1, 정상·전체 건너뛰기·빈 DB는 0이다.
잘못된 요청·설정은 2, 저장소 장애는 3이다.

원본 한 건마다 정제와 결과 저장을 수행한다. 제외 상태 변경과 기존 Clean·Analysis 삭제는
하나의 트랜잭션으로 처리한다. 저장소 장애가 나면 명령을 중단하고 오류를 반환하며 앞선 항목의
커밋은 유지한다. `skip`으로 재실행하면 이미 저장된 Clean을 건너뛰고 나머지를 처리한다.
명령이 끝나거나 오류·사용자 중단이 발생하면 런타임이 DB 연결을 닫는다.

정제 결과 저장·제외 처리 전에 처음 읽은 원본과 현재 원본을 같은 쓰기 트랜잭션에서
비교한다. 정제 도중 다른 `import --policy upsert` 실행으로 원본이 바뀌었다면 이전
결과를 저장하거나 기존 Clean·Analysis를 삭제하지 않는다. 해당 항목은
`RAW_REVIEW_CHANGED` 오류(`retryable=True`)와 `failed`로 집계하며, 나머지 항목은 계속
처리한다. 종료 코드는 1이며 `clean`을 다시 실행하면 최신 원본을 처리한다.

## 검증

```bash
python -m unittest tests.test_clean_rejection_storage tests.test_clean_service tests.test_clean_cli -v
python -m unittest discover -s tests -q
```

임시 DB에서 제외 상태의 원자성, 읽기 전용 보호, 부분 실패 집계, 분석 유지·무효화,
기준 변경 후 재정제, 파일 재적재 후 복구를 검증한다. 실제 CLI로 import부터 정제·조회·통계·
내보내기까지 실행하고 선택적 패키지와 API 키 없이 정제가 동작하는지도 확인한다.
