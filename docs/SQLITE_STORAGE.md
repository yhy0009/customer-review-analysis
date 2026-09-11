# SQLite 저장소 안내

`src/sqlite_repository.py`의 `SQLiteReviewRepository`가 유일한 SQLite 구현이다.
`src/storage.py`는 `ReviewRepository` Protocol과 기존 import를 위한 클래스 이름만 제공한다.
두 경로는 같은 클래스와 스키마를 사용하며, 아래 import는 모두 유효하다.

```python
from src.storage import SQLiteReviewRepository
# 또는 from src.sqlite_repository import SQLiteReviewRepository
# 이전 이름: from src.sqlite_repository import SqliteReviewRepository

with SQLiteReviewRepository(database_path="data/app_database.db") as repository:
    statistics = repository.get_statistics()
```

경로를 첫 번째 인자로 넘겨도 된다. 키워드 인자는 `database_path`다.
상대 경로는 현재 작업 디렉터리가 아닌 프로젝트 루트 기준이다. 메모리 DB는 지원하지 않는다.
`SQLiteReviewRepository.from_config(config)`로 공통 설정의 `storage.database_path`를 사용할 수 있다.

## 저장과 통계 규칙

- Raw 저장소가 내부 ID를 부여한다. Clean은 조회한 Raw ID를 그대로 사용하며,
  원본이 없는 Clean ID는 행 단위 오류다. Clean 전용 ID는 생성하지 않는다.
- Raw 중복 저장의 `skip`은 기존 값과 분석 결과를 유지한다. `upsert`는 Raw ID와
  생성 시각을 유지하고 Clean·Analysis를 삭제하여 다시 정제할 수 있게 한다.
- Clean `upsert`는 제품·날짜·별점·본문이 변경되면 분석을 무효화한다.
  동일 내용의 재저장은 분석을 유지한다.
- 분석 결과와 Raw/Clean 상태를 같은 트랜잭션으로 저장한다. 분석 실패를 기록하면
  이전 결과를 제거하고, 재시도가 성공하면 실패 상태와 오류 메시지를 해제한다.
- Raw/Clean 배치의 잘못된 행은 savepoint로 격리한다. DB 장애는 해당 저장 배치 전체를
  롤백한다. AI 배치는 리뷰별로 저장하므로 뒤 항목의 DB 장애 전에 저장한 성공 결과는 남는다.
- 통계의 전체 수는 Clean 기준이며 분석 완료·실패·미분석 수의 합이다.
  `fetch_unanalyzed_reviews()`는 재시도용으로 실패도 포함하지만,
  `get_statistics().unanalyzed_reviews`는 실패를 제외한다.
- 별점×감정 행렬은 1~5점과 모든 감정의 0건 구간을 포함한다. 분석된 날짜의 집계도
  0건 감정을 포함한다. 평균 별점은 원래 계산값을 반환하고 출력 단계에서 반올림한다.
  상위 키워드는 리뷰당 한 번 세며, 건수 내림차순·동률 시 키워드 오름차순으로 정렬한다.

통합하면서 기존 별도 구현의 0건 통계 구간, 평균 정밀도, 키워드 동률 순서를 옮겼다.
Raw/Clean 독립 ID 발급, 작업 디렉터리 기준 경로, 버전 없는 스키마 초기화는
현행 인터페이스 명세와 맞지 않아 사용하지 않는다.

## 기존 DB 처리

import 통합은 DB 변환이 아니다. 기준 스키마는 `PRAGMA user_version = 1`이며,
기존 `storage.py`가 만든 버전 0 DB에는 Clean의 별도 ID·`raw_id`·`dedupe_key`가 있다.
Raw 필드 직렬화와 분석 메타데이터 컬럼도 v1과 다르다.
버전 0 DB나 지원하지 않는 스키마는 `StorageError`로 거부하고 자동 수정하지 않는다.
버전 숫자만 1로 바꾸면 데이터가 변환되는 것이 아니므로 그렇게 처리해서는 안 된다.

기존 DB를 점검할 때는 저장소 생성자 대신 SQLite 읽기 전용 연결로 확인할 수 있다.
아래 코드는 데이터 본문을 읽거나 스키마를 변경하지 않는다.

```python
import sqlite3
from pathlib import Path

path = Path("data/app_database.db").resolve()  # 프로젝트 루트에서 실행
with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
    print("version:", connection.execute("PRAGMA user_version").fetchone()[0])
    for table in ("raw_reviews", "clean_reviews", "analysis_results"):
        print(table, connection.execute(f"PRAGMA table_info({table})").fetchall())
```

보존할 구형 데이터가 있다면 별도 변환 작업으로 처리한다.

1. 쓰는 프로세스를 중지하고 SQLite backup API로 일관된 백업을 만든다.
2. 원본을 유지한 채 별도의 v1 DB로 옮긴다. 이전 Clean ID → Raw ID 대응표를 만들고
   분석 결과의 `review_id`도 함께 변환한다. 원본 없는 Clean, 여러 Clean이 같은 Raw를
   참조하는 경우는 임의 병합하지 않고 처리 방침을 정한다.
3. Raw JSON 직렬화·중복 키·시각·상태를 변환하고 충돌이나 변환 불가 행을 보고한다.
4. 원본 대비 건수·ID 대응·분석 결과·통계와 `PRAGMA integrity_check` 및
   `PRAGMA foreign_key_check`를 검증한 뒤 DB 경로를 전환한다.

이번 통합은 자동 마이그레이션을 제공하지 않는다. 구형 스키마와 데이터가 초기화 거부 후에도
바이트 단위로 보존되는지는 테스트한다. 테스트용 구형 스키마는
`tests/fixtures/sqlite_legacy_v0.sql`에 보관하며 새 DB 생성에는 사용하지 않는다.

## 검증

프로젝트 의존성을 설치한 환경에서 다음을 실행한다. 모든 테스트는 임시 DB를 사용하고
실제 API를 호출하지 않는다.

```bash
python -m unittest discover -s tests -v
```

`test_storage.py`는 기존 import 경로의 Raw·스키마·롤백 계약을,
`test_repository_integration.py`는 기준 구현의 Clean·Analysis·조회 계약을 검증한다.
`test_sqlite_consistency.py`는 같은 파일을 두 경로의 독립 연결로 열어 분석 서비스의
실패·재시도·강제 재분석·통계를 검증한다. CSV/Excel → Raw → Clean → 분석 저장은
`test_pipeline_integration.py`에서 확인한다.
